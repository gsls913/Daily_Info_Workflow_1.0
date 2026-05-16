from investment_system.collectors.alpha_memo.link_memos import extract_urls, parse_alpha_memo_url


def test_parse_alpha_standard_memo_url():
    parsed = parse_alpha_memo_url(
        "https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId=abc123"
    )

    assert parsed["api_type"] == "standard"
    assert parsed["id"] == "abc123"


def test_parse_alpha_self_summary_url_decodes_id():
    parsed = parse_alpha_memo_url(
        "https://alphapai-web.rabyte.cn/reading/self-summary-detail?id=abc%3D"
    )

    assert parsed["api_type"] == "record_convert"
    assert parsed["id"] == "abc="


def test_extract_urls_from_multiple_lines():
    urls = extract_urls([
        "链接一 https://alphapai-web.rabyte.cn/reading/self-summary-detail?id=a%3D",
        "链接二：https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId=b，",
    ])

    assert urls == [
        "https://alphapai-web.rabyte.cn/reading/self-summary-detail?id=a%3D",
        "https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId=b",
    ]

