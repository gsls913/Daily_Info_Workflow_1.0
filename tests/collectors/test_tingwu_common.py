import requests


def test_post_json_with_retry_recovers_from_ssl_error(monkeypatch):
    from investment_system.collectors.podcast.tingwu_python_workflow import tingwu_common

    calls = {"count": 0}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {"ok": True}}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.SSLError("temporary tls EOF")
        return Response()

    monkeypatch.setattr(tingwu_common.requests, "post", fake_post)
    monkeypatch.setattr(tingwu_common.time, "sleep", lambda *_: None)

    result = tingwu_common.post_json_with_retry("cookie", "https://tingwu.example/api", {"action": "x"})

    assert result["data"]["ok"] is True
    assert calls["count"] == 2

