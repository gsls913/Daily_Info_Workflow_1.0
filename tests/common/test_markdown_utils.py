from investment_system.common.markdown_utils import (
    bump_headings_when_h1_exists,
    normalize_markdown_output,
)


def test_normalize_markdown_output_adds_space_after_paragraph_initial_bold():
    text = "**核心信息**这是正文\n- **日期**: 2026-05-14\n  **缩进标题**继续"

    fixed = normalize_markdown_output(text)

    assert "**核心信息** 这是正文" in fixed
    assert "- **日期**: 2026-05-14" in fixed
    assert "  **缩进标题** 继续" in fixed


def test_normalize_markdown_output_keeps_terminal_bold_span():
    assert normalize_markdown_output("**重点**") == "**重点**"


def test_bump_headings_when_h1_exists_demotes_all_headings_one_level():
    text = "# 一级\n\n## 二级\n\n正文"

    assert bump_headings_when_h1_exists(text) == "## 一级\n\n### 二级\n\n正文"

