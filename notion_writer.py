"""Notion block construction — markdown-to-block parsing, page clearing, batch append."""

from __future__ import annotations

import re
from typing import Iterable, List

from notion_client import Client
from notion_client.errors import APIResponseError

from exceptions import AppError


def split_text_chunks(text: str, chunk_size: int = 1800) -> List[str]:
    """Split long text into Notion-safe rich_text chunks."""
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# Regex for inline markdown patterns — order matters so ** beats * and $$ beats $.
_INLINE_PATTERN = re.compile(
    r"\*\*(.+?)\*\*"  # 1 – bold
    r"|\*(.+?)\*"  # 2 – italic
    r"|`(.+?)`"  # 3 – inline code
    r"|\$\$(.+?)\$\$"  # 4 – display math inline (edge case)
    r"|\$(.+?)\$"  # 5 – inline math
    r"|~~(.+?)~~"  # 6 – strikethrough
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
        if match.group(1):  # bold
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
                {
                    "type": "equation",
                    "equation": {"expression": match.group(4).strip()},
                }
            )
            last_end = match.end()
            continue
        elif match.group(5):  # inline math ($…$)
            results.append(
                {
                    "type": "equation",
                    "equation": {"expression": match.group(5).strip()},
                }
            )
            last_end = match.end()
            continue
        elif match.group(6):  # strikethrough
            ann = {"strikethrough": True}
            content = match.group(6)

        for chunk in split_text_chunks(content):
            results.append(
                {
                    "type": "text",
                    "text": {"content": chunk},
                    "annotations": ann,
                }
            )
        last_end = match.end()

    # Trailing plain text
    if last_end < len(text):
        for chunk in split_text_chunks(text[last_end:]):
            results.append({"type": "text", "text": {"content": chunk}})

    return (
        results
        if results
        else [{"type": "text", "text": {"content": text.strip() or " "}}]
    )


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
    block_type = {1: "heading_1", 2: "heading_2", 3: "heading_3"}.get(
        level, "heading_2"
    )
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
            blocks.append(
                paragraph_block("\n".join(paragraph_lines).strip())
            )
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
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        candidate,
    )
    if dashed_match:
        return dashed_match.group(1).lower()

    raise AppError("Invalid Notion page ID/URL format.")


def clear_notion_page(notion: Client, page_id: str) -> None:
    """Archive existing top-level blocks for the target page."""
    start_cursor = None
    while True:
        result = notion.blocks.children.list(
            block_id=page_id, start_cursor=start_cursor
        )
        for child in result.get("results", []):
            notion.blocks.update(block_id=child["id"], archived=True)

        if not result.get("has_more"):
            break
        start_cursor = result.get("next_cursor")


def append_blocks_to_notion_page(
    notion: Client, page_id: str, blocks: Iterable[dict]
) -> None:
    """Append blocks in Notion API batch-safe chunks."""
    batch: List[dict] = []
    for block in blocks:
        batch.append(block)
        if len(batch) == 100:
            notion.blocks.children.append(block_id=page_id, children=batch)
            batch = []

    if batch:
        notion.blocks.children.append(block_id=page_id, children=batch)
