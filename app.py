"""Tkinter desktop app to turn a YouTube transcript into a Notion page."""

from __future__ import annotations

import json
import os
import re
import threading
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List
from urllib.parse import parse_qs, urlparse

from google import genai
import requests
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError
from openai import OpenAI
from tkinter import Tk, StringVar, messagebox, ttk
import tkinter as tk
from tkinter import scrolledtext
from youtube_transcript_api import YouTubeTranscriptApi

CONFIG_PATH = Path("config.json")
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
STALE_DEFAULT_MODELS = {"gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro"}
GEMINI_PROMPT = (
    "You are a technical writer creating a Notion wiki page from a YouTube transcript. "
    "Produce a reference-style entry covering key topics, architectures, workflows, "
    "and implementation details.\n\n"
    "## Structure\n"
    "- Use # / ## / ### for section hierarchy. Do NOT start with a top-level heading — "
    "open with a brief introductory paragraph (no heading), then begin sections with #.\n"
    "- Keep the intro paragraph high-level. Save specifics, results, and technical detail "
    "for the body sections.\n\n"
    "## Writing style\n"
    "- Full paragraph prose like a Wikipedia article. "
    "Bullet points only for true lists (benchmark results, components, etc.) — never to "
    "describe concepts or explain ideas.\n"
    "- Assume the reader has general technical knowledge. When a known concept is mentioned "
    "(e.g. residual connections, attention, backpropagation), reference it naturally "
    "without explaining it — *unless* the video itself spends time explaining the motivation "
    "behind it (e.g. why residual connections were needed for vanishing gradients). "
    "In that case, summarize the problem being solved as background.\n"
    "- Capture the substance and structure of what the video presents, not the speaker's "
    "narrative flow.\n"
    "- Include implementation depth: describe mechanisms, algorithms, and design choices "
    "so the reader understands how things actually work, not just what they are.\n\n"
    "## Headings\n"
    "- Write headings as raw topic names — no framing labels. Never prefix with "
    "\"Background:\", \"Introduction:\", \"Overview:\", \"Conclusion:\", "
    "\"Key Concepts:\", \"Details:\", or similar.\n"
    "- When the video discusses a well-known external concept (ResNets, transformers, etc.), "
    "don't make the concept the heading itself. Instead describe what the video says about it, "
    "e.g. \"## Problems with ResNets\" or \"## Attention in this context\".\n\n"
    "## Formatting\n"
    "- Code-like content: fenced code blocks.\n"
    "- Display math: `$$...$$` (LaTeX, one expression per block).\n"
    "- Inline math: `$...$` (LaTeX, within a sentence).\n"
    "- Use LaTeX for equations, formulas, and any mathematical notation present in the video.\n"
    "- Image placeholders: `[Image: brief description of what the image should show]` — use these at natural points in the text where a diagram, screenshot, or illustration would aid understanding. The user will replace them with actual images later.\n"
    "- Output only markdown.\n\n"
    "Transcript:\n{transcript}"
)
TRANSCRIPT_CHAR_LIMITS = {"gemini": 150_000, "deepseek": 200_000}


def truncate_transcript(transcript: str, limit: int) -> tuple[str, bool]:
    """Truncate transcript to *limit* characters. Returns (text, was_truncated)."""
    if len(transcript) <= limit:
        return transcript, False
    return transcript[:limit] + "\n\n...[truncated]", True


class AppError(Exception):
    """Base application exception."""


class TranscriptError(AppError):
    """Raised when transcript retrieval fails."""


DEFAULT_DEEPSEEK_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"]

@dataclass
class AppConfig:
    """Persistent user configuration."""

    gemini_api_key: str = ""
    deepseek_api_key: str = ""
    notion_api_key: str = ""
    llm_provider: str = "gemini"  # "gemini" or "deepseek"
    gemini_model: str = DEFAULT_MODELS[0]
    gemini_models: List[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    deepseek_model: str = DEFAULT_DEEPSEEK_MODELS[0]
    deepseek_models: List[str] = field(default_factory=lambda: list(DEFAULT_DEEPSEEK_MODELS))
    notion_page_id: str = ""
    replace_existing_content: bool = False


def split_text_chunks(text: str, chunk_size: int = 1800) -> List[str]:
    """Split long text into Notion-safe rich_text chunks."""
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# Regex for inline markdown patterns — order matters so ** beats * and $$ beats $.
_INLINE_PATTERN = re.compile(
    r"\*\*(.+?)\*\*"         # 1 – bold
    r"|\*(.+?)\*"            # 2 – italic
    r"|`(.+?)`"              # 3 – inline code
    r"|\$\$(.+?)\$\$"        # 4 – display math inline (edge case)
    r"|\$(.+?)\$"            # 5 – inline math
    r"|~~(.+?)~~"            # 6 – strikethrough
)


def parse_inline_markdown(text: str) -> List[dict]:
    """Parse inline markdown into Notion rich text objects with annotations.

    Supports **bold**, *italic*, ``code``, $math$, ~~strikethrough~~.
    """
    if not text:
        return [{"type": "text", "text": {"content": " "}}]

    results: List[dict] = []
    last_end = 0

    for match in _INLINE_PATTERN.finditer(text):
        # Plain text segment before this match
        if match.start() > last_end:
            for chunk in split_text_chunks(text[last_end : match.start()]):
                results.append({"type": "text", "text": {"content": chunk}})

        # Determine which group matched and build the rich-text object
        if match.group(1):   # bold
            ann = {"bold": True}
            content = match.group(1)
        elif match.group(2):  # italic
            ann = {"italic": True}
            content = match.group(2)
        elif match.group(3):  # inline code
            ann = {"code": True}
            content = match.group(3)
        elif match.group(4):  # display math inline ($$…$$ mid-line)
            results.append(
                {"type": "equation", "equation": {"expression": match.group(4).strip()}}
            )
            last_end = match.end()
            continue
        elif match.group(5):  # inline math ($…$)
            results.append(
                {"type": "equation", "equation": {"expression": match.group(5).strip()}}
            )
            last_end = match.end()
            continue
        elif match.group(6):  # strikethrough
            ann = {"strikethrough": True}
            content = match.group(6)

        for chunk in split_text_chunks(content):
            results.append(
                {"type": "text", "text": {"content": chunk}, "annotations": ann}
            )
        last_end = match.end()

    # Trailing plain text
    if last_end < len(text):
        for chunk in split_text_chunks(text[last_end:]):
            results.append({"type": "text", "text": {"content": chunk}})

    return results if results else [{"type": "text", "text": {"content": text.strip() or " "}}]


def text_rich_objects(text: str) -> List[dict]:
    """Build plain (unannotated) Notion rich text objects — used for code blocks."""
    return [
        {"type": "text", "text": {"content": chunk}}
        for chunk in split_text_chunks(text.strip() or " ")
    ]


def paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": parse_inline_markdown(text)},
    }


def heading_block(level: int, text: str) -> dict:
    block_type = {1: "heading_1", 2: "heading_2", 3: "heading_3"}.get(level, "heading_2")
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": parse_inline_markdown(text)},
    }


def bullet_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": parse_inline_markdown(text)},
    }


def code_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": text_rich_objects(text),
            "language": "plain text",
        },
    }


def equation_block(expression: str) -> dict:
    """Notion equation block for display math."""
    return {
        "object": "block",
        "type": "equation",
        "equation": {"expression": expression.strip()},
    }


def markdown_to_notion_blocks(markdown_text: str) -> List[dict]:
    """Convert simplified markdown into Notion block payloads."""
    blocks: List[dict] = []
    paragraph_lines: List[str] = []
    code_lines: List[str] = []
    math_lines: List[str] = []
    in_code_block = False
    in_math_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            blocks.append(paragraph_block("\n".join(paragraph_lines).strip()))
            paragraph_lines = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            if in_code_block:
                blocks.append(code_block("\n".join(code_lines).strip()))
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if stripped == "$$":
            flush_paragraph()
            if in_math_block:
                blocks.append(equation_block("\n".join(math_lines).strip()))
                math_lines = []
                in_math_block = False
            else:
                in_math_block = True
            continue

        if in_math_block:
            math_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            continue

        if stripped.startswith("# "):
            flush_paragraph()
            blocks.append(heading_block(1, stripped[2:].strip()))
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            blocks.append(heading_block(2, stripped[3:].strip()))
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            blocks.append(heading_block(3, stripped[4:].strip()))
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            blocks.append(bullet_block(stripped[2:].strip()))
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    if in_code_block and code_lines:
        blocks.append(code_block("\n".join(code_lines).strip()))
    if in_math_block and math_lines:
        blocks.append(equation_block("\n".join(math_lines).strip()))

    return blocks


def normalize_page_id(page_or_url: str) -> str:
    """Extract and normalize a Notion page UUID from raw ID or URL."""
    candidate = page_or_url.strip().split("?")[0].rstrip("/")
    hex_match = re.search(r"([0-9a-fA-F]{32})", candidate)
    if hex_match:
        compact = hex_match.group(1).lower()
        return (
            f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
            f"{compact[16:20]}-{compact[20:32]}"
        )

    dashed_match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        candidate,
    )
    if dashed_match:
        return dashed_match.group(1).lower()

    raise AppError("Invalid Notion page ID/URL format.")


def extract_video_id(youtube_url: str) -> str:
    """Extract YouTube video ID from URL."""
    parsed = urlparse(youtube_url.strip())
    host = parsed.netloc.lower()

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.lstrip("/")
        if video_id:
            return video_id

    if host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return video_id

        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
            return path_parts[1]

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", youtube_url.strip()):
        return youtube_url.strip()

    raise AppError("Could not extract a valid YouTube video ID from the provided URL.")


def get_transcript(video_id: str) -> tuple[str, str]:
    """Get transcript using youtube-transcript-api."""
    try:
        api = YouTubeTranscriptApi()
        transcript_data = api.fetch(video_id)
        transcript = "\n".join(item.text for item in transcript_data).strip()
        if not transcript:
            raise TranscriptError("Transcript returned empty text.")
        return transcript, "youtube-transcript-api"
    except TranscriptError:
        raise
    except Exception as exc:
        raise TranscriptError(f"Transcript retrieval failed: {exc}") from exc


def summarize(
    transcript: str,
    *,
    provider: str,
    gemini_key: str = "",
    deepseek_key: str = "",
    gemini_model: str = "",
    deepseek_model: str = "",
) -> str:
    """Route summarization to the selected LLM provider."""
    if provider == "deepseek":
        return _summarize_deepseek(transcript, deepseek_key, deepseek_model)
    return _summarize_gemini(transcript, gemini_key, gemini_model)


def _summarize_gemini(transcript: str, gemini_key: str, model_name: str) -> str:
    """Generate markdown page content from transcript using Gemini."""
    if not gemini_key:
        raise AppError("Gemini API key is empty.")

    client = genai.Client(api_key=gemini_key)
    prompt = GEMINI_PROMPT.format(transcript=transcript)

    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
    except Exception as exc:
        raise AppError(f"Gemini summarization failed: {exc}") from exc

    summary = (getattr(response, "text", None) or "").strip()
    if not summary:
        raise AppError("Gemini returned empty page content.")
    return summary


def _summarize_deepseek(transcript: str, deepseek_key: str, model_name: str) -> str:
    """Generate markdown page content from transcript using DeepSeek."""
    if not deepseek_key:
        raise AppError("DeepSeek API key is empty.")

    client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com/v1")
    prompt = GEMINI_PROMPT.format(transcript=transcript)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a technical writer. Output only markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
    except Exception as exc:
        raise AppError(f"DeepSeek summarization failed: {exc}") from exc

    summary = (response.choices[0].message.content or "").strip()
    if not summary:
        raise AppError("DeepSeek returned empty page content.")
    return summary


def fetch_gemini_models(gemini_key: str) -> List[str]:
    """Fetch available Gemini text-generation models for a given API key."""
    key = gemini_key.strip()
    if not key:
        raise AppError("Please enter a Gemini API key before refreshing models.")

    client = genai.Client(api_key=key)
    model_names: List[str] = []
    for model in client.models.list():
        if "generateContent" not in (model.supported_actions or []):
            continue

        name = (model.name or "").removeprefix("models/").strip()
        if name.startswith("gemini"):
            model_names.append(name)

    available = sorted(set(model_names))
    if not available:
        raise AppError("No Gemini generateContent models were returned for this API key.")
    return available


def clear_notion_page(notion: Client, page_id: str) -> None:
    """Archive existing top-level blocks for the target page."""
    start_cursor = None
    while True:
        result = notion.blocks.children.list(block_id=page_id, start_cursor=start_cursor)
        for child in result.get("results", []):
            notion.blocks.update(block_id=child["id"], archived=True)

        if not result.get("has_more"):
            break
        start_cursor = result.get("next_cursor")


def append_blocks_to_notion_page(notion: Client, page_id: str, blocks: Iterable[dict]) -> None:
    """Append blocks in Notion API batch-safe chunks."""
    batch: List[dict] = []
    for block in blocks:
        batch.append(block)
        if len(batch) == 100:
            notion.blocks.children.append(block_id=page_id, children=batch)
            batch = []

    if batch:
        notion.blocks.children.append(block_id=page_id, children=batch)


class YouTubeToNotionApp(Tk):
    """Main Tkinter UI application."""

    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube → LLM → Notion")
        self.geometry("900x750")

        load_dotenv(override=False)
        self.config_data = self.load_config()
        self._build_ui()
        self.apply_config_to_ui(self.config_data)

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)

        self.youtube_url_var = StringVar()
        self.provider_var = StringVar(value="gemini")
        self.gemini_model_var = StringVar()
        self.gemini_key_var = StringVar()
        self.deepseek_key_var = StringVar()
        self.notion_key_var = StringVar()
        self.notion_page_var = StringVar()
        self.replace_var = tk.BooleanVar(value=False)

        row = 0

        self._add_label_entry("YouTube URL", self.youtube_url_var, row)
        row += 1

        # --- LLM Provider dropdown ---
        ttk.Label(self, text="LLM Provider").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        provider_frame = ttk.Frame(self)
        provider_frame.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        provider_frame.columnconfigure(0, weight=1)
        self.provider_combo = ttk.Combobox(
            provider_frame, textvariable=self.provider_var,
            values=["gemini", "deepseek"], state="readonly",
        )
        self.provider_combo.grid(row=0, column=0, sticky="ew")
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        row += 1

        # --- Model dropdown ---
        ttk.Label(self, text="Model").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        model_frame = ttk.Frame(self)
        model_frame.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        model_frame.columnconfigure(0, weight=1)
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.gemini_model_var, state="readonly")
        self.model_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(model_frame, text="Refresh", command=self._refresh_models).grid(
            row=0, column=1, padx=(6, 0)
        )
        row += 1

        # --- Gemini API key row ---
        self._gemini_key_row: List[tk.Widget] = []
        gk_lbl = ttk.Label(self, text="Gemini API key")
        gk_lbl.grid(row=row, column=0, sticky="w", padx=8, pady=6)
        gk_entry = ttk.Entry(self, textvariable=self.gemini_key_var, show="*")
        gk_entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        self._gemini_key_row = [gk_lbl, gk_entry]

        # --- DeepSeek API key row (same grid row; hidden by default) ---
        self._deepseek_key_row: List[tk.Widget] = []
        dk_lbl = ttk.Label(self, text="DeepSeek API key")
        dk_lbl.grid(row=row, column=0, sticky="w", padx=8, pady=6)
        dk_entry = ttk.Entry(self, textvariable=self.deepseek_key_var, show="*")
        dk_entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        dk_lbl.grid_remove()
        dk_entry.grid_remove()
        self._deepseek_key_row = [dk_lbl, dk_entry]
        row += 1

        self._add_label_entry("Notion API key", self.notion_key_var, row, masked=True)
        row += 1
        self._add_label_entry("Notion page URL/ID", self.notion_page_var, row)
        row += 1

        ttk.Checkbutton(
            self,
            text="Replace existing Notion page content",
            variable=self.replace_var,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=6)
        row += 1

        button_frame = ttk.Frame(self)
        button_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=8)

        self.run_button = ttk.Button(button_frame, text="Run", command=self.run_pipeline)
        self.run_button.pack(side="left", padx=4)

        ttk.Button(button_frame, text="Save Config", command=self.save_config_from_ui).pack(
            side="left", padx=4
        )
        ttk.Button(button_frame, text="Load Config", command=self.reload_config).pack(
            side="left", padx=4
        )

        row += 1
        ttk.Label(self, text="Progress / Logs").grid(
            row=row, column=0, sticky="nw", padx=8, pady=(8, 0)
        )
        row += 1

        self.log_text = scrolledtext.ScrolledText(self, wrap="word", height=22)
        self.log_text.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
        self.rowconfigure(row, weight=1)

    def _add_label_entry(
        self,
        label: str,
        var: StringVar,
        row: int,
        masked: bool = False,
    ) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        entry = ttk.Entry(self, textvariable=var, show="*" if masked else "")
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)

    def log(self, message: str) -> None:
        """Thread-safe logging into the text area."""

        def _append() -> None:
            self.log_text.insert("end", f"{message}\n")
            self.log_text.see("end")

        self.after(0, _append)

    def load_config(self) -> AppConfig:
        """Load config from config.json, then .env defaults."""
        cfg = AppConfig()

        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as file:
                raw = json.load(file)
            saved_models = raw.get("gemini_models", [])
            # Discard stale default lists from old versions so users see current models.
            if set(saved_models) == STALE_DEFAULT_MODELS:
                saved_models = list(DEFAULT_MODELS)
            cfg = AppConfig(
                gemini_api_key=raw.get("gemini_api_key", ""),
                deepseek_api_key=raw.get("deepseek_api_key", ""),
                notion_api_key=raw.get("notion_api_key", ""),
                llm_provider=raw.get("llm_provider", "gemini"),
                gemini_model=raw.get("gemini_model", DEFAULT_MODELS[0]),
                gemini_models=saved_models or list(DEFAULT_MODELS),
                deepseek_model=raw.get("deepseek_model", DEFAULT_DEEPSEEK_MODELS[0]),
                deepseek_models=raw.get("deepseek_models", list(DEFAULT_DEEPSEEK_MODELS)),
                notion_page_id=raw.get("notion_page_id", ""),
                replace_existing_content=bool(raw.get("replace_existing_content", False)),
            )

        # Environment fallback for users who prefer .env editing.
        cfg.gemini_api_key = cfg.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        cfg.deepseek_api_key = cfg.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY", "")
        cfg.notion_api_key = cfg.notion_api_key or os.getenv("NOTION_API_KEY", "")
        cfg.notion_page_id = cfg.notion_page_id or os.getenv("NOTION_PAGE_ID", "")

        if cfg.gemini_model not in cfg.gemini_models:
            cfg.gemini_models.append(cfg.gemini_model)

        return cfg

    def apply_config_to_ui(self, cfg: AppConfig) -> None:
        """Populate UI controls from config object."""
        self.youtube_url_var.set("")
        self.provider_var.set(cfg.llm_provider)
        self.gemini_key_var.set(cfg.gemini_api_key)
        self.deepseek_key_var.set(cfg.deepseek_api_key)
        self.notion_key_var.set(cfg.notion_api_key)
        self.notion_page_var.set(cfg.notion_page_id)
        self.replace_var.set(cfg.replace_existing_content)

        self._gemini_models_cache = cfg.gemini_models or list(DEFAULT_MODELS)
        self._gemini_model_cache = cfg.gemini_model or self._gemini_models_cache[0]
        self._deepseek_models_cache = cfg.deepseek_models or list(DEFAULT_DEEPSEEK_MODELS)
        self._deepseek_model_cache = cfg.deepseek_model or self._deepseek_models_cache[0]

        # Trigger provider UI sync: hides/shows key fields and sets model list.
        self._on_provider_changed()

        if cfg.llm_provider == "gemini" and cfg.gemini_api_key:
            self.log("Gemini API key detected. Refreshing model list in background...")
            self.refresh_gemini_models()

    def collect_config_from_ui(self) -> AppConfig:
        """Collect current UI values into config object."""
        provider = self.provider_var.get().strip() or "gemini"
        model_values = list(self.model_combo["values"])
        model_name = self.gemini_model_var.get().strip()

        if provider == "deepseek":
            deepseek_models = model_values or list(DEFAULT_DEEPSEEK_MODELS)
            deepseek_model = model_name or deepseek_models[0]
            gemini_models = self._gemini_models_cache
            gemini_model = self._gemini_model_cache
        else:
            gemini_models = model_values or list(DEFAULT_MODELS)
            gemini_model = model_name or gemini_models[0]
            deepseek_models = self._deepseek_models_cache
            deepseek_model = self._deepseek_model_cache

        return AppConfig(
            gemini_api_key=self.gemini_key_var.get().strip(),
            deepseek_api_key=self.deepseek_key_var.get().strip(),
            notion_api_key=self.notion_key_var.get().strip(),
            llm_provider=provider,
            gemini_model=gemini_model,
            gemini_models=gemini_models,
            deepseek_model=deepseek_model,
            deepseek_models=deepseek_models,
            notion_page_id=self.notion_page_var.get().strip(),
            replace_existing_content=self.replace_var.get(),
        )

    def save_config_from_ui(self) -> None:
        """Save current config to disk."""
        cfg = self.collect_config_from_ui()
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            json.dump(asdict(cfg), file, indent=2)
        self.log(f"Saved config to {CONFIG_PATH.resolve()}")
        messagebox.showinfo("Saved", "Configuration saved successfully.")

    def reload_config(self) -> None:
        """Reload config from disk."""
        try:
            self.config_data = self.load_config()
            self.apply_config_to_ui(self.config_data)
            self.log("Loaded configuration from file/environment.")
        except Exception as exc:
            messagebox.showerror("Load Config Error", str(exc))

    def _on_provider_changed(self, _event: object = None) -> None:
        """Switch key field visibility and model list when provider changes."""
        # When triggered by user action (not initial setup), save current selection
        # to the outgoing provider's cache before switching.
        if _event is not None:
            old_provider = "deepseek" if self.provider_var.get() == "gemini" else "gemini"
            if old_provider == "gemini":
                self._gemini_model_cache = self.gemini_model_var.get().strip() or self._gemini_model_cache
                self._gemini_models_cache = list(self.model_combo["values"]) or self._gemini_models_cache
            else:
                self._deepseek_model_cache = self.gemini_model_var.get().strip() or self._deepseek_model_cache
                self._deepseek_models_cache = list(self.model_combo["values"]) or self._deepseek_models_cache

        provider = self.provider_var.get()
        if provider == "deepseek":
            for w in self._gemini_key_row:
                w.grid_remove()
            self._deepseek_key_row[0].grid(row=3, column=0, sticky="w", padx=8, pady=6)
            self._deepseek_key_row[1].grid(row=3, column=1, sticky="ew", padx=8, pady=6)
            models = self._deepseek_models_cache
            current_model = self._deepseek_model_cache
            self.log("Switched to DeepSeek provider.")
        else:
            for w in self._deepseek_key_row:
                w.grid_remove()
            self._gemini_key_row[0].grid(row=3, column=0, sticky="w", padx=8, pady=6)
            self._gemini_key_row[1].grid(row=3, column=1, sticky="ew", padx=8, pady=6)
            models = self._gemini_models_cache
            current_model = self._gemini_model_cache
            self.log("Switched to Gemini provider.")

        self.model_combo["values"] = models
        self.gemini_model_var.set(current_model if current_model in models else models[0])

    def _refresh_models(self) -> None:
        """Refresh model dropdown for the currently selected provider."""
        if self.provider_var.get() == "deepseek":
            models = list(DEFAULT_DEEPSEEK_MODELS)
            self._deepseek_models_cache = models
            self.model_combo["values"] = models
            current = self.gemini_model_var.get().strip()
            self.gemini_model_var.set(current if current in models else models[0])
            self._deepseek_model_cache = self.gemini_model_var.get()
            self.log(f"DeepSeek models: {', '.join(models)}")
        else:
            self.refresh_gemini_models()

    def refresh_gemini_models(self) -> None:
        """Refresh Gemini model options from the Gemini API."""
        gemini_key = self.gemini_key_var.get().strip()
        if not gemini_key:
            self.log("Skipping model refresh: Gemini API key is empty.")
            return

        self.log("Refreshing Gemini model list from API...")
        worker = threading.Thread(
            target=self._refresh_gemini_models_worker,
            args=(gemini_key,),
            daemon=True,
        )
        worker.start()

    def _refresh_gemini_models_worker(self, gemini_key: str) -> None:
        try:
            models = fetch_gemini_models(gemini_key)
        except Exception as exc:
            self.log(
                "Model refresh failed. Please verify your Gemini API key and network "
                "connectivity. Falling back to built-in model list."
            )
            self.log(f"Model refresh error details: {exc}")
            return

        self.after(0, lambda: self._apply_gemini_models(models))
        self.log(f"Loaded {len(models)} Gemini models from API.")

    def _apply_gemini_models(self, models: List[str]) -> None:
        if not models:
            self.log("Model refresh returned no models. Keeping existing model list.")
            return
        self._gemini_models_cache = models
        if self.provider_var.get() != "gemini":
            # Not the active provider; just update cache silently.
            return
        current_model = self.gemini_model_var.get().strip()
        self.model_combo["values"] = models
        self.gemini_model_var.set(current_model if current_model in models else models[0])
        self._gemini_model_cache = self.gemini_model_var.get()

    def run_pipeline(self) -> None:
        """Run the full transcript -> page content -> Notion workflow."""
        self.run_button.configure(state="disabled")
        worker = threading.Thread(target=self._run_pipeline_worker, daemon=True)
        worker.start()

    def _run_pipeline_worker(self) -> None:
        def fail(message: str) -> None:
            self.log(f"ERROR: {message}")
            self.after(0, lambda: messagebox.showerror("Process Failed", message))

        try:
            cfg = self.collect_config_from_ui()
            youtube_url = self.youtube_url_var.get().strip()

            if not youtube_url:
                raise AppError("Please enter a YouTube URL.")
            if not cfg.notion_api_key:
                raise AppError("Please enter a Notion API key.")
            if not cfg.notion_page_id:
                raise AppError("Please enter a Notion page ID or URL.")
            if cfg.llm_provider == "deepseek" and not cfg.deepseek_api_key:
                raise AppError("Please enter a DeepSeek API key.")
            if cfg.llm_provider == "gemini" and not cfg.gemini_api_key:
                raise AppError("Please enter a Gemini API key.")

            self.log("Extracting video ID...")
            video_id = extract_video_id(youtube_url)
            self.log(f"Video ID: {video_id}")

            self.log("Fetching transcript...")
            transcript, source = get_transcript(video_id)
            self.log(f"Transcript source: {source}")

            char_limit = TRANSCRIPT_CHAR_LIMITS.get(cfg.llm_provider, 100_000)
            transcript, was_truncated = truncate_transcript(transcript, char_limit)
            if was_truncated:
                self.log(
                    f"Transcript truncated to {char_limit:,} chars "
                    f"({cfg.llm_provider} safety limit)."
                )

            provider_label = "DeepSeek" if cfg.llm_provider == "deepseek" else "Gemini"
            model_name = cfg.deepseek_model if cfg.llm_provider == "deepseek" else cfg.gemini_model
            self.log(f"Summarizing with {provider_label} model: {model_name}")
            summary_markdown = summarize(
                transcript=transcript,
                provider=cfg.llm_provider,
                gemini_key=cfg.gemini_api_key,
                deepseek_key=cfg.deepseek_api_key,
                gemini_model=cfg.gemini_model,
                deepseek_model=cfg.deepseek_model,
            )

            self.log("Converting markdown to Notion blocks...")
            blocks = markdown_to_notion_blocks(summary_markdown)
            if not blocks:
                raise AppError("Generated content parsing produced no Notion blocks.")

            notion_page_id = normalize_page_id(cfg.notion_page_id)
            notion = Client(auth=cfg.notion_api_key)

            if cfg.replace_existing_content:
                self.log("Clearing existing Notion content...")
                clear_notion_page(notion, notion_page_id)

            self.log(f"Appending {len(blocks)} blocks to Notion page...")
            append_blocks_to_notion_page(notion, notion_page_id, blocks)

            self.log("Done. Page content written to Notion successfully.")
            self.after(0, lambda: messagebox.showinfo("Success", "Page content sent to Notion."))

        except (AppError, TranscriptError, APIResponseError) as exc:
            fail(str(exc))
        except requests.RequestException as exc:
            fail(f"Network error: {exc}")
        except Exception as exc:  # Unexpected runtime errors.
            self.log(traceback.format_exc())
            fail(f"Unexpected error: {exc}")
        finally:
            self.after(0, lambda: self.run_button.configure(state="normal"))


def main() -> None:
    app = YouTubeToNotionApp()
    app.mainloop()


if __name__ == "__main__":
    main()
