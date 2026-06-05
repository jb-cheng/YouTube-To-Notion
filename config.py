"""Configuration — constants, dataclass, load/save from JSON + .env."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

CONFIG_PATH = Path("config.json")
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
STALE_DEFAULT_MODELS = {"gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro"}
DEFAULT_DEEPSEEK_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"]
GEMINI_PROMPT = (
    "You are a technical writer creating a Notion wiki page from a YouTube transcript. "
    "Produce a reference-style entry covering key topics, architectures, workflows, "
    "and implementation details.\n\n"
    "## Structure\n"
    "- Use # / ## / ### for section hierarchy. Do NOT start with a top-level heading — "
    "open with a brief introductory paragraph (no heading), then begin sections with #.\n"
    "- The intro paragraph MUST be short (2-4 sentences max) and purely high-level — "
    "no technical detail, no specifics, no implementation depth. Just set the scene. "
    "Save all technical content for the body sections. The intro is NOT a summary of the "
    "whole video.\n\n"
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
    "- Image placeholders: `[Image: brief description of what the image should show]` — use "
    "these at natural points in the text where a diagram, screenshot, or illustration would "
    "aid understanding. The user will replace them with actual images later.\n"
    "- Output only markdown.\n\n"
    "Transcript:\n{transcript}"
)
TRANSCRIPT_CHAR_LIMITS = {"gemini": 150_000, "deepseek": 200_000}


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
    gemini_use_grounding: bool = False


def load_config() -> AppConfig:
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
            gemini_use_grounding=bool(raw.get("gemini_use_grounding", False)),
        )

    # Environment fallback for users who prefer .env editing.
    cfg.gemini_api_key = cfg.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    cfg.deepseek_api_key = cfg.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY", "")
    cfg.notion_api_key = cfg.notion_api_key or os.getenv("NOTION_API_KEY", "")
    cfg.notion_page_id = cfg.notion_page_id or os.getenv("NOTION_PAGE_ID", "")

    if cfg.gemini_model not in cfg.gemini_models:
        cfg.gemini_models.append(cfg.gemini_model)

    return cfg


def save_config(cfg: AppConfig) -> None:
    """Persist config to JSON file."""
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(asdict(cfg), file, indent=2)
