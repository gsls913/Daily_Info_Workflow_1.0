import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import random
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from .tingwu_common import WORKDIR, load_cookie, request_headers
except ImportError:  # pragma: no cover - direct script execution fallback
    from tingwu_common import WORKDIR, load_cookie, request_headers


DEFAULT_AUDIO = None
MAX_FILES_PER_BATCH = 50
MAX_DURATION_SECONDS = 6 * 60 * 60
MAX_AUDIO_BYTES = 500 * 1024 * 1024
MAX_VIDEO_BYTES = 6 * 1024 * 1024 * 1024
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".wma", ".aac", ".ogg", ".amr", ".flac", ".aiff"}
VIDEO_EXTS = {".mp4", ".wmv", ".m4v", ".flv", ".rmvb", ".dat", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".3gp", ".ogg"}
ALL_EXTS = AUDIO_EXTS | VIDEO_EXTS
FFPROBE_CANDIDATES = []


def content_type_for(path: Path) -> str:
    mapping = {
        ".m4a": "audio/x-m4a",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
    }
    return mapping.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def media_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS and ext not in VIDEO_EXTS - AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS and ext not in AUDIO_EXTS - VIDEO_EXTS:
        return "video"
    # .ogg can be audio or video; Tingwu lists it in both groups. Treat it as audio
    # for the stricter 500M size limit unless a caller renames/encodes as video.
    if ext == ".ogg":
        return "audio"
    return "unknown"


def find_ffprobe(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("ffprobe")
    if found:
        return found
    for candidate in FFPROBE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def probe_duration_seconds(path: Path, ffprobe: str | None) -> float | None:
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def validate_media_file(path: Path, ffprobe: str | None, require_duration: bool = False) -> dict:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Not a file: {path}")
    ext = path.suffix.lower()
    if ext not in ALL_EXTS:
        raise ValueError(f"Unsupported file format: {path.name}")
    kind = media_kind(path)
    size = path.stat().st_size
    max_bytes = MAX_VIDEO_BYTES if kind == "video" else MAX_AUDIO_BYTES
    if size > max_bytes:
        limit = "6G" if kind == "video" else "500M"
        raise ValueError(f"{path.name} exceeds {limit} size limit")
    duration = probe_duration_seconds(path, ffprobe)
    if duration is None:
        if require_duration:
            raise ValueError(f"Could not determine duration for {path.name}; install/provide ffprobe or disable strict duration check")
        duration_note = "unknown"
    else:
        if duration > MAX_DURATION_SECONDS:
            raise ValueError(f"{path.name} is longer than 6 hours")
        duration_note = round(duration, 3)
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "size": size,
        "durationSeconds": duration_note,
    }


def expand_inputs(inputs: list[str], recursive: bool = False) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            files.extend(p for p in iterator if p.is_file() and p.suffix.lower() in ALL_EXTS)
        else:
            files.append(path)
    seen = set()
    unique = []
    for file in files:
        resolved = file.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def chunks(items: list[Path], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def post_json(cookie: str, url: str, payload: dict) -> dict:
    response = requests.post(url, headers=request_headers(cookie, "https://tingwu.aliyun.com/home"), json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"{payload.get('action')} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def generate_put_link(cookie: str, file_path: Path) -> dict:
    task_id = f"rc-upload-{int(time.time() * 1000)}-{random.randint(0, 9999)}"
    ext = file_path.suffix.lstrip(".").lower() or "m4a"
    payload = {
        "action": "generatePutLink",
        "version": "1.0",
        "taskId": task_id,
        "useSts": 1,
        "fileSize": file_path.stat().st_size,
        "dirId": 0,
        "fileContentType": content_type_for(file_path),
        "tag": {
            "showName": file_path.stem,
            "fileFormat": ext,
            "fileType": "local",
            "lang": "fspk",
            "roleSplitNum": 0,
            "translateSwitch": 0,
            "transTargetValue": 0,
            "originalTag": json.dumps({"isVideo": 0}, separators=(",", ":")),
            "client": "web",
        },
    }
    data = post_json(cookie, "https://tingwu.aliyun.com/api/trans/request?generatePutLink&c=web", payload)["data"]
    print("generatePutLink", data["taskId"], data["transId"])
    return data


def oss_authorization(sts: dict, content_type: str, date: str) -> str:
    canonical_headers = f"x-oss-security-token:{sts['securityToken']}\n"
    canonical_resource = f"/{sts['bucket']}/{sts['fileKey']}"
    string_to_sign = "\n".join(["PUT", "", content_type, date, canonical_headers + canonical_resource])
    digest = hmac.new(sts["accessKeySecret"].encode(), string_to_sign.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    return f"OSS {sts['accessKeyId']}:{signature}"


def upload_to_oss(data: dict, file_path: Path) -> None:
    sts = data["sts"]
    content_type = content_type_for(file_path)
    date = dt.datetime.now(dt.UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    key = "/".join(quote(part, safe="") for part in sts["fileKey"].split("/"))
    url = f"https://{sts['bucket']}.oss-cn-shanghai.aliyuncs.com/{key}"
    headers = {
        "date": date,
        "content-type": content_type,
        "x-oss-security-token": sts["securityToken"],
        "authorization": oss_authorization(sts, content_type, date),
    }
    with file_path.open("rb") as fh:
        response = requests.put(url, headers=headers, data=fh, timeout=300)
    response.raise_for_status()
    print("ossUpload", response.status_code)


def sync_put_link(cookie: str, data: dict, file_path: Path) -> None:
    payload = {
        "action": "syncPutLink",
        "version": "1.0",
        "fileLink": data["getLink"],
        "fileSize": file_path.stat().st_size,
        "transId": data["transId"],
    }
    post_json(cookie, "https://tingwu.aliyun.com/api/trans/request?syncPutLink&c=web", payload)
    print("syncPutLink", data["transId"])


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


def upload_one(cookie: str, file_path: Path) -> dict:
    data = generate_put_link(cookie, file_path)
    upload_to_oss(data, file_path)
    sync_put_link(cookie, data, file_path)
    status = get_trans_status(cookie, data["transId"])
    result = {
        "file": str(file_path),
        "taskId": data["taskId"],
        "transId": data["transId"],
        "fileKey": data["sts"]["fileKey"],
        "getLink": "[REDACTED_URL]",
        "status": status,
        "uploadedAt": dt.datetime.now(dt.UTC).isoformat(),
    }
    if status:
        print("status", status.get("status"), "progress", status.get("progress"), status.get("tag", {}).get("showName"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="*", help="Audio/video files or folders. Folders scan one level by default.")
    parser.add_argument("--cookie-log")
    parser.add_argument("--storage-state")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan folder inputs.")
    parser.add_argument("--batch-size", type=int, default=MAX_FILES_PER_BATCH, help="Max files per batch; Tingwu UI limit is 50.")
    parser.add_argument("--ffprobe", help="Path to ffprobe.exe for duration validation.")
    parser.add_argument("--require-duration-check", action="store_true", help="Fail if duration cannot be checked.")
    parser.add_argument("--dry-run", action="store_true", help="Only validate files and print batches; do not upload.")
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > MAX_FILES_PER_BATCH:
        raise ValueError(f"--batch-size must be between 1 and {MAX_FILES_PER_BATCH}")
    input_values = args.inputs or ([DEFAULT_AUDIO] if DEFAULT_AUDIO else [])
    files = expand_inputs(input_values, recursive=args.recursive)
    if not files:
        raise ValueError("No supported media files found.")
    ffprobe = find_ffprobe(args.ffprobe)
    if not ffprobe:
        print("warning: ffprobe not found; duration check will be skipped")
    validations = [validate_media_file(file, ffprobe, args.require_duration_check) for file in files]
    for batch_no, batch in enumerate(chunks(files, args.batch_size), start=1):
        print(f"batch {batch_no}: {len(batch)} file(s)")
    print(json.dumps({"validated": validations}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    cookie = load_cookie(args.cookie_log, args.storage_state)
    results = []
    for batch_no, batch in enumerate(chunks(files, args.batch_size), start=1):
        print(f"uploading batch {batch_no}: {len(batch)} file(s)")
        for file_path in batch:
            results.append(upload_one(cookie, file_path))
    output = {
        "count": len(results),
        "batchSize": args.batch_size,
        "results": results,
    }
    WORKDIR.mkdir(parents=True, exist_ok=True)
    (WORKDIR / "tingwu_last_upload_python.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", WORKDIR / "tingwu_last_upload_python.json")


if __name__ == "__main__":
    main()

