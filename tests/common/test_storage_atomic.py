import json

from investment_system.common.storage.download_history import load_json_with_backup, save_json_atomic


def test_save_json_atomic_writes_target_and_backup(tmp_path):
    path = tmp_path / "state.json"

    save_json_atomic({"version": 1}, path)
    save_json_atomic({"version": 2}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 2}
    assert json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8")) == {"version": 1}


def test_load_json_with_backup_falls_back_to_backup(tmp_path):
    path = tmp_path / "state.json"

    save_json_atomic({"ok": True}, path)
    path.write_text("{broken", encoding="utf-8")

    assert load_json_with_backup(path, default={}) == {"ok": True}

