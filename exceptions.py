"""Application exception hierarchy."""

from __future__ import annotations


class AppError(Exception):
    """Base application exception."""


class TranscriptError(AppError):
    """Raised when transcript retrieval fails."""
