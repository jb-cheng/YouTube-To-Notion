# YouTube-To-Notion

A Tkinter desktop app that:

1. Accepts a YouTube URL.
2. Extracts transcript via YouTube Data API v3 first, then falls back to `youtube-transcript-api`.
3. Sends transcript to Gemini to generate technical markdown page content describing what the video discusses.
4. Writes the generated page content into a Notion page as blocks.

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

- `config.json` (saved/loaded via UI buttons)
- `.env` (fallback values)

Supported values:

- `gemini_api_key`
- `notion_api_key`
- `youtube_api_key`
- `gemini_model`
- `gemini_models`
- `notion_page_id`
- `replace_existing_content`

Optional `.env` keys:

- `GEMINI_API_KEY`
- `NOTION_API_KEY`
- `YOUTUBE_API_KEY`
- `NOTION_PAGE_ID`

### Getting API keys

- **YouTube Data API v3 key**: Google Cloud Console → Enable YouTube Data API v3 → Create API key.
- **Gemini API key**: Google AI Studio / Google Cloud Generative AI credentials.
- **Notion API key**: Notion Integrations → Internal Integration Token. Share the target page with the integration.

## UI Features

- YouTube URL input
- Gemini model dropdown (refreshed from Gemini API)
- Masked key inputs
- Notion page URL/ID input
- Replace existing content toggle
- Run button with live logs
- Refresh button to fetch available Gemini models for your API key
- Save Config / Load Config buttons
- Error popups with friendly messages

## Notion Markdown support

Gemini markdown is converted into Notion blocks with support for:

- `#` → heading 1
- `##` → heading 2
- paragraph text
- bullet lists (`-` / `*`)
- fenced code blocks (```)

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
