"""YouTube transcript retrieval."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi

from exceptions import TranscriptError


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

    raise TranscriptError("Could not extract a valid YouTube video ID from the provided URL.")


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
