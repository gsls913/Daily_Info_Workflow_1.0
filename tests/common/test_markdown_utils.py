from investment_system.common.markdown_utils import (
    bump_headings_when_h1_exists,
    fix_bold_span_boundary_spacing,
    merge_adjacent_bold_spans,
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


def test_normalize_markdown_output_merges_adjacent_bold_spans():
    text = "**赛维时代** **交流纪要****2026051****5**\n**Q** **：****e-bike****库存**"

    fixed = normalize_markdown_output(text)

    assert "****" not in fixed
    assert "**赛维时代 交流纪要20260515**" in fixed
    assert "**Q ：e-bike库存**" in fixed


def test_merge_adjacent_bold_spans_preserves_middle_spaces():
    text = "**文本1** **文本2**\n**文本3**   **文本4**\n**文本5****文本6**"

    fixed = merge_adjacent_bold_spans(text)

    assert fixed == "**文本1 文本2**\n**文本3   文本4**\n**文本5文本6**"


def test_bold_span_boundary_spacing_uses_paired_markers():
    text = "**A：**今年\n前文**：重点**后文\n**_重点_**正文\n**（提示）**正文\n**重点**正文"

    fixed = fix_bold_span_boundary_spacing(text)

    assert "**A：** 今年" in fixed
    assert "前文 **：重点**后文" in fixed
    assert "**_重点_** 正文" in fixed
    assert "**（提示）** 正文" in fixed
    assert "**重点**正文" in fixed


def test_bold_span_boundary_spacing_handles_left_and_right_edges_separately():
    text = "左**：开头标点**右\n左**结尾标点：**右\n左** 正文 **右"

    fixed = fix_bold_span_boundary_spacing(text)

    assert "左 **：开头标点**右" in fixed
    assert "左**结尾标点：** 右" in fixed
    assert "左 ** 正文 ** 右" in fixed


def test_bold_cleanup_keeps_metadata_colon_spacing():
    text = "- **日期**: 2026-05-14\n- **我的评价**: "

    assert normalize_markdown_output(text) == text


def test_bump_headings_when_h1_exists_demotes_all_headings_one_level():
    text = "# 一级\n\n## 二级\n\n正文"

    assert bump_headings_when_h1_exists(text) == "## 一级\n\n### 二级\n\n正文"

