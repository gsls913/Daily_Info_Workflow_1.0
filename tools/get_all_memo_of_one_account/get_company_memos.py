"""
获取指定公司的所有历史会议纪要
================================
用法:
  python get_company_memos.py              # 交互式输入股票代码和公司名称
  python get_company_memos.py --stock 601888.SH --name 中国中免  # 命令行指定

功能:
  1. 用户输入股票代码和公司名称
  2. 获取该公司的所有历史纪要列表（支持翻页）
  3. 下载纪要详情并保存为Markdown
  4. 自动进行AI标签判断和评价
  5. 管理已读文章和过期清理

依赖:
  pip install requests playwright
  playwright install chromium
"""

import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import requests
import json
import time
import os
import re
import argparse
import random
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common_libs.utils.notifications import show_windows_notification
from common_libs.utils.paths import PROJECT_ROOT, CREDENTIALS_DIR, TOKEN_FILE, ALPHAPAI_INFO_FILE, MEMO_BASE_DIR, COMPANY_MEMO_HISTORY_FILE
from common_libs.utils.logging_config import setup_logging, clean_old_logs, get_log_functions
from common_libs.alphapai.auth import auto_login, load_token, get_token, get_headers
from common_libs.alphapai.html2md import html_to_markdown
from common_libs.alphapai.transcript import format_transcript
from common_libs.storage.download_history import (
    load_download_history, save_download_history,
    add_to_history as _add_to_history, clean_old_history as _clean_old_history,
    extract_date_from_key
)
from common_libs.article.article_manager import (
    check_if_read, extract_date_from_md,
    clean_old_read_articles as _clean_old_read_articles
)
from common_libs.config.config_loader import (
    get as cfg, get_alphapai_api, get_retention_days
)

HISTORY_FILE = COMPANY_MEMO_HISTORY_FILE

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_RETENTION_DAYS = get_retention_days('log')

logger = setup_logging(LOG_DIR, log_prefix="download", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)

OBSIDIAN_BASE_DIR = MEMO_BASE_DIR
INBOX_DIR = os.path.join(OBSIDIAN_BASE_DIR, "0-Inbox")
READ_INBOX_DIR = os.path.join(OBSIDIAN_BASE_DIR, "已读", "0-Inbox")

READ_ARTICLE_RETENTION_DAYS = get_retention_days('memo_read_article')
HISTORY_RETENTION_DAYS = get_retention_days('history')
MAX_HISTORY_PER_STOCK = cfg('alphapai.max_history_per_key', 100)


BASE_URL = get_alphapai_api('').rstrip('/')
DETAIL_API = get_alphapai_api('detail')
STOCK_TRACE_API = get_alphapai_api('stock_trace')


def auto_login_local():
    return auto_login(TOKEN_FILE, ALPHAPAI_INFO_FILE, log_info=log_info, log_error=log_error, screenshot_dir=LOG_DIR)


def get_token_local():
    return get_token(TOKEN_FILE, ALPHAPAI_INFO_FILE, log_info=log_info, log_error=log_error, screenshot_dir=LOG_DIR)


def get_meeting_unique_key(meeting):
    title = meeting.get('title', '').strip()
    time_str = meeting.get('time', '') or ''
    date_str = time_str[:10] if time_str else ''
    return f"{title}|{date_str}"


def clean_old_history():
    return _clean_old_history(HISTORY_FILE, retention_days=HISTORY_RETENTION_DAYS)


def is_meeting_downloaded(stock_code, meeting_key):
    history = load_download_history(HISTORY_FILE)
    return meeting_key in history.get(stock_code, [])


def add_to_history(stock_code, meeting_keys):
    return _add_to_history(HISTORY_FILE, stock_code, meeting_keys, max_per_key=MAX_HISTORY_PER_STOCK)


def ensure_folder_structure():
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(READ_INBOX_DIR, exist_ok=True)


def archive_read_articles():
    log_info("\n" + "=" * 70)
    log_info("📁 开始归档已读文章...")
    log_info("=" * 70)

    ensure_folder_structure()

    if not os.path.exists(INBOX_DIR):
        log_info("  0-Inbox 文件夹不存在，跳过归档")
        return 0

    md_files = [f for f in os.listdir(INBOX_DIR) if f.endswith('.md')]

    if not md_files:
        log_info("  0-Inbox 文件夹中没有文章")
        return 0

    archived_count = 0

    for md_file in md_files:
        md_file_path = os.path.join(INBOX_DIR, md_file)

        if check_if_read(md_file_path):
            dest_path = os.path.join(READ_INBOX_DIR, md_file)

            if os.path.exists(dest_path):
                log_warn(f"⚠️ 目标文件已存在，跳过: {md_file}")
                continue

            try:
                import shutil
                shutil.move(md_file_path, dest_path)
                archived_count += 1
                log_info(f"  ✓ 已归档: {md_file} → 已读/0-Inbox/")
            except Exception as e:
                log_error(f"❌ 移动文件失败 {md_file}: {e}")

    log_info(f"  总计归档: {archived_count} 篇已读文章")
    log_info("=" * 70)

    return archived_count


def clean_old_read_articles(days_threshold=READ_ARTICLE_RETENTION_DAYS):
    log_info("\n" + "=" * 70)
    log_info(f"🧹 开始清理过期已读文章（超过{days_threshold}天）...")
    log_info("=" * 70)

    total_deleted, _ = _clean_old_read_articles(
        OBSIDIAN_BASE_DIR, ["0-Inbox"],
        days_threshold=days_threshold,
        date_field="日期"
    )

    log_info("=" * 70)
    return total_deleted


# ============================================================
# API请求重试机制
# ============================================================
def _api_request_with_retry(
    url: str,
    headers: dict,
    payload: dict = None,
    method: str = "GET",
    max_attempts: int = 2
) -> tuple:
    """
    带重试机制的API请求
    
    Args:
        url: API地址
        headers: 请求头
        payload: 请求体（POST时使用）
        method: 请求方法（GET或POST）
        max_attempts: 最大尝试次数
    
    Returns:
        (data, error_msg): 成功返回(data, None)，失败返回(None, error_msg)
    """
    for attempt in range(max_attempts):
        try:
            if method.upper() == "POST":
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
            else:
                resp = requests.get(url, headers=headers, timeout=30)
            
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401 and attempt == 0:
                log_info("🔄 Token 已过期，重新登录...")
                token_data = auto_login_local()
                if not token_data:
                    log_error("❌ 重新登录失败")
                    return None, "重新登录失败"
                headers['authorization'] = token_data['authorization']
                headers['x-device'] = token_data['x_device']
                continue
            log_error(f"❌ HTTP错误: {e}")
            return None, str(e)
        except requests.exceptions.Timeout:
            log_error(f"❌ 请求超时")
            return None, "请求超时"
        except requests.exceptions.ConnectionError as e:
            log_error(f"❌ 网络连接错误: {e}")
            return None, f"网络连接错误: {e}"
        except json.JSONDecodeError as e:
            log_error(f"❌ JSON解析错误: {e}")
            return None, f"JSON解析错误: {e}"
        except Exception as e:
            log_error(f"❌ 请求异常: {e}")
            return None, str(e)
        
        if data.get('code') != 200000:
            if attempt == 0 and (data.get('code') == 401001 or 'token' in str(data.get('message', '')).lower()):
                log_info("🔄 API 返回错误，尝试重新登录...")
                token_data = auto_login_local()
                if not token_data:
                    return None, "重新登录失败"
                headers['authorization'] = token_data['authorization']
                headers['x-device'] = token_data['x_device']
                continue
            log_error(f"❌ API错误: {data.get('message')}")
            return None, data.get('message', '未知错误')
        
        return data, None
    
    return None, "多次尝试失败"


# ============================================================
# 获取纪要列表
# ============================================================
def fetch_memo_list(headers, stock_code, page_num=1, page_size=50):
    payload = {
        "pageNum": page_num,
        "pageSize": page_size,
        "stockCode": stock_code,
        "type": 31,
        "ratings": []
    }
    
    data, error = _api_request_with_retry(STOCK_TRACE_API, headers, payload, method="POST")
    
    if error:
        return None, 0, 0
    
    result = data.get('data', {})
    memos = result.get('data', [])
    total_page = result.get('totalPageNum', 1)
    total_size = result.get('totalSize', 0)
    
    return memos, total_page, total_size


def fetch_all_memos(headers, stock_code):
    all_memos = []
    page_num = 1
    page_size = 50
    
    log_info(f"\n📋 开始获取 {stock_code} 的所有纪要...")
    
    while True:
        log_info(f"  获取第 {page_num} 页...")
        
        memos, total_page, total_size = fetch_memo_list(headers, stock_code, page_num, page_size)
        
        if memos is None:
            break
        
        if not memos:
            break
        
        all_memos.extend(memos)
        log_info(f"  ✓ 本页 {len(memos)} 篇，累计 {len(all_memos)} 篇")
        
        if page_num >= total_page:
            break
        
        page_num += 1
        time.sleep(0.3)
    
    log_info(f"\n✅ 共获取 {len(all_memos)} 篇纪要")
    return all_memos


# ============================================================
# 获取纪要详情
# ============================================================
def fetch_memo_detail(headers, memo_id, max_retries=4):
    for retry in range(max_retries + 1):
        try:
            resp = requests.get(f"{DETAIL_API}?id={memo_id}", headers=headers, timeout=30)
            resp.raise_for_status()
            resp_data = resp.json()
            
            if resp_data.get('code') == 200000:
                detail = resp_data.get('data')
                if detail and (detail.get('aiSummary') or detail.get('mtSummary')):
                    return detail
                else:
                    # 详情数据为空，等待30秒后重试
                    log_warn(f"⚠️ 详情数据为空，等待30秒后重试 ({retry+1}/{max_retries + 1})...")
                    time.sleep(30)
                    continue
            elif resp_data.get('code') == 401001 or 'token' in str(resp_data.get('message', '')).lower():
                log_info("🔄 Token过期，重新登录...")
                token_data = auto_login_local()
                if token_data:
                    headers['authorization'] = token_data['authorization']
                    headers['x-device'] = token_data['x_device']
                continue
            elif '请求频繁' in str(resp_data.get('message', '')) or 'rate limit' in str(resp_data.get('message', '')).lower():
                log_warn(f"⚠️ 请求频繁，等待30秒后重试 ({retry+1}/{max_retries + 1})...")
                time.sleep(30)
                continue
            else:
                log_warn(f"⚠️ API返回错误: {resp_data.get('message', '未知错误')}")
                
        except requests.exceptions.Timeout:
            log_warn(f"⚠️ 请求超时，重试 {retry+1}/{max_retries + 1}")
        except requests.exceptions.ConnectionError as e:
            log_warn(f"⚠️ 网络错误: {e}，重试 {retry+1}/{max_retries + 1}")
        except Exception as e:
            log_warn(f"⚠️ 获取详情失败: {e}")
        
        time.sleep(1)
    
    return None


# ============================================================
# 保存Markdown
# ============================================================
def save_markdown(memo, detail, stock_code, company_name=""):
    title = memo.get('title', '未知标题')
    time_str = memo.get('time', '')
    date_raw = time_str[:10] if time_str else ''
    memo_id = memo.get('id', '')
    
    article_url = f"https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId={memo_id}"
    
    institution_list = memo.get('institution', [])
    institution_name = institution_list[0].get('name', '') if institution_list else ''
    
    feature_list = memo.get('feature', [])
    feature_str = ', '.join(feature_list) if feature_list else ''
    
    safe_title = "".join(c for c in title if c not in r'\/:?"<>|')
    date_formatted = date_raw.replace("-", "") if date_raw else ""
    filename = f"{date_formatted}_{safe_title}.md"
    
    if len(filename) > 200:
        filename = filename[:200] + ".md"
    
    filepath = os.path.join(INBOX_DIR, filename)

    if os.path.exists(filepath):
        log_info(f"  ⏭️ Skip (已存在): {filename}")
        return None

    parts = []

    if detail:
        parts += ["# 基本信息", ""]
        parts.append(f"- **日期**: {detail.get('roadshowDate', time_str)}")
        parts.append(f"- **机构**: {detail.get('publishInstitution', institution_name) or institution_name}")
        parts.append(f"- **分析师**: {detail.get('analyst', '未知')}")
        parts.append(f"- **嘉宾**: {detail.get('guest', '无')}")

        parts.append(f"- **原文链接**: [{title}]({article_url})")

        dur = detail.get('duration') or (detail.get('mtSummary') or {}).get('duration')
        if dur:
            parts.append(f"- **时长**: {dur} 分钟")
        
        if feature_str:
            parts.append(f"- **标签**: {feature_str}")
        
        parts.append("- **行业**: ")
        # 公司标签：如果有用户输入的公司名称，添加标签
        if company_name:
            parts.append(f"- **公司**: #{company_name}")
        else:
            parts.append("- **公司**: ")
        parts.append("- **人工标签**: ")
        parts.append("- [ ] **是否已读**")
        parts.append("- **我的评价**: ")

    parts += ["", "---", ""]

    ai_html = (detail.get('aiSummary') or {}).get('content') if detail else None
    if ai_html:
        ai_md = html_to_markdown(ai_html)
        if ai_md:
            parts += ["# AI 要点", "", ai_md, ""]

    parts += [
        "---", "",
        f"*下载时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        f"*股票代码: {stock_code}*",
        f"*唯一标识: {title}|{date_raw}*"
    ]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(parts))

    log_info(f"  ✓ {filename}")
    return filepath


# ============================================================
# Windows通知
# ============================================================
def show_windows_notification(title, message, app_id="Microsoft.PowerToys"):
    import subprocess
    
    try:
        ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}").Show($toast)
'''
        
        subprocess.run(
            ['powershell', '-command', ps_script],
            capture_output=True,
            text=True,
            timeout=10
        )
        
    except Exception:
        pass


# ============================================================
# 主流程
# ============================================================
def download_memos_for_stock(stock_code, company_name=""):
    log_info("\n" + "=" * 70)
    log_info(f"📥 获取 {stock_code} 的所有历史纪要")
    if company_name:
        log_info(f"🏷️ 公司名称: {company_name}")
    log_info("=" * 70)
    
    ensure_folder_structure()
    
    token_data = get_token_local()
    if not token_data:
        log_error("❌ 无法获取 token，退出")
        return 0, 0, []
    
    headers = get_headers(token_data)
    
    all_memos = fetch_all_memos(headers, stock_code)
    
    if not all_memos:
        log_info("📭 没有找到任何纪要")
        return 0, 0, []
    
    log_info(f"\n📥 开始下载 {len(all_memos)} 篇纪要...")
    
    downloaded_count = 0
    skipped_count = 0
    downloaded_keys = []
    downloaded_files = []
    
    for i, memo in enumerate(all_memos):
        title = memo.get('title', '未知标题')
        memo_id = memo.get('id', '')
        memo_key = get_meeting_unique_key(memo)
        
        log_info(f"[{i+1}/{len(all_memos)}] {title[:50]}...")
        
        if is_meeting_downloaded(stock_code, memo_key):
            log_info(f"  ⏭️ Skip (已下载)")
            skipped_count += 1
            continue
        
        detail = fetch_memo_detail(headers, memo_id)
        
        if not detail:
            log_warn(f"  ⚠️ 跳过: 无法获取详情数据")
            continue
        
        saved_path = save_markdown(memo, detail, stock_code, company_name)
        if saved_path:
            downloaded_count += 1
            downloaded_keys.append(memo_key)
            downloaded_files.append(Path(saved_path))
        
        wait_time = random.uniform(2, 3)
        time.sleep(wait_time)
    
    if downloaded_keys:
        add_to_history(stock_code, downloaded_keys)
    
    log_info(f"\n✅ 下载完成: 新下载 {downloaded_count} 篇, 跳过已下载 {skipped_count} 篇")
    
    return downloaded_count, len(all_memos), downloaded_files


def run_ai_processing(downloaded_files):
    if not downloaded_files:
        return 0
    
    log_info(f"\n{'=' * 70}")
    log_info("🏷️ 开始AI标签判断和评价")
    log_info(f"{'=' * 70}")
    log_info(f"📋 共有 {len(downloaded_files)} 篇新文章需要处理\n")
    
    try:
        from workflow.ai.ai_client import get_current_provider
        current_provider = get_current_provider()
        provider_display = "火山引擎" if current_provider == "huoshan" else "ModelScope"
        log_info(f"🤖 当前AI模式: {provider_display} ({current_provider})")
        log_info(f"{'=' * 70}\n")
        
        from workflow.ai.aicontent_generator import generate_tags_and_analysis_for_batch_parallel
        
        def tag_log(msg, level="INFO"):
            prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
            log_info(f"  {prefix} {msg}")
        
        results, ind_time, comp_time, analysis_time = generate_tags_and_analysis_for_batch_parallel(
            downloaded_files, log_func=tag_log, return_timing=True
        )
        
        tagged_count = 0
        for ind, comp, analysis_ok in results.values():
            if ind or comp:
                tagged_count += 1
        
        return tagged_count
    
    except Exception as e:
        log_error(f"❌ AI处理失败: {e}")
        import traceback
        traceback.print_exc()
        return 0


def main():
    parser = argparse.ArgumentParser(description='获取指定公司的所有历史会议纪要')
    parser.add_argument('--stock', type=str, default=None, help='股票代码，如 601888.SH')
    parser.add_argument('--name', type=str, default=None, help='公司名称，如 中国中免')
    args = parser.parse_args()
    
    start_time = time.time()
    
    log_info("\n" + "=" * 70)
    log_info("🚀 获取公司历史会议纪要工具")
    log_info("=" * 70)
    
    clean_old_logs(LOG_DIR, LOG_RETENTION_DAYS)
    
    clean_old_history()
    
    archive_read_articles()
    
    clean_old_read_articles()
    
    if args.stock:
        stock_code = args.stock.strip()
        company_name = args.name.strip() if args.name else ""
        log_info(f"\n✅ 使用命令行参数: 股票代码 = {stock_code}")
        if company_name:
            log_info(f"✅ 公司名称 = {company_name}")
    else:
        print("\n" + "=" * 60)
        print("📋 请输入股票代码和公司名称")
        print("=" * 60)
        print("示例股票代码: 601888.SH, 000001.SZ, 01880.HK")
        print("示例公司名称: 中国中免, 招商银行, 腾讯控股")
        print("=" * 60)
        
        stock_code = input("股票代码: ").strip()
        
        if not stock_code:
            log_error("❌ 股票代码不能为空")
            return
        
        company_name = input("公司名称 (可选，按回车跳过): ").strip()
        
        log_info(f"\n✅ 已输入股票代码: {stock_code}")
        if company_name:
            log_info(f"✅ 已输入公司名称: {company_name}")
    
    downloaded_count, total_count, downloaded_files = download_memos_for_stock(stock_code, company_name)
    
    tagged_count = 0
    if downloaded_files:
        tagged_count = run_ai_processing(downloaded_files)
    
    elapsed = time.time() - start_time
    processed_count = max(downloaded_count, 1)
    avg_time = elapsed / processed_count
    
    log_info(f"\n{'=' * 70}")
    log_info("📊 下载统计")
    log_info(f"{'=' * 70}")
    log_info(f"📋 股票代码: {stock_code}")
    if company_name:
        log_info(f"🏷️ 公司名称: {company_name}")
    log_info(f"📥 获取纪要: {total_count} 篇")
    log_info(f"✅ 新下载: {downloaded_count} 篇")
    log_info(f"🏷️ AI处理: {tagged_count} 篇")
    log_info(f"⏱️ 总用时: {elapsed:.1f}秒, 平均每篇: {avg_time:.1f}秒")
    log_info(f"{'=' * 70}")
    
    show_windows_notification(
        "公司纪要下载完成",
        f"{stock_code}: 下载 {downloaded_count}/{total_count} 篇, AI处理 {tagged_count} 篇"
    )


if __name__ == "__main__":
    main()
