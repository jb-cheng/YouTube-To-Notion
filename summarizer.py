"""LLM summarization — Gemini and DeepSeek providers."""

from __future__ import annotations

from typing import List

from google import genai
from google.genai import types
from openai import OpenAI

from config import GEMINI_PROMPT, TRANSCRIPT_CHAR_LIMITS
from exceptions import AppError


def truncate_transcript(transcript: str, limit: int) -> tuple[str, bool]:
    """Truncate transcript to *limit* characters. Returns (text, was_truncated)."""
    if len(transcript) <= limit:
        return transcript, False
    return transcript[:limit] + "\n\n...[truncated]", True


def summarize(
    transcript: str,
    *,
    provider: str,
    gemini_key: str = "",
    deepseek_key: str = "",
    gemini_model: str = "",
    deepseek_model: str = "",
    gemini_use_grounding: bool = False,
) -> str:
    """Route summarization to the selected LLM provider."""
    if provider == "deepseek":
        return _summarize_deepseek(transcript, deepseek_key, deepseek_model)
    return _summarize_gemini(transcript, gemini_key, gemini_model, gemini_use_grounding)


def _summarize_gemini(
    transcript: str, gemini_key: str, model_name: str, use_grounding: bool = False
) -> str:
    """Generate markdown page content from transcript using Gemini."""
    if not gemini_key:
        raise AppError("Gemini API key is empty.")

    client = genai.Client(api_key=gemini_key)
    prompt = GEMINI_PROMPT.format(transcript=transcript)

    try:
        config = (
            types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
            if use_grounding
            else None
        )
        response = client.models.generate_content(
            model=model_name, contents=prompt, config=config
        )
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
                {
                    "role": "system",
                    "content": "You are a technical writer. Output only markdown.",
                },
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
        raise AppError(
            "No Gemini generateContent models were returned for this API key."
        )
    return available
