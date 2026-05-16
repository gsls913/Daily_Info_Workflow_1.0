def test_ensure_zhongxin_models_adds_all_selectable_models():
    from investment_system.launcher import run_workflow

    config = {
        "zhongxin_default_model": "DeepSeek-V4-Pro",
        "zhongxin_models": {
            "DeepSeek-V4-Pro": run_workflow.make_ai_model_stats_template()
        },
    }

    changed = run_workflow.ensure_zhongxin_models(config)

    assert changed is True
    assert set(run_workflow.ZHONGXIN_MODEL_CHOICES).issubset(config["zhongxin_models"])
    assert config["zhongxin_models"]["glm-5.1"]["calls_by_type"]["long_thinking"] == 0
    assert config["zhongxin_default_model"] == "DeepSeek-V4-Pro"


def test_ensure_zhongxin_models_repairs_unknown_default():
    from investment_system.launcher import run_workflow

    config = {
        "zhongxin_default_model": "unknown-model",
        "zhongxin_models": {},
    }

    run_workflow.ensure_zhongxin_models(config)

    assert config["zhongxin_default_model"] == "DeepSeek-V4-Pro"


def test_checked_in_ai_model_json_files_are_parseable():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for relative_path in [
        "data/config/ai_models.json",
        "data/config/ai_models.example.json",
    ]:
        config = json.loads((root / relative_path).read_text(encoding="utf-8"))
        assert set(["DeepSeek-V4-Pro", "glm-5.1", "deepseek-v4-flash", "kimi-k2.6"]).issubset(
            config["zhongxin_models"]
        )

