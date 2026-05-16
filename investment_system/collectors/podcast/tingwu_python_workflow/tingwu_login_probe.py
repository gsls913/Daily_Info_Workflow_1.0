import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from .tingwu_common import WORKDIR, parse_credentials
except ImportError:  # pragma: no cover - direct script execution fallback
    from tingwu_common import WORKDIR, parse_credentials


EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential-file", default=str(WORKDIR / "tongyi_password.txt"))
    parser.add_argument("--storage-state", default=str(WORKDIR / "tingwu_state_python.json"))
    parser.add_argument("--wait-verification-seconds", type=int, default=300)
    parser.add_argument("--headed", action="store_true", help="Show the browser window for manual verification.")
    args = parser.parse_args()

    login_id, password = parse_credentials(args.credential_file)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headed,
            executable_path=EDGE,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(viewport={"width": 1365, "height": 900}, locale="zh-CN")
        page = context.new_page()

        page.goto("https://tingwu.aliyun.com/home", wait_until="networkidle", timeout=60000)
        if page.get_by_role("button", name="立即登录").count():
            page.get_by_role("button", name="立即登录").click()
            page.wait_for_timeout(4000)

        pwd_frame = None
        deadline = time.time() + 25
        while time.time() < deadline and not pwd_frame:
            for frame in page.frames:
                url = frame.url
                if "passport.aliyun.com/havanaone/login/login.htm" in url and "appEntrance=tingwu&" in url:
                    pwd_frame = frame
                    break
            if not pwd_frame:
                page.wait_for_timeout(1000)
        if not pwd_frame:
            page.screenshot(path=str(WORKDIR / "tingwu_login_probe_python_failed.png"), full_page=True)
            (WORKDIR / "tingwu_login_probe_python_frames.txt").write_text(
                "\n".join(frame.url for frame in page.frames),
                encoding="utf-8",
            )
            raise RuntimeError("Password login frame was not found.")

        pwd_frame.locator("#fm-login-id").fill(login_id)
        pwd_frame.locator("#fm-login-password").fill(password)
        captcha_visible = pwd_frame.locator("#fm-login-checkcode").is_visible()
        print("captchaVisible=", captcha_visible)
        pwd_frame.locator("#fm-login-password").press("Enter")
        page.wait_for_timeout(5000)

        def needs_verification() -> bool:
            return any("passport.aliyun.com" in frame.url or "/iv/remote/" in frame.url for frame in page.frames)

        if needs_verification():
            print(
                "Login requires extra verification. Complete it manually in the opened browser; "
                f"waiting up to {args.wait_verification_seconds} seconds."
            )
            deadline = time.time() + args.wait_verification_seconds
            while time.time() < deadline and needs_verification():
                page.wait_for_timeout(2000)

        body = page.locator("body").inner_text(timeout=10000)
        logged_in = "立即登录" not in body and "我的记录" in body
        print("finalUrl=", page.url)
        print("loggedIn=", logged_in)
        if logged_in:
            context.storage_state(path=args.storage_state)
            print("saved", args.storage_state)
        else:
            Path(args.storage_state).write_text(
                json.dumps({"note": "Login did not complete; extra verification may be required."}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("not saved as reusable login state")
        browser.close()


if __name__ == "__main__":
    main()

