import os
import json
import tempfile
from datetime import datetime
from pathlib import Path


def save_json_atomic(data, file_path):
    """
    Atomically save JSON data.

    The temp file is written in the same directory as the target so os.replace
    stays on the same filesystem. A best-effort .bak copy is kept for recovery
    if the process is interrupted or the target becomes corrupted.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    backup_path = path.with_suffix(path.suffix + ".bak")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
            f.flush()

        if path.exists():
            try:
                backup_path.write_bytes(path.read_bytes())
            except Exception:
                pass

        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def load_json_with_backup(file_path, default=None):
    path = Path(file_path)
    backup_path = path.with_suffix(path.suffix + ".bak")
    fallback = {} if default is None else default

    for candidate in (path, backup_path):
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"读取 JSON 失败: {candidate}: {e}")
    return fallback


def load_download_history(history_file):
    return load_json_with_backup(history_file, default={})


def save_download_history(history, history_file):
    try:
        save_json_atomic(history, history_file)
    except Exception as e:
        print(f"保存历史记录失败: {e}")


def extract_date_from_key(meeting_key):
    if '|' in meeting_key:
        parts = meeting_key.split('|')
        if len(parts) >= 2:
            return parts[-1].strip()
    return ''


def clean_old_history(history_file, retention_days=90):
    history = load_download_history(history_file)
    now = datetime.now()
    cleaned_count = 0

    for key in list(history.keys()):
        if not isinstance(history[key], list):
            continue
        new_items = []

        for meeting_key in history[key]:
            date_str = extract_date_from_key(meeting_key)
            if date_str:
                try:
                    meeting_date = datetime.strptime(date_str, '%Y-%m-%d')
                    if (now - meeting_date).days <= retention_days:
                        new_items.append(meeting_key)
                    else:
                        cleaned_count += 1
                except ValueError:
                    new_items.append(meeting_key)
            else:
                new_items.append(meeting_key)

        history[key] = new_items

    if cleaned_count > 0:
        save_download_history(history, history_file)
        print(f"已清理 {cleaned_count} 条过期历史记录（超过{retention_days}天）")

    return cleaned_count


def add_to_history(history_file, category_key, meeting_keys, max_per_key=100):
    history = load_download_history(history_file)
    if category_key not in history:
        history[category_key] = []

    existing_keys = set(history[category_key])
    new_keys = set(meeting_keys)
    all_keys = existing_keys | new_keys

    sorted_keys = sorted(
        all_keys,
        key=lambda k: extract_date_from_key(k),
        reverse=True
    )
    history[category_key] = list(sorted_keys[:max_per_key])

    save_download_history(history, history_file)
    return len(new_keys - existing_keys)


def is_in_history(history_file, category_key, meeting_key):
    history = load_download_history(history_file)
    if category_key not in history:
        return False
    return meeting_key in history[category_key]
