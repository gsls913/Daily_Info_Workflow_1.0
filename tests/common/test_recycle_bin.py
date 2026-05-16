import os
import json
import time

from investment_system.common.runtime import recycle_bin


def test_move_to_recycle_bin_preserves_file_and_records_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    source = tmp_path / "article.md"
    source.write_text("hello", encoding="utf-8")

    moved = recycle_bin.move_to_recycle_bin(source, category="wechat", item_type="markdown")

    assert not source.exists()
    assert moved.destination.exists()
    assert moved.destination.read_text(encoding="utf-8") == "hello"
    assert (moved.destination.parent / "_manifest.json").exists()


def test_purge_recycle_bin_removes_old_files_only(tmp_path, monkeypatch):
    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    root = tmp_path / "recycle"
    old_file = root / "wechat" / "markdown" / "old.md"
    new_file = root / "wechat" / "markdown" / "new.md"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    old_time = time.time() - 11 * 24 * 3600
    os.utime(old_file, (old_time, old_time))

    deleted = recycle_bin.purge_recycle_bin(retention_days=10)

    assert deleted == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_moved_old_file_is_kept_until_recycle_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    source = tmp_path / "old_source.md"
    source.write_text("old content", encoding="utf-8")
    old_time = time.time() - 90 * 24 * 3600
    os.utime(source, (old_time, old_time))

    moved = recycle_bin.move_to_recycle_bin(source, category="memo", item_type="markdown")

    deleted = recycle_bin.purge_recycle_bin(retention_days=10)

    assert deleted == 0
    assert moved.destination.exists()


def test_purge_recycle_bin_uses_manifest_moved_at_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    root = tmp_path / "recycle"
    folder = root / "wechat" / "markdown" / "20260515"
    folder.mkdir(parents=True)
    old_file = folder / "old.md"
    old_file.write_text("old", encoding="utf-8")
    manifest = [
        {
            "source": "D:/source/old.md",
            "destination": str(old_file),
            "category": "wechat",
            "item_type": "markdown",
            "moved_at": "2000-01-01T00:00:00",
        }
    ]
    (folder / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    deleted = recycle_bin.purge_recycle_bin(retention_days=10)

    assert deleted == 1
    assert not old_file.exists()

