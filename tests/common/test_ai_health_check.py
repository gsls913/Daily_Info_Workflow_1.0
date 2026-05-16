from investment_system.common.ai import health_check


def test_zhongxin_health_failure_reports_gateway_without_vpn_hint(monkeypatch):
    class BrokenClient:
        def call(self, *args, **kwargs):
            raise RuntimeError("HTTP 503")

    monkeypatch.setattr(health_check, "create_ai_client", lambda log_func=None: BrokenClient())
    monkeypatch.setattr(health_check, "get_current_provider", lambda: "zhongxin")

    result = health_check.check_ai_health()

    assert result["ok"] is False
    assert result["provider"] == "zhongxin"
    assert "中信 AI 网关返回 503" in result["hint"]
    assert "VPN" not in result["hint"]


def test_zhongxin_health_failure_reports_dns_hint(monkeypatch):
    class BrokenClient:
        def call(self, *args, **kwargs):
            raise RuntimeError("getaddrinfo failed")

    monkeypatch.setattr(health_check, "create_ai_client", lambda log_func=None: BrokenClient())
    monkeypatch.setattr(health_check, "get_current_provider", lambda: "zhongxin")

    result = health_check.check_ai_health()

    assert result["ok"] is False
    assert "无法解析中信 AI 域名" in result["hint"]
    assert "VPN" not in result["hint"]

