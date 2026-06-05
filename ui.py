"""Tkinter UI for the YouTube → Notion pipeline."""

from __future__ import annotations

import threading
import traceback
from typing import List

import requests
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError
from tkinter import Tk, StringVar, messagebox, ttk
import tkinter as tk
from tkinter import scrolledtext

import config
from config import (
    AppConfig,
    CONFIG_PATH,
    DEFAULT_DEEPSEEK_MODELS,
    DEFAULT_GEMINI_MODELS,
    TRANSCRIPT_CHAR_LIMITS,
)
from exceptions import AppError, TranscriptError
from notion_writer import (
    append_blocks_to_notion_page,
    clear_notion_page,
    markdown_to_notion_blocks,
    normalize_page_id,
)
from summarizer import (
    fetch_gemini_models,
    summarize,
    truncate_transcript,
)
from transcript import extract_video_id, get_transcript


class YouTubeToNotionApp(Tk):
    """Main Tkinter UI application. Dark editorial theme with card-based sections."""

    # ── Midnight Workshop colour palette ──────────────────────────────────
    CLR_BG = "#1A1D23"
    CLR_SURFACE = "#22262E"
    CLR_SURFACE_LIGHT = "#272B34"
    CLR_BORDER = "#2E333C"
    CLR_TEXT = "#E8E8E8"
    CLR_TEXT_MUTED = "#8B8FA0"
    CLR_ACCENT = "#E8B86D"
    CLR_ACCENT_HOVER = "#F0C87A"
    CLR_ACCENT_PRESSED = "#D4A85A"
    CLR_INPUT_BG = "#2A2E36"
    CLR_INPUT_FOCUS = "#333842"
    CLR_SUCCESS = "#6BCB9D"
    CLR_ERROR = "#E05C5C"
    CLR_INFO = "#7AAAE0"

    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube \u2192 LLM \u2192 Notion")
        self.geometry("900x750")
        self.configure(bg=self.CLR_BG)

        load_dotenv(override=False)
        self.config_data = config.load_config()
        self._setup_theme()
        self._build_ui()
        self.apply_config_to_ui(self.config_data)

    # ------------------------------------------------------------------ #
    #  Theme & style system
    # ------------------------------------------------------------------ #

    def _setup_theme(self) -> None:
        """Configure ttk styles for the Midnight Workshop dark theme."""
        style = ttk.Style()
        style.theme_use("clam")

        heading_font = ("Georgia", 10)
        label_font = ("Segoe UI", 9)
        bold_font = ("Segoe UI", 9, "bold")

        # TFrame
        style.configure("TFrame", background=self.CLR_BG)

        # TLabel
        style.configure(
            "TLabel",
            background=self.CLR_BG,
            foreground=self.CLR_TEXT,
            font=label_font,
        )

        # TLabelframe — card-like bordered sections
        style.configure(
            "Card.TLabelframe",
            background=self.CLR_SURFACE,
            bordercolor=self.CLR_BORDER,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self.CLR_SURFACE,
            foreground=self.CLR_ACCENT,
            font=heading_font,
        )

        # TEntry
        style.configure(
            "Dark.TEntry",
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_TEXT,
            insertcolor=self.CLR_TEXT,
            borderwidth=1,
            relief="solid",
            padding=(6, 4),
        )
        style.map(
            "Dark.TEntry",
            fieldbackground=[("focus", self.CLR_INPUT_FOCUS)],
            bordercolor=[("focus", self.CLR_ACCENT)],
        )

        # TCombobox
        style.configure(
            "Dark.TCombobox",
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_TEXT,
            arrowcolor=self.CLR_TEXT_MUTED,
            borderwidth=1,
            relief="solid",
            padding=(6, 4),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("focus", self.CLR_INPUT_FOCUS)],
            bordercolor=[("focus", self.CLR_ACCENT)],
        )

        # TButton
        style.configure(
            "TButton",
            background=self.CLR_SURFACE_LIGHT,
            foreground=self.CLR_TEXT,
            borderwidth=1,
            relief="solid",
            padding=(12, 4),
            font=label_font,
        )
        style.map(
            "TButton",
            background=[("active", "#353B45"), ("pressed", "#1E2229")],
        )

        # Accent button — prominent call-to-action
        style.configure(
            "Accent.TButton",
            background=self.CLR_ACCENT,
            foreground=self.CLR_BG,
            borderwidth=1,
            relief="solid",
            padding=(16, 5),
            font=bold_font,
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", self.CLR_ACCENT_HOVER),
                ("pressed", self.CLR_ACCENT_PRESSED),
            ],
        )

        # TCheckbutton
        style.configure(
            "Dark.TCheckbutton",
            background=self.CLR_BG,
            foreground=self.CLR_TEXT,
            font=label_font,
        )
        style.map("Dark.TCheckbutton", background=[("active", self.CLR_BG)])

        # TSeparator (horizontal rule)
        style.configure(
            "Horizontal.TSeparator",
            background=self.CLR_BORDER,
        )

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)

        self.youtube_url_var = StringVar()
        self.provider_var = StringVar(value="gemini")
        self.gemini_model_var = StringVar()
        self.gemini_key_var = StringVar()
        self.deepseek_key_var = StringVar()
        self.notion_key_var = StringVar()
        self.notion_page_var = StringVar()
        self.replace_var = tk.BooleanVar(value=False)
        self.gemini_grounding_var = tk.BooleanVar(value=False)

        # ── Top bar ────────────────────────────────────────────────────────
        top_bar = tk.Frame(self, bg=self.CLR_BG, height=36)
        top_bar.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        top_bar.columnconfigure(1, weight=1)
        top_bar.grid_propagate(False)

        tk.Label(
            top_bar,
            text="YouTube \u2192 LLM \u2192 Notion",
            bg=self.CLR_BG,
            fg=self.CLR_ACCENT,
            font=("Bahnschrift", 13),
        ).grid(row=0, column=0, sticky="w", padx=(16, 8), pady=6)

        tk.Label(
            top_bar,
            text="Transcript \u00b7 Summarize \u00b7 Publish",
            bg=self.CLR_BG,
            fg=self.CLR_TEXT_MUTED,
            font=("Segoe UI", 8),
        ).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=6)

        # Thin decorative divider
        tk.Frame(self, bg=self.CLR_BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 6)
        )

        # ── Section 1: Video Input ─────────────────────────────────────────
        input_sec = ttk.LabelFrame(
            self,
            text="  \u25b6  Input",
            style="Card.TLabelframe",
            padding=(8, 6, 8, 8),
        )
        input_sec.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 0))
        input_sec.columnconfigure(1, weight=1)

        ttk.Label(input_sec, text="YouTube URL").grid(
            row=0, column=0, sticky="w", padx=4, pady=4
        )
        ttk.Entry(
            input_sec,
            textvariable=self.youtube_url_var,
            style="Dark.TEntry",
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        # ── Section 2: LLM Configuration ───────────────────────────────────
        self.llm_sec = ttk.LabelFrame(
            self,
            text="  \u25b6  LLM Configuration",
            style="Card.TLabelframe",
            padding=(8, 6, 8, 8),
        )
        self.llm_sec.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 0))
        self.llm_sec.columnconfigure(1, weight=1)

        r = 0
        # Provider dropdown
        ttk.Label(self.llm_sec, text="Provider").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        prov_frame = ttk.Frame(self.llm_sec)
        prov_frame.grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        prov_frame.columnconfigure(0, weight=1)
        self.provider_combo = ttk.Combobox(
            prov_frame,
            textvariable=self.provider_var,
            values=["gemini", "deepseek"],
            state="readonly",
            style="Dark.TCombobox",
        )
        self.provider_combo.grid(row=0, column=0, sticky="ew")
        self.provider_combo.bind(
            "<<ComboboxSelected>>", self._on_provider_changed
        )
        r += 1

        # Model dropdown + refresh button
        ttk.Label(self.llm_sec, text="Model").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        mdl_frame = ttk.Frame(self.llm_sec)
        mdl_frame.grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        mdl_frame.columnconfigure(0, weight=1)
        self.model_combo = ttk.Combobox(
            mdl_frame,
            textvariable=self.gemini_model_var,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.model_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            mdl_frame,
            text="Refresh",
            command=self._refresh_models,
        ).grid(row=0, column=1, padx=(6, 0))
        r += 1

        # Google Search grounding checkbox (Gemini only)
        self._grounding_row = r
        self._grounding_check = ttk.Checkbutton(
            self.llm_sec,
            text="Google Search grounding (Gemini only)",
            variable=self.gemini_grounding_var,
            style="Dark.TCheckbutton",
        )
        self._grounding_check.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        r += 1

        # Gemini API key entry
        self._gemini_key_row: List[tk.Widget] = []
        gk_lbl = ttk.Label(self.llm_sec, text="Gemini API key")
        gk_lbl.grid(row=r, column=0, sticky="w", padx=4, pady=4)
        gk_entry = ttk.Entry(
            self.llm_sec,
            textvariable=self.gemini_key_var,
            show="*",
            style="Dark.TEntry",
        )
        gk_entry.grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        self._gemini_key_row = [gk_lbl, gk_entry]

        # DeepSeek API key entry (same row, hidden by default)
        self._deepseek_key_row: List[tk.Widget] = []
        dk_lbl = ttk.Label(self.llm_sec, text="DeepSeek API key")
        dk_lbl.grid(row=r, column=0, sticky="w", padx=4, pady=4)
        dk_entry = ttk.Entry(
            self.llm_sec,
            textvariable=self.deepseek_key_var,
            show="*",
            style="Dark.TEntry",
        )
        dk_entry.grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        dk_lbl.grid_remove()
        dk_entry.grid_remove()
        self._deepseek_key_row = [dk_lbl, dk_entry]
        self._key_row = r  # saved for provider-switch toggling
        r += 1

        # ── Section 3: Notion Destination ──────────────────────────────────
        note_sec = ttk.LabelFrame(
            self,
            text="  \u25b6  Notion Destination",
            style="Card.TLabelframe",
            padding=(8, 6, 8, 8),
        )
        note_sec.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 0))
        note_sec.columnconfigure(1, weight=1)

        r = 0
        ttk.Label(note_sec, text="Notion API key").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        ttk.Entry(
            note_sec,
            textvariable=self.notion_key_var,
            show="*",
            style="Dark.TEntry",
        ).grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        r += 1

        ttk.Label(note_sec, text="Page URL / ID").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        ttk.Entry(
            note_sec,
            textvariable=self.notion_page_var,
            style="Dark.TEntry",
        ).grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        r += 1

        ttk.Checkbutton(
            note_sec,
            text="Replace existing page content",
            variable=self.replace_var,
            style="Dark.TCheckbutton",
        ).grid(row=r, column=1, sticky="w", padx=4, pady=4)
        r += 1

        # Action buttons
        btn_frame = ttk.Frame(note_sec)
        btn_frame.grid(
            row=r, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 4)
        )

        self.run_button = ttk.Button(
            btn_frame,
            text="Run Pipeline",
            style="Accent.TButton",
            command=self.run_pipeline,
        )
        self.run_button.pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_frame,
            text="Save Config",
            command=self.save_config_from_ui,
        ).pack(side="left", padx=4)
        ttk.Button(
            btn_frame,
            text="Load Config",
            command=self.reload_config,
        ).pack(side="left", padx=4)

        # ── Log area ───────────────────────────────────────────────────────
        tk.Label(
            self,
            text="  Progress & Logs",
            bg=self.CLR_BG,
            fg=self.CLR_TEXT_MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        ).grid(row=5, column=0, sticky="ew", padx=(20, 0), pady=(10, 2))

        self.log_text = scrolledtext.ScrolledText(
            self,
            wrap="word",
            height=20,
            bg=self.CLR_SURFACE,
            fg=self.CLR_TEXT_MUTED,
            insertbackground=self.CLR_ACCENT,
            font=("Consolas", 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.CLR_BORDER,
            highlightcolor=self.CLR_ACCENT,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.grid(
            row=6, column=0, sticky="nsew", padx=16, pady=(2, 12)
        )
        self.rowconfigure(6, weight=1)

        # Colour tags for log levels
        self.log_text.tag_config("error", foreground=self.CLR_ERROR)
        self.log_text.tag_config("success", foreground=self.CLR_SUCCESS)
        self.log_text.tag_config("info", foreground=self.CLR_INFO)

    # ------------------------------------------------------------------ #
    #  Logging (thread-safe, with colour-coded levels)
    # ------------------------------------------------------------------ #

    def log(self, message: str) -> None:
        """Thread-safe logging into the text area with auto-colouring."""

        def _append() -> None:
            tag = ""
            if message.startswith("ERROR:"):
                tag = "error"
            elif message.startswith("Done.") or "successfully" in message.lower():
                tag = "success"
            elif any(
                kw in message
                for kw in ("Refreshing", "Loaded", "Fetching", "Extracting")
            ):
                tag = "info"
            self.log_text.insert("end", f"{message}\n", tag)
            self.log_text.see("end")

        self.after(0, _append)

    # ------------------------------------------------------------------ #
    #  Config  (load/save/apply)
    # ------------------------------------------------------------------ #

    def apply_config_to_ui(self, cfg: AppConfig) -> None:
        """Populate UI controls from config object."""
        self.youtube_url_var.set("")
        self.provider_var.set(cfg.llm_provider)
        self.gemini_key_var.set(cfg.gemini_api_key)
        self.deepseek_key_var.set(cfg.deepseek_api_key)
        self.notion_key_var.set(cfg.notion_api_key)
        self.notion_page_var.set(cfg.notion_page_id)
        self.replace_var.set(cfg.replace_existing_content)
        self.gemini_grounding_var.set(cfg.gemini_use_grounding)

        self._gemini_models_cache = cfg.gemini_models or list(DEFAULT_GEMINI_MODELS)
        self._gemini_model_cache = (
            cfg.gemini_model or self._gemini_models_cache[0]
        )
        self._deepseek_models_cache = cfg.deepseek_models or list(
            DEFAULT_DEEPSEEK_MODELS
        )
        self._deepseek_model_cache = (
            cfg.deepseek_model or self._deepseek_models_cache[0]
        )

        # Trigger provider UI sync: hides/shows key fields and sets model list.
        self._on_provider_changed()

        if cfg.llm_provider == "gemini" and cfg.gemini_api_key:
            self.log(
                "Gemini API key detected. Refreshing model list in background..."
            )
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
            gemini_models = model_values or list(DEFAULT_GEMINI_MODELS)
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
            gemini_use_grounding=self.gemini_grounding_var.get(),
        )

    def save_config_from_ui(self) -> None:
        """Save current config to disk."""
        cfg = self.collect_config_from_ui()
        config.save_config(cfg)
        self.log(f"Saved config to {CONFIG_PATH.resolve()}")
        messagebox.showinfo("Saved", "Configuration saved successfully.")

    def reload_config(self) -> None:
        """Reload config from disk."""
        try:
            self.config_data = config.load_config()
            self.apply_config_to_ui(self.config_data)
            self.log("Loaded configuration from file/environment.")
        except Exception as exc:
            messagebox.showerror("Load Config Error", str(exc))

    # ------------------------------------------------------------------ #
    #  Provider switching & model refresh
    # ------------------------------------------------------------------ #

    def _on_provider_changed(self, _event: object = None) -> None:
        """Switch key field visibility and model list when provider changes."""
        # When triggered by user action (not initial setup), save current selection
        # to the outgoing provider's cache before switching.
        if _event is not None:
            old_provider = (
                "deepseek"
                if self.provider_var.get() == "gemini"
                else "gemini"
            )
            if old_provider == "gemini":
                self._gemini_model_cache = (
                    self.gemini_model_var.get().strip()
                    or self._gemini_model_cache
                )
                self._gemini_models_cache = (
                    list(self.model_combo["values"])
                    or self._gemini_models_cache
                )
            else:
                self._deepseek_model_cache = (
                    self.gemini_model_var.get().strip()
                    or self._deepseek_model_cache
                )
                self._deepseek_models_cache = (
                    list(self.model_combo["values"])
                    or self._deepseek_models_cache
                )

        provider = self.provider_var.get()
        r = self._key_row  # row inside llm_sec where both key fields live
        if provider == "deepseek":
            for w in self._gemini_key_row:
                w.grid_remove()
            self._deepseek_key_row[0].grid(
                row=r, column=0, sticky="w", padx=4, pady=4
            )
            self._deepseek_key_row[1].grid(
                row=r, column=1, sticky="ew", padx=4, pady=4
            )
            self._grounding_check.grid_remove()
            models = self._deepseek_models_cache
            current_model = self._deepseek_model_cache
            self.log("Switched to DeepSeek provider.")
        else:
            for w in self._deepseek_key_row:
                w.grid_remove()
            self._gemini_key_row[0].grid(
                row=r, column=0, sticky="w", padx=4, pady=4
            )
            self._gemini_key_row[1].grid(
                row=r, column=1, sticky="ew", padx=4, pady=4
            )
            self._grounding_check.grid(
                row=self._grounding_row,
                column=1,
                sticky="w",
                padx=4,
                pady=2,
            )
            models = self._gemini_models_cache
            current_model = self._gemini_model_cache
            self.log("Switched to Gemini provider.")

        self.model_combo["values"] = models
        self.gemini_model_var.set(
            current_model if current_model in models else models[0]
        )

    def _refresh_models(self) -> None:
        """Refresh model dropdown for the currently selected provider."""
        if self.provider_var.get() == "deepseek":
            models = list(DEFAULT_DEEPSEEK_MODELS)
            self._deepseek_models_cache = models
            self.model_combo["values"] = models
            current = self.gemini_model_var.get().strip()
            self.gemini_model_var.set(
                current if current in models else models[0]
            )
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
                "Model refresh failed. Please verify your Gemini API key and "
                "network connectivity. Falling back to built-in model list."
            )
            self.log(f"Model refresh error details: {exc}")
            return

        self.after(0, lambda: self._apply_gemini_models(models))
        self.log(f"Loaded {len(models)} Gemini models from API.")

    def _apply_gemini_models(self, models: List[str]) -> None:
        if not models:
            self.log(
                "Model refresh returned no models. Keeping existing model list."
            )
            return
        self._gemini_models_cache = models
        if self.provider_var.get() != "gemini":
            # Not the active provider; just update cache silently.
            return
        current_model = self.gemini_model_var.get().strip()
        self.model_combo["values"] = models
        self.gemini_model_var.set(
            current_model if current_model in models else models[0]
        )
        self._gemini_model_cache = self.gemini_model_var.get()

    # ------------------------------------------------------------------ #
    #  Pipeline
    # ------------------------------------------------------------------ #

    def run_pipeline(self) -> None:
        """Run the full transcript -> page content -> Notion workflow."""
        self.run_button.configure(state="disabled")
        worker = threading.Thread(
            target=self._run_pipeline_worker, daemon=True
        )
        worker.start()

    def _run_pipeline_worker(self) -> None:
        def fail(message: str) -> None:
            self.log(f"ERROR: {message}")
            self.after(
                0, lambda: messagebox.showerror("Process Failed", message)
            )

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
            transcript, was_truncated = truncate_transcript(
                transcript, char_limit
            )
            if was_truncated:
                self.log(
                    f"Transcript truncated to {char_limit:,} chars "
                    f"({cfg.llm_provider} safety limit)."
                )

            provider_label = (
                "DeepSeek"
                if cfg.llm_provider == "deepseek"
                else "Gemini"
            )
            model_name = (
                cfg.deepseek_model
                if cfg.llm_provider == "deepseek"
                else cfg.gemini_model
            )
            self.log(
                f"Summarizing with {provider_label} model: {model_name}"
            )
            if cfg.llm_provider == "gemini" and cfg.gemini_use_grounding:
                self.log(
                    "Google Search grounding enabled \u2014 "
                    "LLM may fact-check with live search."
                )
            summary_markdown = summarize(
                transcript=transcript,
                provider=cfg.llm_provider,
                gemini_key=cfg.gemini_api_key,
                deepseek_key=cfg.deepseek_api_key,
                gemini_model=cfg.gemini_model,
                deepseek_model=cfg.deepseek_model,
                gemini_use_grounding=cfg.gemini_use_grounding,
            )

            self.log("Converting markdown to Notion blocks...")
            blocks = markdown_to_notion_blocks(summary_markdown)
            if not blocks:
                raise AppError(
                    "Generated content parsing produced no Notion blocks."
                )

            notion_page_id = normalize_page_id(cfg.notion_page_id)
            notion = Client(auth=cfg.notion_api_key)

            if cfg.replace_existing_content:
                self.log("Clearing existing Notion content...")
                clear_notion_page(notion, notion_page_id)

            self.log(
                f"Appending {len(blocks)} blocks to Notion page..."
            )
            append_blocks_to_notion_page(notion, notion_page_id, blocks)

            self.log("Done. Page content written to Notion successfully.")
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Success", "Page content sent to Notion."
                ),
            )

        except (AppError, TranscriptError, APIResponseError) as exc:
            fail(str(exc))
        except requests.RequestException as exc:
            fail(f"Network error: {exc}")
        except Exception as exc:  # Unexpected runtime errors.
            self.log(traceback.format_exc())
            fail(f"Unexpected error: {exc}")
        finally:
            self.after(
                0, lambda: self.run_button.configure(state="normal")
            )
