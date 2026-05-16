def test_alpha_wechat_cloudfront_image_url_strips_appmsg_suffix():
    from investment_system.collectors.alpha_wechat.fetch_wechat_articles import normalize_image_download_url

    raw = "https://cloudfront-s3.rabyte.cn/wechat_format/x/images/202605/a/b.png&from=appmsg"

    assert normalize_image_download_url(raw) == "https://cloudfront-s3.rabyte.cn/wechat_format/x/images/202605/a/b.png"


def test_common_wechat_cloudfront_image_url_strips_html_escaped_suffix():
    from investment_system.common.wechat_downloader.wechat_to_md import normalize_image_download_url

    raw = "https://cloudfront-s3.rabyte.cn/wechat_format/x/images/202605/a/b.webp&amp;from=appmsg"

    assert normalize_image_download_url(raw) == "https://cloudfront-s3.rabyte.cn/wechat_format/x/images/202605/a/b.webp"


def test_non_cloudfront_image_url_keeps_query():
    from investment_system.collectors.alpha_wechat.fetch_wechat_articles import normalize_image_download_url

    raw = "https://mmbiz.qpic.cn/example.png?wx_fmt=png&tp=webp"

    assert normalize_image_download_url(raw) == raw

