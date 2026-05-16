def test_parse_url_inputs_extracts_multiple_unique_links():
    from investment_system.micro_programs.youdao_note_to_md import parse_url_inputs

    urls = parse_url_inputs([
        "【有道云笔记】20260514【盐津铺子】公司小范围交流",
        "https://share.note.youdao.com/s/abc123",
        "",
        "另一个：https://share.note.youdao.com/ynoteshare/index.html?id=123&type=note，",
        "无关链接：https://example.com/not-note",
        "https://share.note.youdao.com/s/abc123",
    ])

    assert urls == [
        "https://share.note.youdao.com/s/abc123",
        "https://share.note.youdao.com/ynoteshare/index.html?id=123&type=note",
    ]


def test_parse_youdao_editor_content_keeps_headings_lists_and_marks():
    from investment_system.micro_programs.youdao_note_to_md import parse_youdao_editor_content

    content = {
        "5": [
            {"6": "h", "4": {"l": "h2"}, "7": [{"8": "标题"}]},
            {"6": "l", "4": {"lt": "ordered", "ll": 1, "li": "a"}, "7": [{"8": "第一条"}]},
            {"6": "l", "4": {"lt": "ordered", "ll": 1, "li": "a"}, "7": [{"8": "第二条"}]},
            {"6": "p", "7": [{"8": "重点", "9": [{"2": "b"}]}]},
        ]
    }

    markdown = parse_youdao_editor_content(content)

    assert "## 标题" in markdown
    assert "1. 第一条" in markdown
    assert "2. 第二条" in markdown
    assert "**重点**" in markdown


def test_build_youdao_markdown_uses_memo_template_and_demotes_body_h1():
    from investment_system.micro_programs.youdao_note_to_md import NoteDocument, build_youdao_markdown

    document = NoteDocument(
        title="测试笔记",
        markdown="# 原标题\n\n**重点**正文",
        final_url="https://share.note.youdao.com/s/abc123",
    )

    content = build_youdao_markdown(document, document.final_url)

    assert content.startswith("# 基本信息\n")
    assert "- **原文链接**: [测试笔记](https://share.note.youdao.com/s/abc123)" in content
    assert "# 正文\n\n## 原标题" in content
    assert "# AI 评价" not in content

