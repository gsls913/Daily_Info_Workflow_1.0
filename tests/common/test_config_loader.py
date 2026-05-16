import pytest


def test_config_loader_reports_yaml_parse_error(tmp_path, monkeypatch):
    from investment_system.common.config import config_loader

    config_file = tmp_path / "config.yaml"
    config_file.write_text("ai:\n...\n  parallel_workers: 5\n", encoding="utf-8")

    monkeypatch.setattr(config_loader, "_CONFIG_FILE", str(config_file))
    config_loader.reload_config()

    assert "config.yaml 解析失败" in config_loader.get_config_load_error()
    with pytest.raises(RuntimeError, match="config.yaml 解析失败"):
        config_loader.get("paths.obsidian_base_dir")


def test_config_loader_reads_valid_yaml_after_reload(tmp_path, monkeypatch):
    from investment_system.common.config import config_loader

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'paths:\n  obsidian_base_dir: "D:\\\\notes"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(config_loader, "_CONFIG_FILE", str(config_file))
    config_loader.reload_config()

    assert config_loader.get_config_load_error() is None
    assert config_loader.get("paths.obsidian_base_dir") == "D:\\notes"

