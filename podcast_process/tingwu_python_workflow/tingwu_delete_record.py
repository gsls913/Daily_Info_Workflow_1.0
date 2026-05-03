import argparse
import json
import sys

import requests

from tingwu_common import load_cookie, request_headers


LIST_URL = "https://tingwu.aliyun.com/api/trans/request?getTransList&c=web"
STATUS_URL = "https://tingwu.aliyun.com/api/trans/request?getTransStatus&c=web"
DELETE_URL = "https://tingwu.aliyun.com/api/trans/request?delTrans&c=web"
DEFAULT_STATUSES = [0, 1, 2, 3, 4, 11]
DEFAULT_PAGE_SIZE = 48


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def post_json(cookie: str, url: str, payload: dict) -> dict:
    response = requests.post(url, headers=request_headers(cookie), json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"{payload.get('action')} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def simplify_record(record: dict) -> dict:
    tag = record.get("tag") or {}
    return {
        "transId": record.get("transId") or record.get("transIdStr"),
        "userId": record.get("userId"),
        "status": record.get("status"),
        "showTime": record.get("showTime"),
        "gmtCreate": record.get("gmtCreate"),
        "name": tag.get("showName") or record.get("showName") or record.get("title") or "",
    }


def extract_items(data: dict) -> list[dict]:
    raw = data.get("data")
    if isinstance(raw, dict):
        return raw.get("list") or raw.get("transList") or raw.get("records") or []
    if isinstance(raw, list):
        return raw
    return []


def list_trans(cookie: str, page_no: int = 1, page_size: int = DEFAULT_PAGE_SIZE, statuses: list[int] | None = None) -> list[dict]:
    payload = {
        "action": "getTransList",
        "version": "1.0",
        "userId": "",
        "filter": {
            "status": statuses or DEFAULT_STATUSES,
            "fileTypes": [],
            "beginTime": "",
            "mediaType": "",
            "endTime": "",
            "showName": "",
            "read": "",
            "lang": "",
            "shareUserId": "",
            "client": "",
            "dirId": 0,
        },
        "orderType": 0,
        "orderDesc": True,
        "preview": 1,
        "pageNo": page_no,
        "pageSize": page_size,
    }
    return extract_items(post_json(cookie, LIST_URL, payload))


def get_trans_status(cookie: str, trans_id: str) -> dict | None:
    payload = {
        "action": "getTransStatus",
        "version": "1.0",
        "userId": "",
        "transIds": [trans_id],
        "preview": 1,
    }
    data = post_json(cookie, STATUS_URL, payload)
    return (data.get("data") or [None])[0]


def find_trans_by_id(cookie: str, trans_id: str, page_size: int = DEFAULT_PAGE_SIZE, statuses: list[int] | None = None, max_pages: int | None = None) -> dict | None:
    page_no = 1
    while True:
        if max_pages is not None and page_no > max_pages:
            return None
        records = list_trans(cookie, page_no=page_no, page_size=page_size, statuses=statuses)
        if not records:
            return None
        for record in records:
            if (record.get("transId") or record.get("transIdStr")) == trans_id:
                return simplify_record(record)
        if len(records) < page_size:
            return None
        page_no += 1


def delete_trans(cookie: str, record: dict) -> dict:
    payload = {
        "action": "delTrans",
        "version": "1.0",
        "userId": record.get("userId") or "",
        "transIds": [record["transId"]],
    }
    return post_json(cookie, DELETE_URL, payload)


def resolve_target(cookie: str, args: argparse.Namespace) -> dict:
    if args.trans_id:
        record = find_trans_by_id(cookie, args.trans_id, page_size=args.page_size)
        if not record:
            raw = get_trans_status(cookie, args.trans_id)
            record = simplify_record(raw) if raw else None
        if not record:
            raise RuntimeError(f"Transcript not found: {args.trans_id}")
        return record
    records = [simplify_record(record) for record in list_trans(cookie, args.page_no, args.page_size)]
    if not records:
        raise RuntimeError("No Tingwu records found.")
    return records[args.index - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description="List or delete Tongyi Tingwu transcript records.")
    parser.add_argument("--cookie-log")
    parser.add_argument("--storage-state")
    parser.add_argument("--list", action="store_true", help="List recent records and exit.")
    parser.add_argument("--page-no", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--index", type=int, default=1)
    parser.add_argument("--trans-id")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    if args.page_no < 1:
        raise ValueError("--page-no must be 1 or greater")
    if args.index < 1:
        raise ValueError("--index must be 1 or greater")
    if args.page_size < args.index:
        args.page_size = args.index

    cookie = load_cookie(args.cookie_log, args.storage_state)
    if args.list:
        print(json.dumps([simplify_record(record) for record in list_trans(cookie, args.page_no, args.page_size)], ensure_ascii=False, indent=2))
        return

    target = resolve_target(cookie, args)
    print("target", json.dumps(target, ensure_ascii=False))
    if not args.delete:
        print("dry-run: pass --delete --yes to delete this record")
        return
    if not args.yes:
        raise RuntimeError("Refusing to delete without --yes.")

    print("deleteResult", json.dumps(delete_trans(cookie, target), ensure_ascii=False))
    if target.get("transId"):
        try:
            after = get_trans_status(cookie, target["transId"])
        except Exception as exc:
            after = {"checkError": str(exc)}
        print("afterDeleteCheck", json.dumps(after, ensure_ascii=False))


if __name__ == "__main__":
    main()
