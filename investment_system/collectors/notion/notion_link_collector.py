"""
Notion link-only article collector.

Reads article URLs from a Notion database and appends them to an Obsidian
Markdown table without downloading full article content.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
from bs4 import BeautifulSoup
from notion_client import Client

from investment_system.common.config.config_loader import get as cfg, get_retention_days
from investment_system.common.runtime.task_state import update_task
from investment_system.common.storage.download_history import save_json_atomic
from investment_system.common.utils.logging_config import setup_logging, clean_old_logs, get_log_functions
from investment_system.common.utils.notifications import show_windows_notification
from investment_system.common.utils.paths import (
    NOTION_CONFIG_FILE,
    NOTION_LINK_FAILED_ARTICLES_FILE,
    NOTION_LINK_HISTORY_FILE,
    OBSIDIAN_BASE_DIR,
)

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_RETENTION_DAYS = get_retention_days('log')
logger = setup_logging(LOG_DIR, log_prefix="notion_link_collector", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)

DATABASE_NAME = cfg('notion_link_collection.database_name', '文章收藏（仅存链接）')
PROPERTY_STORED = cfg('notion_link_collection.property_stored', '是否已存到本地')
VALUE_STORED = cfg('notion_link_collection.value_stored', '已存到本地')
VALUE_FAILED = cfg('notion_link_collection.value_failed', '3次存到本地失败')
PROPERTY_FAILURE_COUNT = cfg('notion_link_collection.property_failure_count', '存本地失败次数')
MAX_STORE_FAILURES = cfg('notion_link_collection.max_store_failures', 3)
PROPERTY_URL = cfg('notion_link_collection.property_url', '网址')
PROPERTY_TITLE = cfg('notion_link_collection.property_title', '标题')
NOTION_PAGE_SIZE = cfg('notion_link_collection.page_size', 100)
OUTPUT_DIR = os.path.join(
    OBSIDIAN_BASE_DIR,
    cfg('notion_link_collection.output_dir', r'B综合\收藏文章')
)
OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    cfg('notion_link_collection.output_filename', '收藏文章合集.md')
)

FAILED_LINKS_FILE = NOTION_LINK_FAILED_ARTICLES_FILE
MAX_FAILED_LINKS = cfg('notion_link_collection.max_failed_links', 500)

URL_RE = re.compile(r"https?://[^\s<>\])\"']+")


def load_config():
    if not os.path.exists(NOTION_CONFIG_FILE):
        raise FileNotFoundError(f"配置文件不存在: {NOTION_CONFIG_FILE}")

    notion_token = None
    database_id = None
    database_names = {
        DATABASE_NAME,
        cfg('notion_link_collection.database_alias', '网页收藏（仅存链接）'),
        '网页收藏（仅存链接）',
        '文章收藏（仅存链接）',
    }
    with open(NOTION_CONFIG_FILE, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if parts[0] == "NotionToken" and len(parts) >= 2:
                notion_token = parts[-1]
            elif parts[0] in database_names and len(parts) >= 2:
                database_id = parts[-1]

    if not notion_token:
        raise ValueError("未找到 NotionToken")
    if not database_id:
        raise ValueError(f"未找到数据库配置: {DATABASE_NAME}")

    return notion_token, database_id


def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_warn(f"读取 JSON 失败: {path}: {e}")
    return default


def load_processed_links():
    history = load_json_file(NOTION_LINK_HISTORY_FILE, {"processed": {}})
    history.setdefault("processed", {})
    return history


def save_processed_links(history):
    save_json_atomic(history, NOTION_LINK_HISTORY_FILE)


def processed_key(page_id, url):
    return page_id or url


def is_processed_locally(history, page_id, url):
    key = processed_key(page_id, url)
    return bool(key and key in history.get("processed", {}))


def get_processed_record(history, page_id, url):
    key = processed_key(page_id, url)
    if not key:
        return {}
    return history.get("processed", {}).get(key, {})


def record_processed_link(history, page_id, url, title, status="stored", output_file=None):
    key = processed_key(page_id, url)
    if not key:
        return
    history.setdefault("processed", {})[key] = {
        "page_id": page_id,
        "url": url,
        "title": title,
        "status": status,
        "output_file": output_file,
        "processed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_processed_links(history)


def add_failed_link(url, title, error):
    failed = load_json_file(FAILED_LINKS_FILE, [])
    if url and any(item.get("url") == url for item in failed):
        return
    failed.append({
        "url": url,
        "title": title,
        "error": str(error),
        "failed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    if len(failed) > MAX_FAILED_LINKS:
        failed = failed[-MAX_FAILED_LINKS:]
    save_json_atomic(failed, FAILED_LINKS_FILE)


def get_property_type(properties, property_name):
    return (properties or {}).get(property_name, {}).get("type", "")


def pick_property(properties, preferred_name, allowed_types, name_hints=()):
    if preferred_name in properties and properties[preferred_name].get("type") in allowed_types:
        return preferred_name, properties[preferred_name].get("type")
    for prop_name, meta in properties.items():
        if meta.get("type") in allowed_types and any(hint in prop_name for hint in name_hints):
            return prop_name, meta.get("type")
    for prop_name, meta in properties.items():
        if meta.get("type") in allowed_types:
            return prop_name, meta.get("type")
    return preferred_name, ""


def get_plain_text(text_items):
    return "".join(item.get("plain_text", "") for item in (text_items or [])).strip()


def extract_url_from_property(prop):
    prop_type = prop.get("type")
    if prop_type == "url":
        return (prop.get("url") or "").strip()
    if prop_type in ("rich_text", "title"):
        text = get_plain_text(prop.get(prop_type))
        match = URL_RE.search(text)
        return match.group(0) if match else text.strip()
    return ""


def extract_url_from_page(page, url_property_name):
    props = page.get("properties", {})
    if url_property_name in props:
        return extract_url_from_property(props[url_property_name])
    for prop in props.values():
        url = extract_url_from_property(prop)
        if url.startswith(("http://", "https://")):
            return url
    return ""


def extract_title_from_page(page, title_property_name):
    prop = page.get("properties", {}).get(title_property_name, {})
    prop_type = prop.get("type")
    if prop_type in ("title", "rich_text"):
        return get_plain_text(prop.get(prop_type))
    return ""


def extract_number_from_page(page, property_name):
    prop = page.get("properties", {}).get(property_name, {})
    if prop.get("type") == "number":
        return prop.get("number") or 0
    return 0


def is_valid_url(url):
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def fetch_article_title(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=12)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for selector in (
                ("meta", {"property": "og:title"}),
                ("meta", {"name": "twitter:title"}),
                ("meta", {"name": "title"}),
            ):
                tag = soup.find(*selector)
                title = (tag.get("content", "") if tag else "").strip()
                if title:
                    return normalize_title(title)
            if soup.title and soup.title.string:
                return normalize_title(soup.title.string)
            h1 = soup.find("h1")
            if h1:
                return normalize_title(h1.get_text(" ", strip=True))
            return ""
        except Exception as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    log_warn(f"  ⚠️ 获取网页标题失败: {last_error}")
    return ""


def normalize_title(title):
    title = re.sub(r"\s+", " ", (title or "")).strip()
    return title[:200]


def build_title_property(property_name, property_type, title):
    title = normalize_title(title)
    if not property_name or not title:
        return {}
    value = [{"text": {"content": title[:2000]}}]
    if property_type == "title":
        return {property_name: {"title": value}}
    if property_type == "rich_text":
        return {property_name: {"rich_text": value}}
    return {}


def build_stored_property(property_name, property_type):
    if property_type == "select":
        return {property_name: {"select": {"name": VALUE_STORED}}}
    if property_type == "status":
        return {property_name: {"status": {"name": VALUE_STORED}}}
    if property_type == "checkbox":
        return {property_name: {"checkbox": True}}
    return {}


def build_failure_properties(stored_property_name, stored_property_type, failure_count_property_name, failure_count):
    properties = {}
    if failure_count_property_name:
        properties[failure_count_property_name] = {"number": failure_count}
    if failure_count >= MAX_STORE_FAILURES and stored_property_type == "select":
        properties[stored_property_name] = {"select": {"name": VALUE_FAILED}}
    elif failure_count >= MAX_STORE_FAILURES and stored_property_type == "status":
        properties[stored_property_name] = {"status": {"name": VALUE_FAILED}}
    return properties


def build_pending_filter(property_name, property_type):
    if property_type == "select":
        return {
            "and": [
                {"property": property_name, "select": {"does_not_equal": VALUE_STORED}},
                {"property": property_name, "select": {"does_not_equal": VALUE_FAILED}},
            ]
        }
    if property_type == "status":
        return {
            "and": [
                {"property": property_name, "status": {"does_not_equal": VALUE_STORED}},
                {"property": property_name, "status": {"does_not_equal": VALUE_FAILED}},
            ]
        }
    if property_type == "checkbox":
        return {"property": property_name, "checkbox": {"equals": False}}
    return None


def update_page_properties_with_retry(notion, page_id, properties, action_label):
    if not properties:
        return True
    max_retries = 3
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            notion.pages.update(page_id=page_id, properties=properties)
            return True
        except Exception as e:
            error_msg = str(e)
            if "rate limit" in error_msg.lower():
                wait_time = retry_delay * (attempt + 1) * 2
                log_warn(f"⚠️  API限流，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            elif attempt < max_retries - 1:
                log_warn(f"⚠️  {action_label}失败 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}")
                time.sleep(retry_delay)
            else:
                log_error(f"❌ {action_label}失败: {error_msg}")
                return False
    return False


def mark_as_stored(notion, page_id, title, title_property_name, title_property_type, stored_property_name, stored_property_type):
    stored_property = build_stored_property(stored_property_name, stored_property_type)
    title_property = build_title_property(title_property_name, title_property_type, title)
    properties = {**stored_property, **title_property}
    if update_page_properties_with_retry(notion, page_id, properties, "更新 Notion 状态"):
        return True

    if title_property and stored_property:
        log_warn("  ⚠️ 标题与状态合并更新失败，回退为只更新存储状态")
        return update_page_properties_with_retry(notion, page_id, stored_property, "更新 Notion 状态")
    return False


def query_pending_pages(notion, database_id, stored_property_name, stored_property_type):
    pages = []
    has_more = True
    start_cursor = None
    query_filter = build_pending_filter(stored_property_name, stored_property_type)

    while has_more:
        for attempt in range(3):
            try:
                kwargs = {
                    "database_id": database_id,
                    "start_cursor": start_cursor,
                    "page_size": NOTION_PAGE_SIZE,
                }
                if query_filter:
                    kwargs["filter"] = query_filter
                response = notion.databases.query(**kwargs)
                pages.extend(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
                break
            except Exception as e:
                error_msg = str(e)
                if "rate limit" in error_msg.lower():
                    wait_time = 4 * (attempt + 1)
                    log_warn(f"⚠️  API限流，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                elif attempt < 2:
                    log_warn(f"⚠️  查询失败 (尝试 {attempt + 1}/3): {error_msg[:100]}")
                    time.sleep(2)
                else:
                    log_error(f"❌ 查询数据库失败: {error_msg}")
                    has_more = False
                    break
    return pages


def ensure_collection_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# 收藏文章合集\n\n"
            "| 标题 | 文章链接 | 备注 |\n"
            "| --- | --- | --- |\n",
            encoding="utf-8",
        )
    return path


def escape_table_cell(text):
    text = str(text or "").replace("\r", " ").replace("\n", "<br>")
    return text.replace("|", "\\|").strip()


def extract_existing_urls(path):
    if not path.exists():
        return set()
    urls = set()
    for match in URL_RE.finditer(path.read_text(encoding="utf-8")):
        urls.add(match.group(0).rstrip("\\|"))
    return urls


def append_link_row(path, title, url):
    path = ensure_collection_file(path)
    existing_urls = extract_existing_urls(path)
    if url in existing_urls:
        return False
    row = f"| {escape_table_cell(title)} | {escape_table_cell(url)} |  |\n"
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write(row)
    return True


def main():
    log_info("\n" + "=" * 60)
    log_info("🔖 Notion链接收藏收集器")
    log_info("=" * 60)
    clean_old_logs(LOG_DIR, LOG_RETENTION_DAYS)

    try:
        notion_token, database_id = load_config()
        notion = Client(auth=notion_token)
        db_info = notion.databases.retrieve(database_id=database_id)
    except Exception as e:
        log_error(f"连接 Notion 失败: {e}")
        return 1

    properties = db_info.get("properties", {})
    title_property_name, title_property_type = pick_property(
        properties, PROPERTY_TITLE, {"title", "rich_text"}, ("标题", "名称", "Name")
    )
    url_property_name, url_property_type = pick_property(
        properties, PROPERTY_URL, {"url", "rich_text", "title"}, ("网址", "链接", "URL", "url")
    )
    stored_property_name, stored_property_type = pick_property(
        properties, PROPERTY_STORED, {"select", "status", "checkbox"}, ("存", "下载", "状态")
    )
    failure_count_property_name, failure_count_property_type = pick_property(
        properties, PROPERTY_FAILURE_COUNT, {"number"}, ("失败次数", "失败")
    )

    log_info(f"✅ 连接成功: {''.join(t.get('plain_text', '') for t in db_info.get('title', [])) or DATABASE_NAME}")
    log_info(f"   URL字段: {url_property_name} ({url_property_type or '未知'})")
    log_info(f"   标题字段: {title_property_name} ({title_property_type or '未知'})")
    log_info(f"   状态字段: {stored_property_name} ({stored_property_type or '未知'})")
    log_info(f"   失败次数字段: {failure_count_property_name} ({failure_count_property_type or '未知'})")

    if not build_stored_property(stored_property_name, stored_property_type):
        log_warn("   未找到可写状态字段，将依赖本地历史和 Markdown 去重")

    output_path = ensure_collection_file(OUTPUT_FILE)
    log_info(f"📄 输出文件: {output_path}")

    pages = query_pending_pages(notion, database_id, stored_property_name, stored_property_type)
    if not pages:
        log_info("✅ 没有待处理链接")
        return 0

    history = load_processed_links()
    success_count = 0
    skipped_count = 0
    failed_count = 0

    log_info(f"✅ 找到 {len(pages)} 条待处理记录")
    for i, page in enumerate(pages, 1):
        page_id = page.get("id", "")
        url = extract_url_from_page(page, url_property_name)
        task_key = page_id or url or f"notion_link_item_{i}"
        notion_title = extract_title_from_page(page, title_property_name)
        current_failure_count = extract_number_from_page(page, failure_count_property_name)

        log_info(f"\n[{i}/{len(pages)}] 处理链接: {url[:80] if url else '无'}")

        if not is_valid_url(url):
            skipped_count += 1
            title = notion_title or "无有效链接"
            record_processed_link(history, page_id, url, title, status="skipped_no_url")
            mark_as_stored(
                notion, page_id, title, title_property_name, title_property_type,
                stored_property_name, stored_property_type
            )
            update_task("notion_link_collection", task_key, "skipped_no_url", title=title)
            continue

        if is_processed_locally(history, page_id, url):
            skipped_count += 1
            record = get_processed_record(history, page_id, url)
            title = record.get("title") or notion_title or url
            log_warn("  本地记录显示已处理，跳过重复追加并重试标记 Notion")
            mark_as_stored(
                notion, page_id, title, title_property_name, title_property_type,
                stored_property_name, stored_property_type
            )
            update_task("notion_link_collection", task_key, "skipped_local_processed", title=title)
            continue

        try:
            update_task("notion_link_collection", task_key, "saving_link", title=notion_title or url, meta={"url": url})
            fetched_title = fetch_article_title(url)
            title = fetched_title or notion_title or url
            appended = append_link_row(output_path, title, url)
            record_processed_link(history, page_id, url, title, output_file=str(output_path))
            mark_as_stored(
                notion, page_id, title, title_property_name, title_property_type,
                stored_property_name, stored_property_type
            )
            success_count += 1
            status = "link_saved" if appended else "skipped_existing_url"
            update_task("notion_link_collection", task_key, status, title=title, meta={"path": str(output_path)})
            log_info(f"  ✅ {'已追加' if appended else '本地已有，未重复追加'}: {title[:60]}")
        except Exception as e:
            failed_count += 1
            title = notion_title or url
            next_failure_count = current_failure_count + 1
            failure_properties = build_failure_properties(
                stored_property_name,
                stored_property_type,
                failure_count_property_name,
                next_failure_count
            )
            update_page_properties_with_retry(notion, page_id, failure_properties, "更新失败次数")
            add_failed_link(url, title, e)
            update_task("notion_link_collection", task_key, "failed", title=title, error=str(e))
            log_error(f"  ❌ 处理失败: {e}")

    report = f"""
============================================================
Notion链接收藏完成
============================================================
成功处理: {success_count}
跳过: {skipped_count}
失败: {failed_count}
输出文件: {output_path}
============================================================
"""
    log_info(report)
    show_windows_notification(
        "Notion链接收藏完成",
        f"成功 {success_count}，跳过 {skipped_count}，失败 {failed_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)

