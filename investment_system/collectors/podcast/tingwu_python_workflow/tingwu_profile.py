import argparse
import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    from .tingwu_common import (
        DEFAULT_PROFILE_DIR,
        DEFAULT_STORAGE_STATE,
        WORKDIR,
        cookie_from_storage_state,
        parse_credentials,
        request_headers,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from tingwu_common import (
        DEFAULT_PROFILE_DIR,
        DEFAULT_STORAGE_STATE,
        WORKDIR,
        cookie_from_storage_state,
        parse_credentials,
        request_headers,
    )


EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
HOME_URL = "https://tingwu.aliyun.com/home"
FOLDER_URL = "https://tingwu.aliyun.com/folders/0"
DIRECT_LOGIN_URL = (
    "https://account.aliyun.com/login/login_aliyun.htm"
    "?oauth_callback=https%3A%2F%2Ftingwu.aliyun.com%2Fhome"
)


def configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def launch_context(p, profile_dir: Path, headed: bool):
    profile_dir.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=not headed,
        executable_path=EDGE,
        viewport={"width": 1365, "height": 900},
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled"],
    )


def first_page(context):
    return context.pages[0] if context.pages else context.new_page()


def is_logged_in(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=10000)
    except Exception:
        return False
    return "我的记录" in body and "立即登录" not in body


def api_logged_in(storage_state: Path) -> bool:
    try:
        cookie = cookie_from_storage_state(storage_state)
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
            "pageNo": 1,
            "pageSize": 1,
        }
        response = requests.post(
            "https://tingwu.aliyun.com/api/trans/request?getTransList&c=web",
            headers=request_headers(cookie),
            json=payload,
            timeout=30,
        )
        data = response.json()
        return bool(data.get("success"))
    except Exception:
        return False


def current_body(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=10000)
    except Exception:
        return ""


def goto_and_check(page, url: str = HOME_URL) -> bool:
    page.goto(url, wait_until="networkidle", timeout=60000)
    return is_logged_in(page)


def save_state(context, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(output))
    print("saved storage state:", output)


def write_status(logged_in: bool, state_path: Path, profile_dir: Path) -> None:
    result = {
        "loggedIn": logged_in,
        "profileDir": str(profile_dir),
        "storageState": str(state_path),
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    WORKDIR.mkdir(parents=True, exist_ok=True)
    (WORKDIR / "tingwu_profile_status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def wait_for_manual_login(page, timeout_seconds: int) -> bool:
    print("Browser opened. Please log in to Tongyi Tingwu in that window.")
    print("If the embedded login panel says network error, use the blue login-page link or log in from the full page.")
    print(f"Waiting up to {timeout_seconds} seconds...")
    deadline = time.time() + timeout_seconds
    redirected_full_login = False
    while time.time() < deadline:
        if is_logged_in(page):
            return True
        body = current_body(page)
        if "网络出现问题" in body and not redirected_full_login:
            print("embedded login failed; opening full Aliyun login page in the same profile")
            page.goto(DIRECT_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            redirected_full_login = True
        page.wait_for_timeout(2000)
    return is_logged_in(page)


def click_login_if_present(page) -> None:
    button = page.get_by_role("button", name="立即登录")
    try:
        if button.count():
            button.click()
            page.wait_for_timeout(4000)
    except Exception:
        pass


def select_password_login_tab(page) -> None:
    candidates = [
        page.get_by_text("账号密码登录", exact=True),
        page.locator("text=账号密码登录"),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=5000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            pass


def find_password_frame(page):
    deadline = time.time() + 25
    while time.time() < deadline:
        for frame in page.frames:
            url = frame.url
            if "passport.aliyun.com/havanaone/login/login.htm" in url and "appEntrance=tingwu&" in url:
                return frame
        page.wait_for_timeout(1000)
    return None


def needs_extra_verification(page) -> bool:
    return any("passport.aliyun.com" in frame.url or "/iv/remote/" in frame.url for frame in page.frames)


def command_bootstrap(args) -> int:
    with sync_playwright() as p:
        context = launch_context(p, Path(args.profile_dir), headed=True)
        page = first_page(context)
        goto_and_check(page, HOME_URL)
        if is_logged_in(page):
            print("already logged in")
            save_state(context, Path(args.storage_state))
            write_status(True, Path(args.storage_state), Path(args.profile_dir))
            context.close()
            return 0
        click_login_if_present(page)
        ok = wait_for_manual_login(page, args.timeout)
        if ok:
            save_state(context, Path(args.storage_state))
            write_status(True, Path(args.storage_state), Path(args.profile_dir))
            print("bootstrap complete")
            context.close()
            return 0
        page.screenshot(path=str(WORKDIR / "tingwu_profile_bootstrap_timeout.png"), full_page=True)
        write_status(False, Path(args.storage_state), Path(args.profile_dir))
        print("bootstrap timed out; screenshot saved")
        context.close()
        return 1


def command_status(args) -> int:
    with sync_playwright() as p:
        context = launch_context(p, Path(args.profile_dir), headed=args.headed)
        page = first_page(context)
        goto_and_check(page, FOLDER_URL)
        candidate_state = WORKDIR / "tingwu_profile_state.candidate.json"
        save_state(context, candidate_state)
        logged_in = api_logged_in(candidate_state)
        print("loggedIn=", logged_in)
        if logged_in:
            candidate_state.replace(Path(args.storage_state))
            print("updated storage state:", args.storage_state)
        elif candidate_state.exists():
            candidate_state.unlink()
        write_status(logged_in, Path(args.storage_state), Path(args.profile_dir))
        context.close()
        return 0 if logged_in else 2


def command_auto_login(args) -> int:
    login_id, password = parse_credentials(args.credential_file)
    with sync_playwright() as p:
        context = launch_context(p, Path(args.profile_dir), headed=args.headed)
        page = first_page(context)
        if goto_and_check(page, HOME_URL):
            print("already logged in")
            save_state(context, Path(args.storage_state))
            write_status(True, Path(args.storage_state), Path(args.profile_dir))
            context.close()
            return 0

        click_login_if_present(page)
        select_password_login_tab(page)
        frame = find_password_frame(page)
        if not frame:
            page.screenshot(path=str(WORKDIR / "tingwu_profile_auto_login_no_frame.png"), full_page=True)
            print("password login frame was not found")
            context.close()
            return 1

        frame.locator("#fm-login-id").fill(login_id)
        frame.locator("#fm-login-password").fill(password)
        captcha_visible = frame.locator("#fm-login-checkcode").is_visible()
        print("captchaVisible=", captcha_visible)
        frame.locator("#fm-login-password").press("Enter")
        page.wait_for_timeout(5000)

        if needs_extra_verification(page):
            print("extra verification is required")
            if args.headed and args.wait_verification_seconds > 0:
                deadline = time.time() + args.wait_verification_seconds
                while time.time() < deadline and needs_extra_verification(page):
                    page.wait_for_timeout(2000)
            else:
                print("run `python tingwu_profile.py bootstrap` to complete verification manually")

        logged_in = is_logged_in(page)
        print("loggedIn=", logged_in)
        if logged_in:
            save_state(context, Path(args.storage_state))
        else:
            page.screenshot(path=str(WORKDIR / "tingwu_profile_auto_login_failed.png"), full_page=True)
        write_status(logged_in, Path(args.storage_state), Path(args.profile_dir))
        context.close()
        return 0 if logged_in else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a dedicated persistent Tongyi Tingwu browser profile.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE))
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="Open a visible browser for first manual login.")
    bootstrap.add_argument("--timeout", type=int, default=600)
    bootstrap.set_defaults(func=command_bootstrap)

    status = sub.add_parser("status", help="Check whether the dedicated profile is logged in.")
    status.add_argument("--headed", action="store_true")
    status.set_defaults(func=command_status)

    auto = sub.add_parser("auto-login", help="Try password login inside the dedicated profile.")
    auto.add_argument("--credential-file", default=str(WORKDIR / "tongyi_password.txt"))
    auto.add_argument("--headed", action="store_true")
    auto.add_argument("--wait-verification-seconds", type=int, default=300)
    auto.set_defaults(func=command_auto_login)
    return parser


def main() -> None:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except PlaywrightTimeoutError as exc:
        print("playwright timeout:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

