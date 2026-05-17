from __future__ import annotations

import os
import shutil
import sys
import unicodedata
import re


UI_DEFAULT_WIDTH = 96
UI_ACCENT = "\033[38;5;208m"
UI_RESET = "\033[0m"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style(text: str, code: str = UI_ACCENT) -> str:
    if not supports_color():
        return text
    return f"{code}{text}{UI_RESET}"


def terminal_width() -> int:
    columns = shutil.get_terminal_size((UI_DEFAULT_WIDTH, 24)).columns
    return max(42, min(110, columns - 2))


def display_width(text: str) -> int:
    width = 0
    for char in ANSI_RE.sub("", str(text)):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def fit_text(text: str, width: int) -> str:
    text = str(text)
    result = []
    used = 0
    idx = 0
    while idx < len(text):
        ansi_match = ANSI_RE.match(text, idx)
        if ansi_match:
            result.append(ansi_match.group(0))
            idx = ansi_match.end()
            continue
        char = text[idx]
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
        idx += 1
    if used < display_width(text) and width >= 1:
        while result and used + 1 > width:
            removed = result.pop()
            if ANSI_RE.fullmatch(removed):
                continue
            used -= 2 if unicodedata.east_asian_width(removed) in {"F", "W"} else 1
        if used + 1 <= width:
            result.append("…")
            used += 1
    return "".join(result) + " " * max(0, width - used)


def wrap_text(text: str, width: int, subsequent_prefix: str = "") -> list[str]:
    text = str(text)
    if not text:
        return [""]
    lines = []
    current = ""
    current_width = 0
    limit = width
    idx = 0
    while idx < len(text):
        ansi_match = ANSI_RE.match(text, idx)
        if ansi_match:
            current += ansi_match.group(0)
            idx = ansi_match.end()
            continue
        char = text[idx]
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if current and current_width + char_width > limit:
            lines.append(current)
            current = subsequent_prefix
            current_width = display_width(subsequent_prefix)
            limit = width
        current += char
        current_width += char_width
        idx += 1
    lines.append(current)
    return lines


def box_top(width: int, title: str = "") -> str:
    inner_width = width - 2
    if title:
        label = f"─ {title} "
        label = fit_text(label, inner_width)
        line = label.rstrip() + "─" * max(0, inner_width - display_width(label.rstrip()))
        return "╭" + line + "╮"
    return "╭" + "─" * inner_width + "╮"


def print_box(lines: list[str], title: str = "", width: int | None = None):
    width = width or terminal_width()
    inner_width = width - 2
    print(style(box_top(width, title)))
    for line in lines:
        prefix = line[:len(line) - len(line.lstrip(" "))]
        wrapped = wrap_text(line, inner_width, subsequent_prefix=prefix)
        for item in wrapped:
            print(style("│") + fit_text(item, inner_width) + style("│"))
    print(style("╰" + "─" * inner_width + "╯"))


def print_rule(width: int | None = None):
    width = width or terminal_width()
    print(style("─" * width))


def _readline_windows(label: str) -> str:
    return input(style("› ") + label).strip()


def ui_prompt(label: str) -> str:
    if os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty():
        return _readline_windows(label)
    return input(style("› ") + label).strip()


def ui_pause_confirm(label: str = "按 Enter 确认，按 Ctrl+C 取消..."):
    input(style("› ") + label)


def ui_menu(
    title: str,
    items: list[tuple[str, str, str | None]],
    subtitle: str | None = None,
    notes: list[str] | None = None,
    footer: str | None = None,
):
    lines: list[str] = []
    if subtitle:
        lines.extend(wrap_text(subtitle, max(24, terminal_width() - 8)))
        lines.append("")
    for item_index, (key, label, detail) in enumerate(items):
        lines.append(style(f"  {key:<3}{label}"))
        if detail:
            for wrapped in wrap_text(detail, max(24, terminal_width() - 10), subsequent_prefix="     "):
                lines.append(f"     {wrapped.strip()}")
        if item_index != len(items) - 1:
            lines.append("")
    if notes:
        lines.append("")
        for note in notes:
            lines.append(f"  · {note}")
    if footer:
        lines.append("")
        lines.append(f"  {footer}")
    print()
    print_box(lines, title=title)


def ui_panel(title: str, lines: list[str], footer: str | None = None):
    panel_lines = list(lines)
    if footer:
        panel_lines.append("")
        panel_lines.append(footer)
    print()
    print_box(panel_lines, title=title)


def ui_success(message: str):
    print(style("✓ ") + message)


def ui_error(message: str):
    print(f"❌ {message}")


def ui_warn(message: str):
    print(f"⚠️  {message}")

