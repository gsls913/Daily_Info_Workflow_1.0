import os
import json
import time
from playwright.sync_api import sync_playwright

BASE_URL = "https://alphapai-web.rabyte.cn"
LOGIN_URL = f"{BASE_URL}/login"

_PHONE = None
_PASSWORD = None


def load_alphapai_credentials(alphapai_info_file):
    global _PHONE, _PASSWORD
    phone = ""
    password = ""

    if os.path.exists(alphapai_info_file):
        try:
            with open(alphapai_info_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('PHONE '):
                        phone = line[6:].strip()
                    elif line.startswith('PASSWORD '):
                        password = line[9:].strip()
        except Exception as e:
            print(f"读取配置文件失败: {e}")

    if not phone or not password:
        raise RuntimeError(
            f"未找到登录凭证，请在 {alphapai_info_file} 中配置：\n"
            "PHONE 你的手机号\n"
            "PASSWORD 你的密码"
        )

    _PHONE = phone
    _PASSWORD = password
    return phone, password


def get_credentials(alphapai_info_file):
    global _PHONE, _PASSWORD
    if _PHONE is None or _PASSWORD is None:
        load_alphapai_credentials(alphapai_info_file)
    return _PHONE, _PASSWORD


def auto_login(token_file, alphapai_info_file, log_info=print, log_error=print, screenshot_dir=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            log_info("1. 访问登录页面...")
            page.goto(LOGIN_URL, wait_until="networkidle")
            time.sleep(2)

            log_info("2. 切换到账号密码登录...")
            link = page.query_selector("text=账号密码登录")
            if not link:
                raise Exception("未找到'账号密码登录'链接")
            link.click()
            time.sleep(2)

            log_info("3. 输入账号密码...")
            phone, password = get_credentials(alphapai_info_file)
            page.wait_for_selector('input[placeholder="请输入手机号"]', timeout=5000)
            page.query_selector('input[placeholder="请输入手机号"]').fill(phone)
            page.query_selector('input[type="password"]').fill(password)
            time.sleep(1)

            log_info("4. 点击登录...")
            login_button = page.query_selector('div:has-text("登录")')
            if login_button:
                cls = (login_button.get_attribute("class") or "").lower()
                if "button" in cls or "btn" in cls:
                    login_button.click()
                else:
                    for btn in page.query_selector_all('div'):
                        if btn.inner_text().strip() == "登录":
                            btn.click()
                            break
            else:
                raise Exception("未找到登录按钮")

            log_info("5. 等待登录完成...")
            page.wait_for_url("**/home/**", timeout=10000)

            if "login" in page.url:
                raise Exception("登录失败，仍在登录页面")

            log_info("登录成功！提取token...")
            tokens = page.evaluate("""() => ({
                user_auth_token: localStorage.getItem('USER_AUTH_TOKEN'),
                vt_token: localStorage.getItem('vt_token')
            })""")

            if not tokens['user_auth_token'] or not tokens['vt_token']:
                raise Exception("Token提取失败")

            token_data = {
                'authorization': tokens['user_auth_token'],
                'x_device': tokens['vt_token'],
                'updated_at': time.strftime('%Y-%m-%d %H:%M:%S')
            }

            os.makedirs(os.path.dirname(token_file), exist_ok=True)
            with open(token_file, 'w', encoding='utf-8') as f:
                json.dump(token_data, f, ensure_ascii=False, indent=2)
            log_info(f"Token已缓存到 {token_file}")
            return token_data

        except Exception as e:
            log_error(f"登录失败: {e}")
            if screenshot_dir:
                try:
                    os.makedirs(screenshot_dir, exist_ok=True)
                    page.screenshot(path=os.path.join(screenshot_dir, "login_error.png"))
                except:
                    pass
            return None
        finally:
            time.sleep(1)
            browser.close()


def load_token(token_file):
    if os.path.exists(token_file):
        try:
            with open(token_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None


def get_token(token_file, alphapai_info_file, log_info=print, log_error=print, screenshot_dir=None):
    token = load_token(token_file)
    if token:
        return token
    log_info("缓存token不存在或已失效，重新登录...")
    return auto_login(token_file, alphapai_info_file, log_info, log_error, screenshot_dir)


def get_headers(token_data):
    return {
        'authorization': token_data['authorization'],
        'x-device': token_data['x_device'],
        'x-from': 'web',
        'content-type': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0',
        'referer': 'https://alphapai-web.rabyte.cn/reading/home/meeting',
        'origin': 'https://alphapai-web.rabyte.cn'
    }
