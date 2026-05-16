import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import requests

try:
    from .tingwu_common import load_cookie, post_json_with_retry, request_headers
except ImportError:  # pragma: no cover - direct script execution fallback
    from tingwu_common import load_cookie, post_json_with_retry, request_headers


DEFAULT_PAGE_SIZE = 48


def post_json(cookie: str, url: str, payload: dict) -> dict:
    return post_json_with_retry(cookie, url, payload, timeout=60)


def get_trans_status(cookie: str, trans_id: str) -> dict | None:
    payload = {
        "action": "getTransStatus",
        "version": "1.0",
        "userId": "",
        "transIds": [trans_id],
        "preview": 1,
    }
    data = post_json(cookie, "https://tingwu.aliyun.com/api/trans/request?getTransStatus&c=web", payload)
    return (data.get("data") or [None])[0]


def extract_items(data: dict) -> list[dict]:
    raw = data.get("data")
    if isinstance(raw, dict):
        return raw.get("list") or raw.get("transList") or raw.get("records") or []
    if isinstance(raw, list):
        return raw
    return []


def list_completed_trans(cookie: str, page_no: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> list[dict]:
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
    return extract_items(post_json(cookie, "https://tingwu.aliyun.com/api/trans/request?getTransList&c=web", payload))


def get_latest_completed_trans(cookie: str, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    page_no = 1
    items = []
    while True:
        page_items = list_completed_trans(cookie, page_no=page_no, page_size=page_size)
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        page_no += 1
    if not items:
        raise RuntimeError("No completed transcript was found.")
    items.sort(key=lambda x: x.get("showTime") or x.get("gmtCreate") or x.get("createTime") or 0, reverse=True)
    return items[0]


def export_trans(cookie: str, trans_id: str, user_id: int) -> str:
    payload = {
        "action": "exportTrans",
        "transIds": [trans_id],
        "userId": user_id,
        "exportDetails": [
            {
                "docType": 1,
                "fileType": 0,
                "withSpeaker": True,
                "withTimeStamp": False,
            }
        ],
    }
    data = post_json(cookie, "https://tingwu.aliyun.com/api/export/request?c=web", payload)
    return data["data"]["exportTaskId"]


def get_export_url(cookie: str, export_task_id: str, timeout_seconds: int = 120) -> str:
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        payload = {"action": "getExportStatus", "exportTaskId": export_task_id}
        data = post_json(cookie, "https://tingwu.aliyun.com/api/export/request?c=web", payload)
        last = data.get("data")
        for item in last.get("exportUrls", []) if isinstance(last, dict) else []:
            if item.get("success") and item.get("url"):
                return item["url"]
        time.sleep(1.5)
    raise TimeoutError(f"Export did not finish. Last status: {json.dumps(last, ensure_ascii=False)}")


def filename_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    disposition = query.get("response-content-disposition", [""])[0]
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if match:
        return unquote(match.group(1))
    match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    if match:
        return unquote(match.group(1))
    return f"tingwu-export-{int(time.time())}.docx"


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)


def download_file(url: str, out_dir: Path) -> Path:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / safe_filename(filename_from_url(url))
    out_path.write_bytes(response.content)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trans-id")
    parser.add_argument("--cookie-log")
    parser.add_argument("--storage-state")
    parser.add_argument("--out-dir", default=str(Path.home() / "Desktop"))
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    args = parser.parse_args()

    cookie = load_cookie(args.cookie_log, args.storage_state)
    target = get_trans_status(cookie, args.trans_id) if args.trans_id else get_latest_completed_trans(cookie, page_size=args.page_size)
    if not target:
        raise RuntimeError(f"Transcript not found: {args.trans_id}")
    if target.get("status") != 0:
        raise RuntimeError(f"Transcript {target.get('transId') or args.trans_id} is not completed. status={target.get('status')}")
    trans_id = target.get("transId") or target.get("transIdStr") or args.trans_id
    show_name = (target.get("tag") or {}).get("showName") or target.get("showName") or ""
    print("exporting", trans_id, show_name)
    export_task_id = export_trans(cookie, trans_id, target["userId"])
    print("exportTaskId", export_task_id)
    url = get_export_url(cookie, export_task_id)
    out_path = download_file(url, Path(args.out_dir))
    print("downloaded", out_path)
    print("bytes", os.path.getsize(out_path))


if __name__ == "__main__":
    main()

