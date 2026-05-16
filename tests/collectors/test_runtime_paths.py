from pathlib import Path


def test_failed_queue_paths_live_under_data_history():
    from investment_system.collectors.alpha_wechat import fetch_wechat_articles
    from investment_system.collectors.notion import notion_collector, notion_link_collector
    from investment_system.common.utils.paths import HISTORY_DIR

    history_root = Path(HISTORY_DIR).resolve()
    paths = [
        fetch_wechat_articles.FAILED_ARTICLES_FILE,
        notion_collector.FAILED_ARTICLES_FILE,
        notion_link_collector.FAILED_LINKS_FILE,
    ]

    for path in paths:
        Path(path).resolve().relative_to(history_root)


def test_tingwu_runtime_defaults_live_outside_source_package():
    from investment_system.collectors.podcast.tingwu_python_workflow import tingwu_common
    from investment_system.common.utils.paths import CREDENTIALS_DIR, RUNTIME_DIR

    runtime_root = Path(RUNTIME_DIR).resolve()
    credentials_root = Path(CREDENTIALS_DIR).resolve()

    tingwu_common.WORKDIR.resolve().relative_to(runtime_root)
    tingwu_common.DEFAULT_PROFILE_DIR.resolve().relative_to(runtime_root)
    tingwu_common.DEFAULT_STORAGE_STATE.resolve().relative_to(credentials_root)


def test_tingwu_modules_import_as_package():
    import investment_system.collectors.podcast.tingwu_python_workflow.tingwu_api_upload  # noqa: F401
    import investment_system.collectors.podcast.tingwu_python_workflow.tingwu_delete_record  # noqa: F401
    import investment_system.collectors.podcast.tingwu_python_workflow.tingwu_export_download  # noqa: F401
    import investment_system.collectors.podcast.tingwu_python_workflow.tingwu_login_probe  # noqa: F401
    import investment_system.collectors.podcast.tingwu_python_workflow.tingwu_profile  # noqa: F401


def test_company_memos_uses_primary_ai_package():
    from pathlib import Path

    source = Path("investment_system/collectors/alpha_memo/company_memos.py").read_text(encoding="utf-8")

    assert "investment_system.common.ai.aicontent_generator" in source
    assert "investment_system.workflow_ai.aicontent_generator" not in source


def test_legacy_compatibility_files_exist():
    required = [
        "common_libs/wechat_downloader/wechat_to_md.py",
        "common_libs/wechat_downloader/run_wechat_downloader.bat",
        "podcast_process/README.md",
        "podcast_process/tingwu_python_workflow/README.md",
        "workflow/ai/ai_models.example.json",
    ]

    for path in required:
        assert Path(path).exists(), path
