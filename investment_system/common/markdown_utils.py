"""Shared helpers for Markdown text written by the workflow."""

from __future__ import annotations

import re
from collections.abc import Iterable


def fix_paragraph_initial_bold_spacing(markdown: str) -> str:
    """Add a space after paragraph-initial bold spans when content follows.

    Some Markdown renderers parse a paragraph like ``**标题**正文`` as an
    over-broad bold run. We only touch lines whose visible text starts with the
    first bold marker, so metadata lines such as ``- **日期**:`` are preserved.
    """
    if not markdown:
        return markdown

    fixed_lines: list[str] = []
    for line in markdown.splitlines(keepends=True):
        newline = ""
        body = line
        if body.endswith("\r\n"):
            body, newline = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, newline = body[:-1], "\n"

        prefix_len = len(body) - len(body.lstrip(" \t"))
        prefix = body[:prefix_len]
        visible = body[prefix_len:]
        if visible.startswith("**"):
            closing = visible.find("**", 2)
            after = closing + 2
            if closing != -1 and after < len(visible) and not visible[after].isspace():
                visible = visible[:after] + " " + visible[after:]
        fixed_lines.append(prefix + visible + newline)

    return "".join(fixed_lines)


def bump_headings_when_h1_exists(markdown: str) -> str:
    """Demote all headings by one level when the text already contains H1."""
    if not re.search(r"(?m)^#\s+", markdown):
        return markdown

    def repl(match: re.Match[str]) -> str:
        hashes = match.group(1)
        rest = match.group(2)
        if len(hashes) >= 6:
            return match.group(0)
        return f"#{hashes}{rest}"

    return re.sub(r"(?m)^(#{1,6})(\s+)", repl, markdown)


def metadata_items(*values: object, empty_values: Iterable[str] = ("", "未知", "无")) -> list[str]:
    """Return clean metadata values while preserving the original order."""
    empty_set = {str(value).strip() for value in empty_values}
    items: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        if not text or text in empty_set or text in seen:
            return
        seen.add(text)
        items.append(text)

    for value in values:
        add(value)
    return items


def join_metadata_values(*values: object) -> str:
    """Join clean metadata values with a comma for compact front matter."""
    return ", ".join(metadata_items(*values))


def format_duration_minutes(value: object) -> str:
    """Format a duration value as minutes without duplicating the unit."""
    items = metadata_items(value)
    if not items:
        return ""
    text = items[0]
    return text if "分钟" in text else f"{text} 分钟"


def format_metadata_datetime(value: object) -> str:
    """Use a readable datetime style in Markdown metadata."""
    items = metadata_items(value, empty_values=("",))
    if not items:
        return ""
    return re.sub(r"(?<=\d{4}-\d{2}-\d{2})T(?=\d{2}:\d{2}:\d{2})", " ", items[0])


def normalize_markdown_output(markdown: str) -> str:
    """Apply safe project-wide Markdown output normalizations."""
    return fix_paragraph_initial_bold_spacing(markdown)

