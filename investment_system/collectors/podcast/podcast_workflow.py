"""
小宇宙播客工作流
================

功能：
1. 归档/清理 Obsidian 中已读的小宇宙笔记
2. 从通义听悟下载已完成但未处理过的转录稿
3. 调用项目统一 AI 客户端梳理播客要点
4. 保存 Markdown 到 Obsidian
5. 拉取配置的小宇宙账号新节目，下载音频并上传到通义听悟转录
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
TINGWU_DIR = SCRIPT_DIR / "tingwu_python_workflow"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(TINGWU_DIR) not in sys.path:
    sys.path.insert(0, str(TINGWU_DIR))

from investment_system.common.ai.ai_client import create_ai_client
from investment_system.common.config.config_loader import get as cfg
from investment_system.common.config.source_config import load_podcast_accounts
from investment_system.common.ai.quality import basic_ai_response_ok
from investment_system.common.markdown_utils import normalize_markdown_output
from investment_system.common.runtime.task_state import update_task
from investment_system.common.runtime.last_downloads import CATEGORY_PODCAST, record_markdown
from investment_system.common.runtime.recycle_bin import move_to_recycle_bin
from investment_system.common.storage.download_history import load_download_history, save_json_atomic
from investment_system.common.utils.logging_config import clean_old_logs, get_log_functions, setup_logging
from investment_system.common.utils.notifications import show_windows_notification
from investment_system.common.utils.paths import PODCAST_BASE_DIR, PODCAST_HISTORY_FILE, PROJECT_ROOT as ROOT_FROM_PATHS, CREDENTIALS_DIR
from investment_system.collectors.podcast.xiaoyuzhou_parser import Episode, Podcast, fetch_podcast_info

from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_api_upload import find_ffprobe, upload_one, validate_media_file
from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_common import DEFAULT_STORAGE_STATE, load_cookie, post_json_with_retry, request_headers
from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_delete_record import DEFAULT_PAGE_SIZE as TINGWU_LIST_DEFAULT_PAGE_SIZE
from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_delete_record import delete_trans, find_trans_by_id, get_trans_status as get_delete_trans_status
from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_export_download import download_file, export_trans, get_export_url, get_trans_status


LOG_DIR = SCRIPT_DIR / "logs"
logger = setup_logging(str(LOG_DIR), log_prefix="podcast_workflow", retention_days=cfg("retention.log_days", 30))
log_info, log_warn, log_error = get_log_functions(logger)

PODCAST_UNREAD_DIR = Path(PODCAST_BASE_DIR) / cfg("podcast.unread_folder_name", "未读")
PODCAST_READ_DIR = Path(PODCAST_BASE_DIR) / cfg("podcast.read_folder_name", "已读")
PODCAST_AUDIO_DIR = PROJECT_ROOT / cfg("podcast.audio_dir", "data\\podcast\\audio")
PODCAST_TRANSCRIPT_DIR = PROJECT_ROOT / cfg("podcast.transcript_dir", "data\\podcast\\transcripts")
RAW_DOCX_DIR = PODCAST_TRANSCRIPT_DIR / "docx"
RAW_TEXT_DIR = PODCAST_TRANSCRIPT_DIR / "txt"

COMPLETED_PAGE_SIZE = cfg("podcast.completed_transcript_page_size", 48)
COMPLETED_MAX_PAGES = cfg("podcast.completed_transcript_max_pages", 0)
MAX_COMPLETED_TRANSCRIPTS_PER_RUN = cfg("podcast.max_completed_transcripts_per_run", 5)
NEW_ACCOUNT_DOWNLOAD_COUNT = cfg("podcast.new_account_download_count", 3)
MAX_DOWNLOAD_PER_ACCOUNT = cfg("podcast.max_download_per_account", 5)
READ_RETENTION_DAYS = cfg("podcast.read_article_days", cfg("retention.wechat_read_article_days", 30))
RAW_TRANSCRIPT_RETENTION_DAYS = cfg("podcast.raw_transcript_retention_days", 7)
AUDIO_RETENTION_DAYS = cfg("podcast.audio_retention_days", 10)
CLEANUP_RAW_TRANSCRIPTS_AFTER_PROCESS = cfg("podcast.cleanup_raw_transcripts_after_process", True)
AUTO_LOGIN_ON_COOKIE_FAILURE = cfg("podcast.auto_login_on_cookie_failure", True)
AUTO_LOGIN_WAIT_VERIFICATION_SECONDS = cfg("podcast.auto_login_wait_verification_seconds", 60)
REFRESH_TINGWU_LOGIN_EACH_RUN = cfg("podcast.refresh_tingwu_login_each_run", True)
DELETE_TINGWU_RECORD_AFTER_PROCESS = cfg("podcast.delete_tingwu_record_after_process", True)
TINGWU_LIST_PAGE_SIZE = cfg("podcast.tingwu_list_page_size", TINGWU_LIST_DEFAULT_PAGE_SIZE)
ALLOW_LOCAL_DELETE = cfg("safety.allow_local_delete", True)
ALLOW_CLOUD_DELETE = cfg("safety.allow_cloud_delete", True)
TINGWU_CREDENTIAL_FILE = Path(CREDENTIALS_DIR) / "tongyi_password.txt"
TRANSCRIPT_CHUNK_SIZE_CHARS = cfg("podcast.transcript_chunk_size_chars", 80000)
AI_SINGLE_MAX_TOKENS = cfg("podcast.ai_single_max_tokens", 12000)
AI_CHUNK_MAX_TOKENS = cfg("podcast.ai_chunk_max_tokens", 8000)
AI_SYNTHESIS_MAX_TOKENS = cfg("podcast.ai_synthesis_max_tokens", 12000)
AUDIO_FILE_SUFFIXES = (".mp3", ".wav", ".m4a", ".wma", ".aac", ".ogg", ".amr", ".flac", ".aiff")

HEADERS_FOR_AUDIO = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


PODCAST_ANALYSIS_PROMPT = """你是一位擅长整理长篇谈话内容的知识整理助手。请阅读下面这期播客的文字稿，在不预设领域立场的前提下，尽量完整、清晰、结构化地梳理内容，适合长期保存到个人知识库。

【播客信息】
标题：{title}
播客：{podcast_name}
发布日期：{pub_date}
时长：{duration}

【文字稿】
{transcript}

【输出要求】
请用 Markdown 输出，要求尽量详细、要点突出、信息密度高：

## 一、简短概要
用一段话概括这期节目的主题、讨论范围和最核心的内容。

## 二、结构化内容梳理
按节目实际内容划分主题模块，逐层整理主要观点、论证过程、故事/案例、事实信息、概念解释、人物态度和重要细节。请尽量保留有信息量的内容，不要因为追求简短而遗漏关键上下文。

## 三、重要细节与金句
整理节目中值得保留的具体表达、例子、转折、分歧、反直觉观点或有启发的表述。没有必要强行提炼金句，但不要漏掉重要细节。

请不要输出表格，不要编造文字稿中没有的信息。可以适当展开，优先保证清晰、完整和可读。"""


SYNTHESIS_PROMPT = """你是一位擅长整理长篇谈话内容的知识整理助手。下面是同一期播客文字稿分段整理后的结果，请综合为一份完整、详细、结构清晰、适合长期保存的播客笔记。

【播客信息】
标题：{title}
播客：{podcast_name}
发布日期：{pub_date}
时长：{duration}

【分段分析】
{chunk_notes}

【输出结构】
## 一、简短概要
## 二、结构化内容梳理
## 三、重要细节与金句

要求：合并重复内容，保留具体信息和上下文，不要输出表格，不要加入原文没有的信息。"""


def ensure_dirs() -> None:
    for path in [PODCAST_UNREAD_DIR, PODCAST_READ_DIR, PODCAST_AUDIO_DIR, RAW_DOCX_DIR, RAW_TEXT_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_old_files(
    directory: Path,
    days_threshold: int,
    suffixes: tuple[str, ...] | None = None,
    timestamp_getter: Any | None = None,
) -> int:
    if days_threshold < 0 or not directory.exists():
        return 0
    now = time.time()
    cutoff_seconds = days_threshold * 24 * 3600
    deleted = 0
    timestamp_getter = timestamp_getter or (lambda path: path.stat().st_mtime)
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        try:
            if now - timestamp_getter(path) > cutoff_seconds:
                path.unlink()
                deleted += 1
                log_info(f"已清理过期运行文件: {path}")
        except Exception as e:
            log_warn(f"清理运行文件失败 {path}: {e}")
    return deleted


def clean_old_podcast_runtime_files() -> int:
    if not ALLOW_LOCAL_DELETE:
        log_info("安全开关禁止本地删除，跳过播客运行文件清理")
        return 0
    deleted = 0
    deleted += clean_old_files(RAW_DOCX_DIR, RAW_TRANSCRIPT_RETENTION_DAYS, (".docx",))
    deleted += clean_old_files(RAW_TEXT_DIR, RAW_TRANSCRIPT_RETENTION_DAYS, (".txt",))
    deleted += clean_old_files(
        PODCAST_AUDIO_DIR,
        AUDIO_RETENTION_DAYS,
        AUDIO_FILE_SUFFIXES,
        timestamp_getter=lambda path: path.stat().st_ctime,
    )
    return deleted


def load_history() -> dict[str, Any]:
    history = load_download_history(PODCAST_HISTORY_FILE)
    history.setdefault("processed_transcripts", [])
    history.setdefault("uploaded_episodes", {})
    history.setdefault("uploads", {})
    return history


def save_history(history: dict[str, Any]) -> None:
    save_json_atomic(history, PODCAST_HISTORY_FILE)


def sanitize_filename(name: str, max_len: int = 90) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    safe = re.sub(r"\s+", " ", safe)
    return safe[:max_len].rstrip(" .") or "untitled"


def parse_date(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if value.isdigit():
        try:
            ts = int(value)
            if ts > 10_000_000_000:
                ts = ts // 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            pass
    return value[:10]


def format_duration(seconds: int | str | None) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "未知"
    if total <= 0:
        return "未知"
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def post_tingwu_json(cookie: str, payload: dict) -> dict:
    return post_json_with_retry(
        cookie,
        "https://tingwu.aliyun.com/api/trans/request?getTransList&c=web",
        timeout=60,
    )


def get_completed_transcripts(cookie: str, page_size: int = COMPLETED_PAGE_SIZE, max_pages: int | None = None) -> list[dict]:
    items: list[dict] = []
    page_no = 1
    while True:
        if max_pages is not None and page_no > max_pages:
            break
        payload = {
            "action": "getTransList",
            "version": "1.0",
            "userId": "",
            "filter": {
                "status": [0],
                "fileTypes": [],
                "beginTime": "",
                "mediaType": "",
                "endTime": "",
                "showName": "",
                "read": "",
                "lang": "",
                "shareUserId": "",
                "client": "",
            },
            "preview": 1,
            "pageNo": page_no,
            "pageSize": page_size,
        }
        data = post_tingwu_json(cookie, payload)
        raw = data.get("data")
        if isinstance(raw, dict):
            page_items = raw.get("list") or raw.get("transList") or raw.get("records") or []
        elif isinstance(raw, list):
            page_items = raw
        else:
            page_items = []
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        page_no += 1
    items.sort(key=lambda x: x.get("showTime") or x.get("gmtCreate") or x.get("createTime") or 0, reverse=True)
    return items


def select_unprocessed_transcripts(completed: list[dict], history: dict[str, Any]) -> list[dict]:
    processed = set(history.get("processed_transcripts", []))

    selected = []
    for item in completed:
        trans_id = item.get("transId") or item.get("transIdStr")
        if not trans_id:
            continue
        if trans_id in processed:
            continue
        selected.append(item)
    return selected


def extract_docx_text(docx_path: Path) -> str:
    paragraphs: list[str] = []
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(docx_path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    for para in root.findall(".//w:p", ns):
        texts = [node.text or "" for node in para.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def clean_ai_response(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def split_text(text: str, chunk_size: int = TRANSCRIPT_CHUNK_SIZE_CHARS) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + chunk_size // 2:
                end = newline
        chunks.append(text[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def generate_podcast_notes(transcript: str, metadata: dict[str, Any]) -> str:
    def ai_log(msg: str, level: str = "INFO") -> None:
        if level == "ERROR":
            log_error(msg)
        elif level == "WARN":
            log_warn(msg)
        else:
            log_info(msg)

    client = create_ai_client(log_func=ai_log)
    chunks = split_text(transcript)

    if len(chunks) == 1:
        prompt = PODCAST_ANALYSIS_PROMPT.format(transcript=chunks[0], **metadata)
        response, _ = client.call_for_long_thinking(
            prompt=prompt,
            temperature=0.6,
            max_tokens=AI_SINGLE_MAX_TOKENS,
            max_attempts_per_model=3,
            max_models=3,
        )
        notes = clean_ai_response(response)
        ok, reason = basic_ai_response_ok(notes, min_chars=120)
        if not ok:
            raise RuntimeError(f"播客 AI 梳理结果质量检查未通过: {reason}")
        return notes

    chunk_notes = []
    for idx, chunk in enumerate(chunks, 1):
        log_info(f"AI分段梳理 [{idx}/{len(chunks)}]")
        prompt = PODCAST_ANALYSIS_PROMPT.format(transcript=chunk, **metadata)
        response, _ = client.call_for_long_thinking(
            prompt=prompt,
            temperature=0.6,
            max_tokens=AI_CHUNK_MAX_TOKENS,
            max_attempts_per_model=3,
            max_models=3,
        )
        chunk_notes.append(f"### 分段 {idx}\n\n{clean_ai_response(response)}")
        time.sleep(1)

    synthesis_prompt = SYNTHESIS_PROMPT.format(chunk_notes="\n\n".join(chunk_notes), **metadata)
    response, _ = client.call_for_long_thinking(
        prompt=synthesis_prompt,
        temperature=0.5,
        max_tokens=AI_SYNTHESIS_MAX_TOKENS,
        max_attempts_per_model=3,
        max_models=3,
    )
    notes = clean_ai_response(response)
    ok, reason = basic_ai_response_ok(notes, min_chars=120)
    if not ok:
        raise RuntimeError(f"播客 AI 综合结果质量检查未通过: {reason}")
    return notes


def metadata_from_transcript(target: dict, history: dict[str, Any]) -> dict[str, Any]:
    trans_id = target.get("transId") or target.get("transIdStr")
    upload_meta = history.get("uploads", {}).get(trans_id or "", {})
    episode = upload_meta.get("episode", {})
    podcast = upload_meta.get("podcast", {})
    tag = target.get("tag") or {}
    title = episode.get("title") or tag.get("showName") or target.get("showName") or f"听悟转录_{trans_id}"
    return {
        "trans_id": trans_id or "",
        "title": title,
        "podcast_name": podcast.get("title") or upload_meta.get("podcast_name") or "未知播客",
        "account_name": upload_meta.get("account_name") or podcast.get("account_name") or podcast.get("title") or upload_meta.get("podcast_name") or "未知播客",
        "account_short_name": upload_meta.get("account_short_name") or "",
        "podcast_url": podcast.get("url") or "",
        "episode_url": episode.get("url") or "",
        "pub_date": parse_date(episode.get("pub_date") or target.get("showTime") or target.get("gmtCreate") or ""),
        "duration": episode.get("duration_formatted") or format_duration(episode.get("duration")),
        "audio_path": upload_meta.get("audio_path") or "",
    }


def save_podcast_markdown(notes: str, transcript: str, meta: dict[str, Any]) -> Path:
    date_prefix = (meta.get("pub_date") or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    filename = f"{date_prefix}_{sanitize_filename(meta.get('podcast_name', '播客'), 24)}_{sanitize_filename(meta.get('title', '未命名播客'), 70)}.md"
    out_path = PODCAST_UNREAD_DIR / filename
    counter = 1
    while out_path.exists():
        out_path = PODCAST_UNREAD_DIR / f"{out_path.stem}_{counter}{out_path.suffix}"
        counter += 1

    content = [
        "# 基本信息",
        "",
        f"- **节目名称**: {meta.get('title') or '未知节目'}",
        f"- **播客账号**: #{meta.get('account_name') or meta.get('podcast_name') or '未知播客'}",
        f"- **发布日期**: {meta.get('pub_date') or ''}",
        f"- **时长**: {meta.get('duration') or '未知'}",
        f"- **小宇宙节目链接**: {meta.get('episode_url') or ''}",
        "- [ ] **是否已读**",
        "- **人工标签**: ",
        "- **我的评价**: ",
        "",
        "---",
        "",
        "# AI 要点",
        "",
        notes,
        "",
        "---",
        "",
        "# 文字稿",
        "",
        transcript,
        "",
        "---",
        f"*整理时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]
    out_path.write_text(normalize_markdown_output("\n".join(content)), encoding="utf-8")
    return out_path


def delete_audio_if_known(meta: dict[str, Any]) -> bool:
    if not ALLOW_LOCAL_DELETE:
        log_info("安全开关禁止本地删除，保留本地音频")
        return False
    audio_path = meta.get("audio_path")
    if not audio_path:
        return False
    path = Path(audio_path)
    try:
        resolved = path.resolve()
        audio_root = PODCAST_AUDIO_DIR.resolve()
        if path.exists() and str(resolved).lower().startswith(str(audio_root).lower()):
            path.unlink()
            log_info(f"已删除本地音频: {path}")
            return True
    except Exception as e:
        log_warn(f"删除本地音频失败 {path}: {e}")
    return False


def delete_transcript_artifacts(*paths: Path) -> int:
    if not ALLOW_LOCAL_DELETE:
        log_info("安全开关禁止本地删除，保留原始转录文件")
        return 0
    deleted = 0
    for path in paths:
        try:
            resolved = path.resolve()
            allowed_roots = [RAW_DOCX_DIR.resolve(), RAW_TEXT_DIR.resolve()]
            if path.exists() and any(str(resolved).lower().startswith(str(root).lower()) for root in allowed_roots):
                path.unlink()
                deleted += 1
                log_info(f"已删除原始转录文件: {path}")
        except Exception as e:
            log_warn(f"删除原始转录文件失败 {path}: {e}")
    return deleted


def delete_tingwu_record_after_success(cookie: str, trans_id: str) -> bool:
    if not DELETE_TINGWU_RECORD_AFTER_PROCESS:
        return False
    if not ALLOW_CLOUD_DELETE:
        log_info("安全开关禁止云端删除，保留听悟转录记录")
        return False
    try:
        record = find_trans_by_id(cookie, trans_id, page_size=TINGWU_LIST_PAGE_SIZE)
        if not record:
            log_warn(f"未在听悟列表中找到待删除转录，已跳过云端删除: {trans_id}")
            return False
        result = delete_trans(cookie, record)
        log_info(f"已删除通义听悟云端转录: {trans_id} status=回收站")
        try:
            after = get_delete_trans_status(cookie, trans_id)
            if after:
                log_info(f"听悟删除后状态: transId={trans_id}, status={after.get('status')}")
        except Exception as check_error:
            log_warn(f"听悟删除后状态校验失败: {trans_id}, {check_error}")
        return bool(result.get("success", True))
    except Exception as e:
        log_error(f"删除通义听悟云端转录失败，已跳过: {trans_id}, {e}")
        return False


def export_and_process_transcript(cookie: str, target: dict, history: dict[str, Any]) -> bool:
    trans_id = target.get("transId") or target.get("transIdStr")
    if not trans_id:
        return False
    if target.get("status") != 0:
        log_warn(f"跳过未完成转录: {trans_id} status={target.get('status')}")
        return False

    log_info(f"导出听悟转录: {trans_id}")
    update_task("podcast_transcript", trans_id, "exporting", title=(target.get("tag") or {}).get("showName", ""))
    latest = get_trans_status(cookie, trans_id)
    if latest and latest.get("status") != 0:
        log_warn(f"转录尚未完成: {trans_id} status={latest.get('status')}")
        return False
    actual = latest or target
    user_id = actual.get("userId") or target.get("userId")
    if not user_id:
        raise RuntimeError(f"无法获取听悟 userId: {trans_id}")

    task_id = export_trans(cookie, trans_id, user_id)
    export_url = get_export_url(cookie, task_id)
    docx_path = download_file(export_url, RAW_DOCX_DIR)
    transcript = extract_docx_text(docx_path)
    if not transcript:
        raise RuntimeError(f"导出的转录稿为空: {docx_path}")
    txt_path = RAW_TEXT_DIR / f"{sanitize_filename(trans_id)}.txt"
    txt_path.write_text(transcript, encoding="utf-8")

    meta = metadata_from_transcript(actual, history)
    update_task("podcast_transcript", trans_id, "transcript_downloaded", title=meta.get("title", ""), meta={"docx_path": str(docx_path), "txt_path": str(txt_path)})
    update_task("podcast_transcript", trans_id, "ai_processing", title=meta.get("title", ""))
    notes = generate_podcast_notes(transcript, meta)
    md_path = save_podcast_markdown(notes, transcript, meta)
    update_task("podcast_transcript", trans_id, "markdown_saved", title=meta.get("title", ""), meta={"md_path": str(md_path)})
    record_markdown(
        CATEGORY_PODCAST,
        md_path,
        title=meta.get("title", ""),
        meta={"trans_id": trans_id, "podcast_name": meta.get("podcast_name", "")},
    )

    processed = history.setdefault("processed_transcripts", [])
    if trans_id not in processed:
        processed.insert(0, trans_id)
    history.setdefault("processed_notes", {})[trans_id] = {
        "md_path": str(md_path),
        "docx_path": str(docx_path),
        "txt_path": str(txt_path),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }
    delete_audio_if_known(meta)
    save_history(history)
    if CLEANUP_RAW_TRANSCRIPTS_AFTER_PROCESS:
        delete_transcript_artifacts(docx_path, txt_path)
    delete_tingwu_record_after_success(cookie, trans_id)
    log_info(f"已保存播客笔记: {md_path}")
    return True


def archive_read_notes() -> int:
    moved = 0
    for md_file in PODCAST_UNREAD_DIR.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            log_warn(f"读取播客笔记失败 {md_file}: {e}")
            continue
        if "- [x] **是否已读**" not in content:
            continue
        dest = PODCAST_READ_DIR / md_file.name
        counter = 1
        while dest.exists():
            dest = PODCAST_READ_DIR / f"{md_file.stem}_{counter}{md_file.suffix}"
            counter += 1
        shutil.move(str(md_file), str(dest))
        moved += 1
        log_info(f"已归档播客笔记: {md_file.name}")
    return moved


def clean_old_read_notes(days_threshold: int = READ_RETENTION_DAYS) -> int:
    if not ALLOW_LOCAL_DELETE:
        log_info("安全开关禁止本地删除，跳过已读播客过期清理")
        return 0
    deleted = 0
    now = datetime.now()
    for md_file in PODCAST_READ_DIR.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        match = re.search(r"-\s*\*\*发布日期\*\*:\s*(\d{4}-\d{2}-\d{2})", content)
        if not match:
            continue
        try:
            pub_date = datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if (now - pub_date).days > days_threshold:
            try:
                move_to_recycle_bin(md_file, category="podcast", item_type="markdown")
                deleted += 1
                log_info(f"已移入回收站过期播客笔记: {md_file.name}")
            except Exception as e:
                log_warn(f"过期播客笔记移入回收站失败 {md_file.name}: {e}")
    return deleted


def episode_url(episode: Episode) -> str:
    return f"https://www.xiaoyuzhoufm.com/episode/{episode.eid}"


def normalize_account(raw: dict[str, Any]) -> dict[str, str]:
    if isinstance(raw, str):
        return {"url": raw, "name": "", "short_name": ""}
    return {
        "url": str(raw.get("url", "")).strip(),
        "name": str(raw.get("name", "")).strip(),
        "short_name": str(raw.get("short_name", raw.get("name", ""))).strip(),
    }


def select_new_episodes(podcast: Podcast, history: dict[str, Any], max_episodes_override: int | None = None) -> list[Episode]:
    uploaded_by_podcast = history.setdefault("uploaded_episodes", {})
    uploaded = set(uploaded_by_podcast.get(podcast.pid, []))
    if max_episodes_override is not None:
        selected = []
        for episode in podcast.episodes:
            if episode.eid not in uploaded:
                selected.append(episode)
            if len(selected) >= max_episodes_override:
                break
        return selected
    if not uploaded:
        return podcast.episodes[:NEW_ACCOUNT_DOWNLOAD_COUNT]
    selected = []
    for episode in podcast.episodes:
        if episode.eid in uploaded:
            break
        selected.append(episode)
        if len(selected) >= MAX_DOWNLOAD_PER_ACCOUNT:
            log_info(f"{podcast.title}: 达到本轮新节目上传上限 {MAX_DOWNLOAD_PER_ACCOUNT} 期")
            break
    return selected


def audio_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix and len(suffix) <= 8 else ".m4a"


def download_episode_audio(podcast: Podcast, episode: Episode, account: dict[str, str]) -> Path:
    if not episode.audio_url:
        raise RuntimeError(f"节目没有音频链接: {episode.title}")
    pub_date = parse_date(episode.pub_date).replace("-", "") or datetime.now().strftime("%Y%m%d")
    short_name = account.get("short_name") or podcast.title
    filename = f"{pub_date}_{sanitize_filename(short_name, 24)}_{sanitize_filename(episode.title, 80)}_{episode.eid}{audio_extension(episode.audio_url)}"
    out_path = PODCAST_AUDIO_DIR / filename
    if out_path.exists() and out_path.stat().st_size > 0:
        log_info(f"音频已存在，跳过下载: {out_path.name}")
        return out_path

    log_info(f"下载音频: {episode.title}")
    with requests.get(episode.audio_url, headers=HEADERS_FOR_AUDIO, stream=True, timeout=120) as response:
        response.raise_for_status()
        temp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        with temp_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(temp_path, out_path)
    return out_path


def upload_episode(cookie: str, podcast: Podcast, episode: Episode, account: dict[str, str], audio_path: Path, history: dict[str, Any]) -> bool:
    update_task("podcast_episode", episode.eid, "uploading", title=episode.title, meta={"podcast": podcast.title, "audio_path": str(audio_path)})
    ffprobe = find_ffprobe()
    validate_media_file(audio_path, ffprobe, require_duration=False)
    result = upload_one(cookie, audio_path)
    trans_id = result.get("transId")
    if not trans_id:
        raise RuntimeError(f"上传成功但没有 transId: {audio_path}")

    uploaded = history.setdefault("uploaded_episodes", {}).setdefault(podcast.pid, [])
    if episode.eid not in uploaded:
        uploaded.insert(0, episode.eid)

    history.setdefault("uploads", {})[trans_id] = {
        "audio_path": str(audio_path),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "podcast_name": podcast.title,
        "account_name": account.get("name") or podcast.title,
        "account_short_name": account.get("short_name") or "",
        "podcast": {
            "pid": podcast.pid,
            "title": podcast.title,
            "account_name": account.get("name") or podcast.title,
            "account_short_name": account.get("short_name") or "",
            "author": podcast.author,
            "url": account.get("url", ""),
        },
        "episode": {
            "eid": episode.eid,
            "title": episode.title,
            "pub_date": parse_date(episode.pub_date),
            "duration": episode.duration,
            "duration_formatted": episode.duration_formatted,
            "url": episode_url(episode),
            "description": episode.description,
        },
        "tingwu": result,
    }
    save_history(history)
    log_info(f"已上传听悟转录任务: {episode.title} transId={trans_id}")
    update_task("podcast_episode", episode.eid, "uploaded_to_tingwu", title=episode.title, meta={"podcast": podcast.title, "trans_id": trans_id})
    return True


def process_completed_transcripts(
    cookie: str,
    history: dict[str, Any],
    max_pages: int | None = None,
    max_items: int | None = MAX_COMPLETED_TRANSCRIPTS_PER_RUN,
) -> int:
    completed = get_completed_transcripts(cookie, max_pages=max_pages)
    targets = select_unprocessed_transcripts(completed, history)
    if not targets:
        log_info("没有新的已完成听悟转录需要处理")
        return 0
    if max_items is not None and max_items > 0 and len(targets) > max_items:
        log_warn(f"已完成转录积压 {len(targets)} 个，本轮只处理前 {max_items} 个；其余留到下次运行")
        targets = targets[:max_items]
    count = 0
    for target in targets:
        trans_id = target.get("transId") or target.get("transIdStr") or "unknown"
        try:
            if export_and_process_transcript(cookie, target, history):
                count += 1
        except Exception as e:
            log_error(f"处理听悟转录失败，已跳过: {trans_id}, {e}")
    return count


def mark_completed_transcripts_processed(cookie: str, history: dict[str, Any], max_pages: int | None = None) -> int:
    completed = get_completed_transcripts(cookie, max_pages=max_pages)
    processed = history.setdefault("processed_transcripts", [])
    existing = set(processed)
    added = 0
    for item in completed:
        trans_id = item.get("transId") or item.get("transIdStr")
        if not trans_id or trans_id in existing:
            continue
        processed.insert(0, trans_id)
        existing.add(trans_id)
        added += 1
    if added:
        history["marked_completed_processed_at"] = datetime.now().isoformat(timespec="seconds")
        save_history(history)
    log_info(f"已标记 {added} 个听悟已完成转录为已处理，不生成 Markdown")
    return added


def upload_new_podcast_episodes(
    cookie: str,
    history: dict[str, Any],
    limit_accounts: int | None = None,
    accounts_override: list[dict[str, str]] | None = None,
    max_episodes_override: int | None = None,
) -> int:
    accounts = [normalize_account(item) for item in (accounts_override or load_podcast_accounts())]
    accounts = [item for item in accounts if item.get("url")]
    if limit_accounts:
        accounts = accounts[:limit_accounts]
    if not accounts:
        log_warn("未配置小宇宙播客账号，请在 data/config/set_config.xlsx 的 podcast_account sheet 中添加")
        return 0

    uploaded_count = 0
    for idx, account in enumerate(accounts, 1):
        log_info(f"[{idx}/{len(accounts)}] 获取小宇宙播客: {account['url']}")
        podcast = fetch_podcast_info(account["url"])
        if not podcast:
            log_warn(f"解析小宇宙播客失败: {account['url']}")
            continue
        episodes = select_new_episodes(podcast, history, max_episodes_override=max_episodes_override)
        log_info(f"{podcast.title}: 待上传 {len(episodes)} 期")
        for episode in episodes:
            try:
                update_task("podcast_episode", episode.eid, "audio_downloading", title=episode.title, meta={"podcast": podcast.title})
                audio_path = download_episode_audio(podcast, episode, account)
                update_task("podcast_episode", episode.eid, "audio_downloaded", title=episode.title, meta={"podcast": podcast.title, "audio_path": str(audio_path)})
                upload_episode(cookie, podcast, episode, account, audio_path, history)
                uploaded_count += 1
                time.sleep(1)
            except Exception as e:
                log_error(f"处理小宇宙节目失败，已跳过: {podcast.title} - {episode.title}: {e}")
                update_task("podcast_episode", episode.eid, "failed", title=episode.title, meta={"podcast": podcast.title}, error=str(e))
    return uploaded_count


def run_tingwu_auto_login(storage_state: str, wait_verification_seconds: int = AUTO_LOGIN_WAIT_VERIFICATION_SECONDS) -> bool:
    cmd = [
        sys.executable,
        str(TINGWU_DIR / "tingwu_profile.py"),
        "--storage-state",
        storage_state,
        "auto-login",
        "--credential-file",
        str(TINGWU_CREDENTIAL_FILE),
        "--wait-verification-seconds",
        str(wait_verification_seconds),
    ]
    log_info("通义听悟登录状态不可用，尝试自动账号密码登录...")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(TINGWU_DIR),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(wait_verification_seconds + 120, 180),
        )
    except Exception as e:
        log_error(f"自动登录脚本执行失败: {e}")
        return False

    output = (result.stdout or "").strip()
    if output:
        for line in output.splitlines()[-8:]:
            log_info(f"听悟登录: {line}")
    if result.returncode != 0:
        log_error(f"自动登录未成功，退出码: {result.returncode}")
        return False
    return True


def ensure_tingwu_cookie(storage_state: str | None = None, refresh_login: bool = False) -> str:
    state_path = storage_state or str(DEFAULT_STORAGE_STATE)
    if refresh_login:
        if run_tingwu_auto_login(state_path):
            log_info("已刷新通义听悟登录状态")
        else:
            log_warn("刷新通义听悟登录状态失败，将尝试使用现有登录态继续")
    try:
        cookie = load_cookie(storage_state=state_path)
        get_completed_transcripts(cookie, page_size=1, max_pages=1)
        return cookie
    except Exception as first_error:
        if not AUTO_LOGIN_ON_COOKIE_FAILURE:
            raise
        log_warn(f"通义听悟登录状态检查失败: {first_error}")
        if not run_tingwu_auto_login(state_path):
            raise
        cookie = load_cookie(storage_state=state_path)
        get_completed_transcripts(cookie, page_size=1, max_pages=1)
        return cookie


def main() -> None:
    parser = argparse.ArgumentParser(description="小宇宙播客下载、听悟转录、AI要点整理工作流")
    parser.add_argument("--phase", choices=["all", "archive", "process-completed", "upload-new", "mark-completed-processed"], default="all")
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE))
    parser.add_argument("--max-completed-pages", type=int, default=COMPLETED_MAX_PAGES, help="扫描听悟已完成转录的最大页数；0 表示扫描到最后一页")
    parser.add_argument("--max-completed-items", type=int, default=MAX_COMPLETED_TRANSCRIPTS_PER_RUN, help="本轮最多整理多少个已完成转录；0 表示不限制")
    parser.add_argument("--limit-accounts", type=int)
    parser.add_argument("--single-podcast-url", default=None, help="只上传单个小宇宙播客主页 URL，例如 https://www.xiaoyuzhoufm.com/podcast/xxxx")
    parser.add_argument("--single-podcast-name", default="", help="单播客模式下显示名称，可选")
    parser.add_argument("--single-podcast-short-name", default="", help="单播客模式下文件名简称，可选")
    parser.add_argument("--count", type=int, default=None, help="单播客模式下最多上传靠前的 N 期；不填则按常规上限")
    parser.add_argument("--all", action="store_true", help="单播客模式下上传所有能抓到且未上传的节目")
    args = parser.parse_args()

    ensure_dirs()
    clean_old_logs(str(LOG_DIR), cfg("retention.log_days", 30))
    clean_old_podcast_runtime_files()
    history = load_history()

    archived = 0
    deleted = 0
    processed = 0
    uploaded = 0

    if args.phase in ("all", "archive"):
        archived = archive_read_notes()
        deleted = clean_old_read_notes()

    if args.phase in ("all", "process-completed", "upload-new", "mark-completed-processed"):
        try:
            cookie = ensure_tingwu_cookie(args.storage_state, refresh_login=REFRESH_TINGWU_LOGIN_EACH_RUN)
        except Exception as e:
            log_error(f"通义听悟登录状态不可用: {e}")
            log_error("自动登录未能恢复。可手动运行: python investment_system.collectors.podcast\\tingwu_python_workflow\\tingwu_profile.py auto-login --headed --wait-verification-seconds=60")
            raise SystemExit(1)

        if args.phase in ("all", "process-completed"):
            max_completed_pages = args.max_completed_pages if args.max_completed_pages > 0 else None
            max_completed_items = args.max_completed_items if args.max_completed_items > 0 else None
            processed = process_completed_transcripts(cookie, history, max_pages=max_completed_pages, max_items=max_completed_items)

        if args.phase == "mark-completed-processed":
            max_completed_pages = args.max_completed_pages if args.max_completed_pages > 0 else None
            processed = mark_completed_transcripts_processed(cookie, history, max_pages=max_completed_pages)

        if args.phase in ("all", "upload-new"):
            accounts_override = None
            if args.single_podcast_url:
                accounts_override = [{
                    "url": args.single_podcast_url,
                    "name": args.single_podcast_name,
                    "short_name": args.single_podcast_short_name,
                }]
            uploaded = upload_new_podcast_episodes(
                cookie,
                history,
                limit_accounts=args.limit_accounts,
                accounts_override=accounts_override,
                max_episodes_override=(
                    10 ** 9 if args.single_podcast_url and args.all
                    else args.count if args.single_podcast_url
                    else None
                ),
            )

    log_info("=" * 60)
    log_info(f"小宇宙播客工作流完成: 归档 {archived}, 删除过期 {deleted}, 整理转录 {processed}, 上传新节目 {uploaded}")
    show_windows_notification(
        "小宇宙播客工作流完成",
        f"整理转录 {processed} 个，上传新节目 {uploaded} 个"
    )


if __name__ == "__main__":
    main()

