import json


def test_zhongxin_provider_uses_configured_chat_completions_url(tmp_path, monkeypatch):
    from investment_system.common.ai import ai_client

    config_path = tmp_path / "ai_models.json"
    key_path = tmp_path / "AI_api_keys.txt"
    model = "DeepSeek-V4-Pro"
    endpoint = "http://ai-api.example.test/v1/chat/completions"

    config_path.write_text(
        json.dumps(
            {
                "last_updated": ai_client._today_str(),
                "ai_provider": "zhongxin",
                "zhongxin_parallel_workers": 5,
                "zhongxin_default_model": model,
                "zhongxin_models": {
                    model: {
                        "daily_limit": 999999,
                        "daily_remaining": 999999,
                        "daily_used": 0,
                        "total_calls": 0,
                        "calls_by_type": {"short_text": 0},
                        "success_by_type": {"short_text": 0},
                        "error_counts": {},
                        "successful_calls": 0,
                        "total_duration_ms": 0,
                    }
                },
                "zhongxin_api_config": {
                    "base_url": endpoint,
                    "timeout_seconds": 240,
                    "retry_delay_seconds": 0,
                },
                "default_params": {
                    "short_text": {
                        "temperature": 0.2,
                        "max_tokens": 128,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    key_path.write_text("zhongxin: test-key\n", encoding="utf-8")

    monkeypatch.setattr(ai_client, "AI_MODELS_CONFIG_FILE", config_path)
    monkeypatch.setattr(ai_client, "API_KEYS_FILE", key_path)

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "pong"
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)

    client = ai_client.create_ai_client(log_func=lambda *_: None)
    response, metadata = client.call(
        prompt="ping",
        call_type=ai_client.AICallType.SHORT_TEXT,
        max_attempts_per_model=1,
    )

    assert response == "pong"
    assert metadata["provider"] == "zhongxin"
    assert metadata["model"] == model
    assert captured["url"] == endpoint
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == model


def test_zhongxin_parallel_workers_from_config(tmp_path, monkeypatch):
    from investment_system.common.ai import ai_client

    config_path = tmp_path / "ai_models.json"
    config_path.write_text(
        json.dumps({"ai_provider": "zhongxin", "zhongxin_parallel_workers": 7}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_client, "AI_MODELS_CONFIG_FILE", config_path)

    assert ai_client.get_parallel_workers() == 7


def test_ai_client_strips_think_tags_at_common_exit(tmp_path, monkeypatch):
    from investment_system.common.ai import ai_client

    config_path = tmp_path / "ai_models.json"
    key_path = tmp_path / "AI_api_keys.txt"
    model = "DeepSeek-V4-Pro"

    config_path.write_text(
        json.dumps(
            {
                "last_updated": ai_client._today_str(),
                "ai_provider": "zhongxin",
                "zhongxin_default_model": model,
                "zhongxin_models": {
                    model: {
                        "daily_limit": 999999,
                        "daily_remaining": 999999,
                        "daily_used": 0,
                        "total_calls": 0,
                        "calls_by_type": {"short_text": 0},
                        "success_by_type": {"short_text": 0},
                        "error_counts": {},
                        "successful_calls": 0,
                        "total_duration_ms": 0,
                    }
                },
                "zhongxin_api_config": {
                    "base_url": "http://ai-api.example.test/v1/chat/completions",
                    "timeout_seconds": 10,
                    "retry_delay_seconds": 0,
                },
                "default_params": {
                    "short_text": {
                        "temperature": 0.2,
                        "max_tokens": 128,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    key_path.write_text("zhongxin: test-key\n", encoding="utf-8")

    monkeypatch.setattr(ai_client, "AI_MODELS_CONFIG_FILE", config_path)
    monkeypatch.setattr(ai_client, "API_KEYS_FILE", key_path)

    class FakeResponse:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"content": "<think>hidden</think>\n最终答案"}}],
                "usage": {},
            }

    def fake_post(url, headers, json, timeout):
        return FakeResponse()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)

    client = ai_client.create_ai_client(log_func=lambda *_: None)
    response, _ = client.call(
        prompt="ping",
        call_type=ai_client.AICallType.SHORT_TEXT,
        max_attempts_per_model=1,
    )

    assert response == "最终答案"

