import os
import json
from datetime import datetime
from pathlib import Path


def save_json_atomic(data, file_path):
    """
    保存 JSON 文件。

    Windows 环境中部分受保护目录会拒绝 os.replace/os.rename 覆盖已有文件，
    因此直接覆盖写入；非 Windows 仍使用临时文件 + replace。
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)

    if os.name == "nt":
        path.write_text(text, encoding="utf-8")
        return

    temp_file = path.with_suffix(path.suffix + ".tmp")
    temp_file.write_text(text, encoding="utf-8")
    os.replace(temp_file, path)


def load_download_history(history_file):
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取历史记录失败: {e}")
    return {}


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
