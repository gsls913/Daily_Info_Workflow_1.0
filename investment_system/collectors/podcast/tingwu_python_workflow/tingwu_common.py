import json
import os
import re
import time
from pathlib import Path

import requests

from investment_system.common.utils.paths import (
    TINGWU_PROFILE_DIR,
    TINGWU_RUNTIME_DIR,
    TINGWU_STORAGE_STATE_FILE,
)

WORKDIR = Path(TINGWU_RUNTIME_DIR)
DEFAULT_PROFILE_DIR = Path(TINGWU_PROFILE_DIR)
DEFAULT_STORAGE_STATE = Path(TINGWU_STORAGE_STATE_FILE)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
)


def extract_cookie(text: str) -> str:
    match = re.search(r"(?:^|\r?\n)cookie\r?\n([^\r\n]+)", text, re.IGNORECASE)
    if not match:
        raise ValueError("Cookie header not found in request log.")
    return match.group(1).strip()


def cookie_from_storage_state(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = [
        f"{cookie['name']}={cookie['value']}"
        for cookie in data.get("cookies", [])
        if cookie.get("domain", "").endswith("aliyun.com")
    ]
    if not cookies:
        raise ValueError("No aliyun.com cookies found in storage state.")
    return "; ".join(cookies)


def load_cookie(cookie_log: str | None = None, storage_state: str | None = None) -> str:
    if os.environ.get("TINGWU_COOKIE"):
        return os.environ["TINGWU_COOKIE"]
    if storage_state:
        return cookie_from_storage_state(Path(storage_state))
    if DEFAULT_STORAGE_STATE.exists():
        try:
            return cookie_from_storage_state(DEFAULT_STORAGE_STATE)
        except Exception:
            pass
    candidates = []
    if cookie_log:
        candidates.append(Path(cookie_log))
    candidates.extend(
        [
            WORKDIR / "下载音频转录的文档.txt",
            WORKDIR / "上传文件并转录.txt",
        ]
    )
    for candidate in candidates:
        try:
            return extract_cookie(candidate.read_text(encoding="utf-8"))
        except Exception:
            pass
    raise ValueError("No usable Cookie source found. Set TINGWU_COOKIE or pass --cookie-log.")


def request_headers(cookie: str, referer: str = "https://tingwu.aliyun.com/folders/0") -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://tingwu.aliyun.com",
        "referer": referer,
        "cookie": cookie,
        "user-agent": DEFAULT_USER_AGENT,
        "bx-v": "2.5.36",
    }


def post_json_with_retry(
    cookie: str,
    url: str,
    payload: dict,
    timeout: int = 60,
    max_attempts: int = 3,
    retry_delay: float = 2.0,
) -> dict:
    """POST JSON to Tingwu with small retries for transient network/TLS failures."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, headers=request_headers(cookie), json=payload, timeout=timeout)
            if response.status_code >= 500 and attempt < max_attempts:
                time.sleep(retry_delay * attempt)
                continue
            response.raise_for_status()
            data = response.json()
            if not data.get("success"):
                raise RuntimeError(f"{payload.get('action')} failed: {json.dumps(data, ensure_ascii=False)}")
            return data
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.SSLError) as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(retry_delay * attempt)
                continue
            raise
        except requests.exceptions.JSONDecodeError as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(retry_delay * attempt)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"{payload.get('action')} failed without response")


def parse_credentials(path: str | Path) -> tuple[str, str]:
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    login_match = re.search(r"(?:账号|用户名|手机号|loginId|username)\s*[:：]\s*([^\s,，;；]+)", text, re.I)
    password_match = re.search(r"(?:密码|password|pwd)\s*[:：]\s*([^\s,，;；]+)", text, re.I)
    if login_match and password_match:
        return login_match.group(1).strip(), password_match.group(1).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[0], lines[1]
    match = re.match(r"^(\S+)[\s,，:：]+(.+)$", text)
    if not match:
        raise ValueError("Credential file should contain account and password.")
    return match.group(1), match.group(2).strip()

