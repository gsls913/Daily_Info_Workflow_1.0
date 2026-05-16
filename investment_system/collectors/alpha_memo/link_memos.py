"""
Download AlphaPai memo detail pages from one or more shared links.

This micro-program reuses the regular AlphaPai batch downloader's Markdown
format and AI post-processing pipeline.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from investment_system.collectors.alpha_memo.alphapai_download import (  # noqa: E402
    DETAIL_API,
    RECORD_CONVERT_DETAIL_API,
    auto_login_local,
    get_headers,
    get_token_local,
    save_markdown,
)
from investment_system.common.config.config_loader import get as cfg  # noqa: E402
from investment_system.common.runtime.last_downloads import CATEGORY_ALPHA_MEMO, record_markdown  # noqa: E402
from investment_system.common.utils.paths import MEMO_BASE_DIR  # noqa: E402


DEFAULT_OUTPUT_DIR = Path(MEMO_BASE_DIR) / "0-Inbox"


def parse_alpha_memo_url(url: str) -> dict[str, str]:
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query)
    if "/reading/self-summary-detail" in parsed.path:
        memo_id = (query.get("id") or [""])[0].strip()
        if not memo_id:
            raise ValueError(f"未在链接中找到 id 参数: {url}")
        return {"id": memo_id, "api_type": "record_convert", "url": url.strip()}
    if "/reading/home/meeting/detail" in parsed.path:
        memo_id = (query.get("articleId") or [""])[0].strip()
        if not memo_id:
            raise ValueError(f"未在链接中找到 articleId 参数: {url}")
        return {"id": memo_id, "api_type": "standard", "url": url.strip()}
    raise ValueError(f"不是支持的 AlphaPai 纪要详情链接: {url}")


def extract_urls(raw_items: list[str]) -> list[str]:
    urls: list[str] = []
    for item in raw_items:
        for url in re.findall(r"https?://[^\s,，]+", item):
            cleaned = url.strip().rstrip("，,。")
            try:
                parse_alpha_memo_url(cleaned)
            except ValueError:
                continue
            if cleaned and cleaned not in urls:
                urls.append(cleaned)
    return urls


def _request_detail(headers: dict, parsed_url: dict[str, str], max_retries: int = 4) -> dict | None:
    api_type = parsed_url["api_type"]
    memo_id = parsed_url["id"]
    endpoint = RECORD_CONVERT_DETAIL_API if api_type == "record_convert" else DETAIL_API
    param_name = "taskId" if api_type == "record_convert" else "id"

    for retry in range(max_retries + 1):
        try:
            response = requests.get(endpoint, headers=headers, params={param_name: memo_id}, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200000:
                detail = data.get("data")
                if detail and (detail.get("aiSummary") or detail.get("mtSummary")):
                    return detail
                print(f"  详情数据暂未生成，等待 30 秒后重试 ({retry + 1}/{max_retries + 1})...")
                time.sleep(30)
                continue
            message = str(data.get("message", ""))
            if data.get("code") == 401001 or "token" in message.lower():
                print("  Token 过期，尝试重新登录...")
                token_data = auto_login_local()
                if token_data:
                    headers.update(get_headers(token_data))
                continue
            if "请求频繁" in message or "rate limit" in message.lower():
                print(f"  请求频繁，等待 30 秒后重试 ({retry + 1}/{max_retries + 1})...")
                time.sleep(30)
                continue
            print(f"  API 返回错误: {message or data}")
            return None
        except requests.exceptions.Timeout:
            print(f"  请求超时，重试 {retry + 1}/{max_retries + 1}")
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                print("  Token 过期，尝试重新登录...")
                token_data = auto_login_local()
                if token_data:
                    headers.update(get_headers(token_data))
                    continue
            print(f"  获取详情失败: {exc}")
        except Exception as exc:
            print(f"  获取详情失败: {exc}")
        time.sleep(1)
    return None


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _meeting_from_detail(parsed_url: dict[str, str], detail: dict) -> dict:
    mt_summary = detail.get("mtSummary") or {}
    title = _first_text(
        detail.get("title"),
        detail.get("roadshowTitle"),
        detail.get("summaryTitle"),
        detail.get("meetingTitle"),
        mt_summary.get("title"),
        mt_summary.get("name"),
        "AlphaPai纪要",
    )
    return {
        "id": parsed_url["id"],
        "title": title,
        "publishTime": _first_text(detail.get("roadshowDate"), detail.get("publishTime")),
        "roadshowDate": _first_text(detail.get("roadshowDate"), detail.get("publishTime")),
        "institutionName": _first_text(detail.get("publishInstitution"), detail.get("institutionName")),
        "analystName": _first_text(detail.get("analyst"), detail.get("analystName")),
        "guestNames": _first_text(detail.get("guest"), detail.get("guestNames")),
    }


def _expected_markdown_path(meeting: dict, detail: dict, output_dir: Path, api_type: str) -> Path:
    title = meeting.get("title", "未知标题")
    date_raw = (meeting.get("publishTime", "") or "")[:10]
    if not date_raw and detail:
        date_raw = (detail.get("roadshowDate", "") or "")[:10]
    if not date_raw and meeting.get("roadshowDate"):
        date_raw = (meeting.get("roadshowDate", "") or "")[:10]

    if api_type == "record_convert":
        from investment_system.collectors.alpha_memo.alphapai_download import process_record_convert_filename

        filename_base = process_record_convert_filename(title, date_raw)
        safe_title = "".join(c for c in filename_base if c not in r'\/:?"<>|')
        filename = f"{safe_title}.md"
    else:
        safe_title = "".join(c for c in title if c not in r'\/:?"<>|')
        date_formatted = date_raw.replace("-", "") if date_raw else ""
        filename = f"{date_formatted}_{safe_title}.md"
    return output_dir / filename


def _needs_ai_analysis(path: Path) -> bool:
    try:
        return "# AI 评价" not in path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False


def download_alpha_memo_links(
    urls: list[str],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    run_ai: bool = True,
) -> list[Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    token_data = get_token_local()
    if not token_data:
        raise RuntimeError("无法获取 AlphaPai token")
    headers = get_headers(token_data)

    saved_files: list[Path] = []
    ai_target_files: list[Path] = []
    parsed_urls = [parse_alpha_memo_url(url) for url in urls]

    print(f"即将下载 {len(parsed_urls)} 个 AlphaPai 纪要链接")
    print(f"保存目录: {output_path}")
    for idx, parsed in enumerate(parsed_urls, 1):
        print(f"\n[{idx}/{len(parsed_urls)}] {parsed['url']}")
        detail = _request_detail(headers, parsed)
        if not detail:
            print("  跳过: 无法获取详情")
            continue
        meeting = _meeting_from_detail(parsed, detail)
        saved = save_markdown(meeting, detail, str(output_path), parsed["api_type"])
        if not saved:
            existing_path = _expected_markdown_path(meeting, detail, output_path, parsed["api_type"])
            if existing_path.exists() and _needs_ai_analysis(existing_path):
                print("  文件已存在，将尝试补充 AI 标签和评价")
                ai_target_files.append(existing_path)
            else:
                print("  跳过: 文件已存在或保存失败")
            continue
        saved_path = Path(saved)
        saved_files.append(saved_path)
        ai_target_files.append(saved_path)
        record_markdown(
            CATEGORY_ALPHA_MEMO,
            saved_path,
            title=meeting.get("title", saved_path.stem),
            meta={"source": "alpha_link_micro_program", "url": parsed["url"]},
        )

    if run_ai and ai_target_files:
        print(f"\n开始对 {len(ai_target_files)} 篇纪要生成标签和 AI 评价...")
        from investment_system.common.ai.aicontent_generator import generate_tags_and_analysis_for_batch_parallel

        def tag_log(message: str, level: str = "INFO"):
            prefix = {"INFO": "  ℹ", "WARN": "  !", "ERROR": "  x"}.get(level, "  ℹ")
            print(f"{prefix} {message}")

        generate_tags_and_analysis_for_batch_parallel(ai_target_files, log_func=tag_log)

    return saved_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Download AlphaPai memo links to Markdown.")
    parser.add_argument("urls", nargs="*", help="AlphaPai memo detail URLs")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="Markdown output directory")
    parser.add_argument("--skip-ai", action="store_true", help="Only save Markdown, skip AI tags/evaluation")
    args = parser.parse_args()

    urls = extract_urls(args.urls)
    if not urls:
        print("未提供有效 AlphaPai 纪要链接")
        return 1

    try:
        saved_files = download_alpha_memo_links(urls, args.out_dir, run_ai=not args.skip_ai)
    except Exception as exc:
        print(f"AlphaPai 链接下载失败: {exc}")
        return 1

    print(f"\n完成：新增保存 {len(saved_files)} 篇 Markdown")
    for path in saved_files:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

