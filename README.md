# YouTube → LLM → Notion

A Tkinter desktop app that:

1. Accepts a YouTube URL.
2. Extracts the transcript via `youtube-transcript-api`.
3. Sends the transcript to an LLM (Gemini or DeepSeek) to generate technical markdown wiki-style content.
4. Writes the generated markdown into a Notion page as blocks.

## Requirements

- Python 3.10+
- Pip dependencies from `requirements.txt`

Install:

```bash
pip install -r requirements.txt
```

## Run the app

```bash
python app.py
```

## Configuration

The app supports configuration through:

- **`config.json`** — API keys, model preferences, provider selection, and toggle options are persisted via the UI's **Save Config** / **Load Config** buttons.
- **`.env`** — fallback values for API keys and Notion page ID (see below).

> Both files are read from the **current working directory** — the same folder you launch the app from (or where the `.exe` lives when using the PyInstaller build). Drop a `.env` file next to your `.exe` or `app.py` to set defaults.

### `config.json` fields

| Field | Description |
|---|---|
| `gemini_api_key` | Gemini API key |
| `deepseek_api_key` | DeepSeek API key |
| `notion_api_key` | Notion internal integration token |
| `llm_provider` | `"gemini"` or `"deepseek"` |
| `gemini_model` | Active Gemini model name |
| `gemini_models` | Cached list of available Gemini models |
| `deepseek_model` | Active DeepSeek model name |
| `deepseek_models` | Cached list of available DeepSeek models |
| `replace_existing_content` | Whether to clear the Notion page before writing |
| `gemini_use_grounding` | Enable Google Search grounding (Gemini only) |

> **Note:** The Notion page URL/ID is **session-only** — it's entered in the UI each time and never saved to `config.json`.

### `.env` fallback keys

```
GEMINI_API_KEY=...
DEEPSEEK_API_KEY=...
NOTION_API_KEY=...
NOTION_PAGE_ID=...
```

### Getting API keys

- **Gemini API key**: [Google AI Studio](https://aistudio.google.com/apikey) — no billing required for the free tier.
- **DeepSeek API key**: [DeepSeek Platform](https://platform.deepseek.com/) → API keys → create a new key.
- **Notion API key**: [Notion Integrations](https://www.notion.so/my-integrations) → New integration → copy the Internal Integration Token. Then share your target page with the integration.

## UI Features

- YouTube URL input
- **LLM Provider** dropdown — switch between Gemini and DeepSeek
- **Model** dropdown (refreshable from the Gemini API for Gemini; built-in list for DeepSeek)
- Google Search grounding checkbox (Gemini only — fact-checks with live search)
- Masked API key inputs for Gemini, DeepSeek, and Notion
- Notion page URL/ID input (session-only — not saved to disk)
- **Replace existing content** toggle
- **Run** button with live scrollable log output
- **Save Config** / **Load Config** buttons
- Error dialogs with friendly messages

## Notion Markdown support

LLM-generated markdown is converted into Notion blocks with support for:

- `#` / `##` / `###` → headings 1–3
- paragraph text with inline formatting (**bold**, *italic*, `code`, $math$, ~~strikethrough~~)
- bullet lists (`-` / `*`)
- fenced code blocks (```` ``` ````)
- display math (`$$...$$`) and inline math (`$...$`)
- image placeholders (`[Image: description]` — passed through as plain text for manual replacement)

## Build `.exe` with PyInstaller

Use the included script:

```bash
build_exe.bat
```

Or run manually:

```bash
pyinstaller --onefile --windowed app.py
```

Generated executable appears in `dist/`.
