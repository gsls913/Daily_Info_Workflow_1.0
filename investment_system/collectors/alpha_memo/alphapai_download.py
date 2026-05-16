"""
AlphaPai 会议纪要一键下载工具
==============================
用法:
  python alphapai_download.py              # 交互式选择标签和数量
  python alphapai_download.py --auto       # 自动模式：增量下载8个标签的新文章
  python alphapai_download.py --tag 1 --count 10  # 使用标签1下载10篇

依赖:
  pip install requests playwright
  playwright install chromium

切换模型方案:
在data/config/ai_models.json中修改ai_provider为"modelscope"、"huoshan"、"minimax"或"zhongxin"。

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
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from investment_system.common.utils.notifications import show_windows_notification
from investment_system.common.utils.paths import PROJECT_ROOT, CREDENTIALS_DIR, TOKEN_FILE, ALPHAPAI_INFO_FILE, MEMO_BASE_DIR, MEMO_HISTORY_FILE
from investment_system.common.utils.logging_config import setup_logging, clean_old_logs, get_log_functions
from investment_system.common.alphapai.auth import auto_login, load_token, get_token, get_headers
from investment_system.common.alphapai.html2md import html_to_markdown
from investment_system.common.alphapai.transcript import format_transcript
from investment_system.common.markdown_utils import format_duration_minutes, join_metadata_values, normalize_markdown_output
from investment_system.common.storage.download_history import (
    load_download_history, save_download_history,
    add_to_history as _add_to_history, clean_old_history as _clean_old_history,
    extract_date_from_key
)
from investment_system.common.article.article_manager import (
    check_if_read, extract_date_from_md, archive_read_articles_from_folders,
    clean_old_read_articles as _clean_old_read_articles
)
from investment_system.common.config.config_loader import (
    get as cfg, get_alphapai_api, get_retention_days,
    get_memo_read_subfolders, get_memo_tag_configs
)
from investment_system.common.runtime.task_state import update_task
from investment_system.common.runtime.last_downloads import CATEGORY_ALPHA_MEMO, record_markdown

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_RETENTION_DAYS = get_retention_days('log')

logger = setup_logging(LOG_DIR, log_prefix="download", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)

BASE_URL = get_alphapai_api('').rstrip('/')
LIST_API = get_alphapai_api('list')
PERSONAL_LIST_API = get_alphapai_api('personal_list')
RECORD_CONVERT_LIST_API = get_alphapai_api('record_convert_list')
RECORD_CONVERT_DETAIL_API = get_alphapai_api('record_convert_detail')
DETAIL_API = get_alphapai_api('detail')

HISTORY_FILE = MEMO_HISTORY_FILE
DOWNLOAD_BASE_DIR = os.path.join(SCRIPT_DIR, "meetings")
DEFAULT_OUTPUT = SCRIPT_DIR
OBSIDIAN_BASE_DIR = MEMO_BASE_DIR

_tag_configs_raw = get_memo_tag_configs()
TAG_CONFIGS = {}
for _tc in _tag_configs_raw:
    _tid = _tc.get('id')
    if _tid is None:
        continue
    TAG_CONFIGS[_tid] = {
        "name": _tc.get('name', ''),
        "history_key": _tc.get('history_key', ''),
        "output_dir": os.path.join(OBSIDIAN_BASE_DIR, _tc.get('output_subdir', '0-Inbox')),
        "api_type": _tc.get('api_type', 'standard'),
    }
    for _extra_key in ('market_type', 'industry'):
        if _extra_key in _tc:
            TAG_CONFIGS[_tid][_extra_key] = _tc[_extra_key]


# ============================================================
# Windows通知功能
# ============================================================
def show_completion_notification(total_downloaded, total_new, tagged_count=0):
    if tagged_count > 0:
        message = f"已下载 {total_downloaded}/{total_new} 篇新文章，{tagged_count} 篇已打标签"
    else:
        message = f"已成功下载 {total_downloaded}/{total_new} 篇新文章到Obsidian"
    show_windows_notification("Alpha派会议纪要下载完成", message)


# ============================================================
# 历史记录管理
# ============================================================
# 定义所有history_key
HISTORY_KEYS = [tc["history_key"] for tc in _tag_configs_raw]
MAX_HISTORY_PER_KEY = cfg('alphapai.max_history_per_key', 100)
HISTORY_RETENTION_DAYS = get_retention_days('history')
MEMO_NEW_SOURCE_DOWNLOAD_COUNT = cfg('memo.new_source_download_count', 10)
MEMO_MAX_DOWNLOAD_PER_SOURCE = cfg('memo.max_download_per_source', 20)


def get_meeting_unique_key(meeting):
    title = meeting.get('title', '').strip()
    roadshow_date = meeting.get('roadshowDate', '') or ''
    if not roadshow_date:
        roadshow_date = meeting.get('publishTime', '') or ''
    date_str = roadshow_date[:10] if roadshow_date else ''
    return f"{title}|{date_str}"


def clean_old_history():
    return _clean_old_history(HISTORY_FILE, retention_days=HISTORY_RETENTION_DAYS)


def add_to_history(tag_id, meeting_keys):
    tag_config = TAG_CONFIGS[tag_id]
    tag_key = tag_config["history_key"]
    return _add_to_history(HISTORY_FILE, tag_key, meeting_keys, max_per_key=MAX_HISTORY_PER_KEY)


def is_meeting_downloaded(tag_id, meeting_key):
    tag_config = TAG_CONFIGS[tag_id]
    tag_key = tag_config["history_key"]
    history = load_download_history(HISTORY_FILE)
    return meeting_key in history.get(tag_key, [])


# ============================================================
# 文件夹管理
# ============================================================
# 已读文件夹配置
READ_FOLDER_NAME = cfg('memo.read_folder_name', '已读')
READ_SUBFOLDERS = get_memo_read_subfolders()


def ensure_folder_structure():
    for tag_id, config in TAG_CONFIGS.items():
        folder_path = config["output_dir"]
        Path(folder_path).mkdir(parents=True, exist_ok=True)

    read_folder = os.path.join(OBSIDIAN_BASE_DIR, READ_FOLDER_NAME)
    Path(read_folder).mkdir(parents=True, exist_ok=True)
    for subfolder in READ_SUBFOLDERS:
        subfolder_path = os.path.join(read_folder, subfolder)
        Path(subfolder_path).mkdir(parents=True, exist_ok=True)

    return OBSIDIAN_BASE_DIR


def archive_read_articles():
    print("\n" + "=" * 70)
    print("📁 开始归档已读文章...")
    print("=" * 70)

    ensure_folder_structure()

    total_archived = archive_read_articles_from_folders(OBSIDIAN_BASE_DIR, READ_SUBFOLDERS)

    print("=" * 70 + "\n")
    return total_archived


def clean_old_read_articles(days_threshold=90):
    print("\n" + "=" * 70)
    print(f"🧹 开始清理过期已读文章（超过{days_threshold}天）...")
    print("=" * 70)

    total_deleted, _ = _clean_old_read_articles(OBSIDIAN_BASE_DIR, READ_SUBFOLDERS, days_threshold=days_threshold)

    print("=" * 70 + "\n")
    return total_deleted


def get_tag_output_folder(tag_id):
    """获取指定标签的输出文件夹路径"""
    config = TAG_CONFIGS[tag_id]
    return config["output_dir"]


# ============================================================
# 交互功能
# ============================================================
def select_tag():
    """让用户选择标签组合"""
    print("\n" + "=" * 60)
    print("📋 请选择要下载的标签组合：")
    print("=" * 60)
    for key, config in TAG_CONFIGS.items():
        print(f"  {key}. {config['name']}")
    print("=" * 60)
    
    while True:
        try:
            choice = input("请输入数字 (1-8): ").strip()
            if not choice:
                print("❌ 输入不能为空，请重新输入")
                continue
            choice_num = int(choice)
            if choice_num in TAG_CONFIGS:
                selected = TAG_CONFIGS[choice_num]
                print(f"✅ 已选择: {selected['name']}")
                return choice_num, selected
            else:
                print("❌ 请输入 1-8 之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")


def select_count():
    """让用户选择下载数量"""
    print("\n" + "=" * 60)
    print("📊 请选择要下载的会议数量：")
    print("=" * 60)
    print("  提示: 输入数字，如 5, 10, 20, 50 等")
    print("=" * 60)
    
    while True:
        try:
            count = input("请输入下载数量 (默认 5): ").strip()
            if not count:
                print("✅ 使用默认数量: 5")
                return 5
            count_num = int(count)
            if count_num <= 0:
                print("❌ 数量必须大于 0")
                continue
            if count_num > 200:
                confirm = input(f"⚠️ 您选择了 {count_num} 篇，数量较多，确认吗？(y/n): ").strip().lower()
                if confirm != 'y':
                    continue
            print(f"✅ 将下载 {count_num} 篇会议")
            return count_num
        except ValueError:
            print("❌ 请输入有效的数字")


def interactive_mode():
    """交互模式：选择标签和数量"""
    print("\n" + "🚀" * 30)
    print("🚀  AlphaPai 会议纪要下载工具  🚀")
    print("🚀" * 30)
    
    tag_id, tag_config = select_tag()
    count = select_count()
    
    return tag_id, tag_config, count


# ============================================================
# 登录模块
# ============================================================
def auto_login_local():
    return auto_login(TOKEN_FILE, ALPHAPAI_INFO_FILE, log_info=log_info, log_error=log_error, screenshot_dir=SCRIPT_DIR)


def get_token_local():
    return get_token(TOKEN_FILE, ALPHAPAI_INFO_FILE, log_info=log_info, log_error=log_error, screenshot_dir=SCRIPT_DIR)


# ============================================================
# 主下载逻辑
# ============================================================
def _api_request_with_retry(
    url: str,
    headers: dict,
    payload: dict,
    max_attempts: int = 2
) -> tuple:
    """
    带重试机制的API请求
    
    Args:
        url: API地址
        headers: 请求头
        payload: 请求体
        max_attempts: 最大尝试次数
    
    Returns:
        (data, error_msg): 成功返回(data, None)，失败返回(None, error_msg)
    """
    for attempt in range(max_attempts):
        try:
            resp = requests.post(url, json=payload, headers=headers)
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
            if attempt == 0:
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


def fetch_meetings_list_standard(headers, page_num, page_size, tag_config):
    """获取标准会议列表（标签1-6），返回 (meetings_list, total_count)"""
    payload = {
        "pageNum": page_num, "pageSize": page_size,
        "beginTime": "", "endTime": "", "marketType": [],
        "marketTypeV2": tag_config["market_type"],
        "featureV2": [], 
        "filterNoPermission": False,
        "hasRadio": False, 
        "industry": tag_config["industry"], 
        "institution": [],
        "isPrivate": False, "stock": [], "word": "",
        "durationCategory": "", "priceMovementSort": ""
    }
    
    data, error = _api_request_with_retry(LIST_API, headers, payload)
    if error:
        return None, 0
    
    meetings = data['data']['list']
    total = data['data'].get('total', 0)
    return meetings, total


def fetch_meetings_list_personal(headers, page_num, page_size, tag_config):
    """获取个人预约会议列表（标签7），返回 (meetings_list, total_count)"""
    payload = {
        "pageNum": page_num, 
        "pageSize": page_size,
        "beginTime": "", 
        "endTime": ""
    }
    
    data, error = _api_request_with_retry(PERSONAL_LIST_API, headers, payload)
    if error:
        return None, 0
    
    meetings = data['data']['list']
    total = data['data'].get('total', 0)
    
    # 转换预约会议的字段格式，使其与其他会议类型兼容
    converted_meetings = []
    for item in meetings:
        # 预约会议的日期字段是 roadshowSummaryTime
        date_str = item.get('roadshowSummaryTime', '') or item.get('roadshowDate', '')
        
        # 机构信息
        institution = item.get('institution') or []
        institution_name = institution[0].get('name', '') if institution else ''
        
        converted = {
            'id': item.get('id', ''),
            'title': item.get('title', '未知标题'),
            'publishTime': date_str,
            'roadshowDate': date_str,
            'institutionName': institution_name,
            'analystName': '',
            'guestNames': item.get('guest', ''),
            'duration': '',
            # 保留原始数据
            '_original': item
        }
        converted_meetings.append(converted)
    
    return converted_meetings, total


def fetch_meetings_list_record_convert(headers, page_num, page_size, tag_config):
    """获取录音转记会议列表（标签8），返回 (meetings_list, total_count)"""
    payload = {
        "pageNum": page_num, 
        "pageSize": page_size
    }
    
    data, error = _api_request_with_retry(RECORD_CONVERT_LIST_API, headers, payload)
    if error:
        return None, 0
    
    # 录音转记API的响应结构不同，数据在 data.data 中
    meetings = data['data']['data'] if 'data' in data['data'] else []
    total = data['data'].get('totalSize', 0)
    
    # 转换录音转记会议的字段格式，使其与其他会议类型兼容
    converted_meetings = []
    for item in meetings:
        # 时长转换：秒转换为分钟
        duration_sec = item.get('durationDetail', 0) or 0
        duration_min = duration_sec // 60 if duration_sec else 0
        
        converted = {
            'id': item.get('id', ''),
            'title': item.get('title', '未知标题'),
            'publishTime': item.get('createTime', ''),
            'roadshowDate': item.get('createTime', ''),
            'institutionName': item.get('institutionName', ''),
            'analystName': '',
            'guestNames': '',
            'duration': f"{duration_min}" if duration_min else '',
            # 录音转记会议特有字段
            'mediaType': item.get('mediaType', ''),
            'summaryDocxUrl': item.get('summaryDocxUrl', ''),
            'summaryRadioDocxUrl': item.get('summaryRadioDocxUrl', ''),
            'originMediaUrl': item.get('originMediaUrl', ''),
            # 保留原始数据
            '_original': item
        }
        converted_meetings.append(converted)
    
    return converted_meetings, total


def fetch_meetings_list(headers, page_num, page_size, tag_config):
    """获取单页会议列表，根据api_type选择对应的API，返回 (meetings_list, total_count)"""
    api_type = tag_config.get("api_type", "standard")
    
    if api_type == "personal":
        return fetch_meetings_list_personal(headers, page_num, page_size, tag_config)
    elif api_type == "record_convert":
        return fetch_meetings_list_record_convert(headers, page_num, page_size, tag_config)
    else:
        return fetch_meetings_list_standard(headers, page_num, page_size, tag_config)


def download_meetings_incremental(tag_id, tag_config, headers, max_count=None, track_files=False):
    """
    增量下载会议纪要
    遇到已下载的文章时停止获取
    如果该标签没有历史记录，默认下载10篇
    
    Args:
        track_files: 是否跟踪本次下载的文件路径
    
    返回: (下载数量, 新增ID列表, 本次下载的文件路径列表)
    """
    output_dir = get_tag_output_folder(tag_id)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # 获取API类型，用于确定URL格式
    api_type = tag_config.get("api_type", "standard")
    
    print(f"\n📂 输出目录: {output_dir}")
    
    # 检查该标签是否有历史记录
    history = load_download_history(HISTORY_FILE)
    tag_key = tag_config["history_key"]
    has_history = tag_key in history and len(history[tag_key]) > 0
    
    # 调试信息：显示历史记录数量
    if has_history:
        print(f"📋 历史记录: {tag_key} 有 {len(history[tag_key])} 条记录")
    
    # 如果没有历史记录且没有指定数量，按配置下载靠前若干篇。
    if not has_history and max_count is None:
        max_count = MEMO_NEW_SOURCE_DOWNLOAD_COUNT
        print(f"📋 该标签无历史记录，默认下载 {max_count} 篇会议")
    elif has_history and max_count is None:
        max_count = MEMO_MAX_DOWNLOAD_PER_SOURCE
        print(f"📋 已有历史记录，本轮最多下载 {max_count} 篇新会议")
    else:
        print(f"📋 开始增量获取新会议...")
    
    new_meetings = []
    page_num = 1
    page_size = 20
    stop_reason = ""
    
    while True:
        print(f"  获取第 {page_num} 页...")
        
        meetings, total = fetch_meetings_list(headers, page_num, page_size, tag_config)
        if meetings is None:
            stop_reason = "获取列表失败"
            break
        
        if not meetings:
            stop_reason = "没有更多会议"
            break
        
        # 检查是否有已下载的文章（仅当有历史记录时检查）
        found_existing = False
        for meeting in meetings:
            meeting_key = get_meeting_unique_key(meeting)
            
            # 检查是否已下载（仅当有历史记录时）
            if has_history and is_meeting_downloaded(tag_id, meeting_key):
                title = meeting.get('title', '未知标题')
                print(f"  ⏹️  遇到已下载文章: {title[:40]}...")
                print(f"     本页共 {len(meetings)} 篇，新增 {len([m for m in meetings[:meetings.index(meeting)] if get_meeting_unique_key(m) not in [get_meeting_unique_key(nm) for nm in new_meetings]])} 篇")
                found_existing = True
                break
            
            new_meetings.append(meeting)
            
            # 如果达到最大数量限制
            if max_count and len(new_meetings) >= max_count:
                stop_reason = f"达到数量限制 ({max_count})"
                found_existing = True
                break
        
        if found_existing:
            break
        
        print(f"  ✓ 本页 {len(meetings)} 篇，累计新文章 {len(new_meetings)} 篇")
        
        # 如果本页没满，说明没有更多数据了
        if len(meetings) < page_size:
            stop_reason = "已获取全部新文章"
            break
        
        page_num += 1
        time.sleep(0.3)
    
    if not new_meetings:
        print(f"✅ 没有新文章需要下载 ({stop_reason})")
        return 0, [], []
    
    print(f"✅ 发现 {len(new_meetings)} 篇新文章，开始下载...\n")
    
    # 下载新文章
    downloaded_keys = []
    downloaded_count = 0
    downloaded_files = []  # 跟踪本次下载的文件路径
    
    for i, meeting in enumerate(new_meetings):
        title = meeting.get('title', '未知标题')
        meeting_id = meeting.get('id', '')  # 仍需要ID用于API调用
        meeting_key = get_meeting_unique_key(meeting)  # 用于历史记录
        print(f"[{i+1}/{len(new_meetings)}] {title[:50]}...")
        update_task("memo", meeting_key, "detail_fetching", title=title, meta={"source": tag_config["name"]})
        
        # 获取详情（带重试机制）
        detail = None
        max_retries = 4  # 最多重试4次，共5次尝试
        for retry in range(max_retries + 1):
            try:
                if api_type == "record_convert":
                    resp = requests.get(f"{RECORD_CONVERT_DETAIL_API}?taskId={meeting_id}", headers=headers, timeout=30)
                else:
                    resp = requests.get(f"{DETAIL_API}?id={meeting_id}", headers=headers, timeout=30)
                
                resp.raise_for_status()
                resp_data = resp.json()
                
                if resp_data.get('code') == 200000:
                    detail = resp_data.get('data')
                    if detail and (detail.get('aiSummary') or detail.get('mtSummary')):
                        break  # 成功获取到有效数据
                    else:
                        # 详情数据为空，等待30秒后重试
                        log_warn(f"  详情数据为空，等待30秒后重试 ({retry+1}/{max_retries + 1})...")
                        time.sleep(30)
                        continue
                elif resp_data.get('code') == 401001 or 'token' in str(resp_data.get('message', '')).lower():
                    # Token过期，重新登录
                    log_info("  Token过期，重新登录...")
                    token_data = auto_login_local()
                    if token_data:
                        headers['authorization'] = token_data['authorization']
                        headers['x-device'] = token_data['x_device']
                    continue
                elif '请求频繁' in str(resp_data.get('message', '')) or 'rate limit' in str(resp_data.get('message', '')).lower():
                    # 请求频繁，等待30秒
                    log_warn(f"  请求频繁，等待30秒后重试 ({retry+1}/{max_retries + 1})...")
                    time.sleep(30)
                    continue
                else:
                    log_warn(f"  API返回错误: {resp_data.get('message', '未知错误')}")
                    
            except requests.exceptions.Timeout:
                log_warn(f"  请求超时，重试 {retry+1}/{max_retries + 1}")
            except requests.exceptions.ConnectionError as e:
                log_warn(f"  网络错误: {e}，重试 {retry+1}/{max_retries + 1}")
            except Exception as e:
                log_warn(f"  获取详情失败: {e}")
            
            time.sleep(1)  # 普通重试前等待1秒
        
        if not detail:
            log_warn(f"  ⚠ 跳过: 无法获取详情数据")
            update_task("memo", meeting_key, "failed", title=title, error="无法获取详情数据")
            continue
        
        # 保存
        saved_path = save_markdown(meeting, detail, output_dir, api_type)
        if saved_path:
            downloaded_count += 1
            downloaded_keys.append(meeting_key)
            update_task("memo", meeting_key, "markdown_saved", title=title, meta={"path": str(saved_path)})
            record_markdown(
                CATEGORY_ALPHA_MEMO,
                saved_path,
                title=title,
                meta={"tag": tag_config.get("name", ""), "meeting_key": meeting_key},
            )
            # 直接使用返回的文件路径
            if track_files:
                downloaded_files.append(Path(saved_path))
        else:
            update_task("memo", meeting_key, "skipped_existing_file", title=title)
        
        # 随机等待0-2秒，避免请求过快
        import random
        wait_time = random.uniform(2, 3)
        time.sleep(wait_time)
    
    # 更新历史记录
    if downloaded_keys:
        add_to_history(tag_id, downloaded_keys)
    
    print(f"\n✅ 标签 [{tag_config['name']}] 完成: 下载 {downloaded_count}/{len(new_meetings)} 篇新文章")
    return downloaded_count, downloaded_keys, downloaded_files


def auto_download_all_tags(max_count_per_tag=None, auto_tag=True):
    """
    自动下载所有8个标签的新文章（增量模式）
    适合定时任务执行
    
    Args:
        max_count_per_tag: 每个标签最大下载数量
        auto_tag: 下载完成后是否自动进行AI标签判断
    """
    # 记录开始时间
    func_start_time = time.time()
    
    print("\n" + "=" * 70)
    print("🤖 自动增量下载模式")
    print("=" * 70)
    print(f"📁 主目录: {OBSIDIAN_BASE_DIR}")
    print(f"📋 历史记录: {HISTORY_FILE}")
    print("=" * 70)

    tag_ids = sorted(TAG_CONFIGS)
    if not tag_ids:
        raise RuntimeError("未读取到 memo.tag_configs，请检查 config/config.yaml 是否可正常解析。")
    
    # 确保文件夹结构
    ensure_folder_structure()
    
    # 获取 token
    token_data = get_token_local()
    if not token_data:
        raise RuntimeError("无法获取 AlphaPai token")
    
    headers = get_headers(token_data)
    
    # 统计
    total_stats = {
        "total_new": 0,
        "total_downloaded": 0,
        "tags": {}
    }
    
    # 跟踪本次下载的所有文件路径
    all_downloaded_files = []
    
    # 遍历配置中的全部标签
    total_tags = len(tag_ids)
    for index, tag_id in enumerate(tag_ids, start=1):
        tag_config = TAG_CONFIGS[tag_id]
        
        print(f"\n{'=' * 70}")
        print(f"🏷️  [{index}/{total_tags}] {tag_config['name']}")
        print(f"{'=' * 70}")
        
        try:
            count, ids, files = download_meetings_incremental(
                tag_id, tag_config, headers, max_count_per_tag, track_files=auto_tag
            )
        except Exception as e:
            log_error(f"标签处理失败，已跳过该标签并继续后续标签: {tag_config['name']}: {e}")
            count, ids, files = 0, [], []
        
        total_stats["tags"][tag_id] = {
            "name": tag_config['name'],
            "new_count": len(ids),
            "downloaded_count": count
        }
        total_stats["total_new"] += len(ids)
        total_stats["total_downloaded"] += count
        
        # 收集本次下载的文件
        all_downloaded_files.extend(files)
        
        # 标签间延迟
        if index < total_tags:
            time.sleep(1)
    
    # 最终统计
    print(f"\n{'=' * 70}")
    print("📊 下载统计")
    print(f"{'=' * 70}")
    for tag_id, stats in total_stats["tags"].items():
        status = "✅" if stats["new_count"] > 0 else "⏭️"
        print(f"{status} {stats['name']}: {stats['downloaded_count']}/{stats['new_count']} 篇")
    print(f"{'=' * 70}")
    print(f"🎉 总计: 下载 {total_stats['total_downloaded']}/{total_stats['total_new']} 篇新文章")
    print(f"{'=' * 70}")
    
    # 自动进行AI标签判断和评价
    tagged_count = 0
    analysis_count = 0
    total_industry_time = 0.0
    total_company_time = 0.0
    total_analysis_time = 0.0
    if auto_tag and all_downloaded_files:
        print(f"\n{'=' * 70}")
        print("🏷️ 开始AI标签判断和评价")
        print(f"{'=' * 70}")
        print(f"共有 {len(all_downloaded_files)} 篇新文章需要处理\n")
        
        try:
            from investment_system.common.ai.ai_client import get_current_provider
            current_provider = get_current_provider()
            provider_display = {
                "huoshan": "火山引擎",
                "minimax": "MiniMax",
                "zhongxin": "中信 AI",
                "modelscope": "ModelScope",
            }.get(current_provider, current_provider)
            print(f"🤖 当前AI模式: {provider_display} ({current_provider})")
            print(f"{'=' * 70}\n")
            
            from investment_system.common.ai.aicontent_generator import generate_tags_and_analysis_for_batch_parallel
            
            def tag_log(msg, level="INFO"):
                prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
                print(f"  {prefix} {msg}")
            
            results, ind_time, comp_time, analysis_time = generate_tags_and_analysis_for_batch_parallel(all_downloaded_files, log_func=tag_log, return_timing=True)
            
            total_industry_time = ind_time
            total_company_time = comp_time
            total_analysis_time = analysis_time
            
            for ind, comp, analysis_ok in results.values():
                if ind or comp:
                    tagged_count += 1
                if analysis_ok:
                    analysis_count += 1
        
        except Exception as e:
            print(f"\n❌ AI处理失败: {e}")
            print("   您可以稍后手动运行标签生成脚本")
    
    # 显示Windows通知
    show_completion_notification(total_stats['total_downloaded'], total_stats['total_new'], tagged_count)
    
    # 显示总用时
    func_elapsed = time.time() - func_start_time
    processed_count = max(total_stats['total_downloaded'], 1)
    avg_time = func_elapsed / processed_count
    print(f"\n⏱️ 总用时: {func_elapsed:.1f}秒, 平均每篇: {avg_time:.1f}秒")
    
    return total_stats


def process_record_convert_filename(title, date_raw):
    """
    处理录音转记会议的文件名
    规则：
    1. 如果标题以日期开头（如20260330_xxx），直接使用
    2. 如果标题以日期_时间开头（如20260330_151713_xxx），删去时间
    3. 如果标题不以日期开头，在开头添加会议日期
    4. 去掉.m4a、.mp3、.mp4等后缀
    """
    import re
    
    # 去掉音频/视频后缀
    title = re.sub(r'\.(m4a|mp3|mp4|wav|ogg)$', '', title, flags=re.IGNORECASE)
    
    # 检查标题是否以日期开头（8位数字）
    date_pattern = r'^(\d{8})'
    match = re.match(date_pattern, title)
    
    if match:
        # 标题以日期开头
        title_date = match.group(1)
        # 检查是否是 日期_时间 格式（如20260330_151713）
        time_pattern = r'^(\d{8})_\d{6}'
        time_match = re.match(time_pattern, title)
        if time_match:
            # 去掉时间部分，保留日期和后面的内容
            title = re.sub(r'^\d{8}_\d{6}_', f'{title_date}_', title)
        return title
    else:
        # 标题不以日期开头，添加会议日期
        date_formatted = date_raw.replace("-", "") if date_raw else ""
        if date_formatted:
            return f"{date_formatted}_{title}"
        return title


def save_markdown(meeting, detail, output_dir, api_type="standard"):
    """保存单篇会议为 Markdown 文件"""
    title = meeting.get('title', '未知标题')
    # 尝试多个可能的日期字段
    date_raw = (meeting.get('publishTime', '') or '')[:10]
    if not date_raw and detail:
        date_raw = (detail.get('roadshowDate', '') or '')[:10]
    if not date_raw and meeting.get('roadshowDate'):
        date_raw = (meeting.get('roadshowDate', '') or '')[:10]
    mid = meeting.get('id', '')
    
    # 根据API类型构建不同的原文链接
    if api_type == "record_convert":
        # 录音转记会议使用不同的URL格式
        article_url = f"https://alphapai-web.rabyte.cn/reading/self-summary-detail?id={mid}"
    else:
        # 标准会议URL格式
        article_url = f"https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId={mid}"

    # 处理文件名
    if api_type == "record_convert":
        # 录音转记会议使用特殊文件名处理
        filename_base = process_record_convert_filename(title, date_raw)
        safe_title = "".join(c for c in filename_base if c not in r'\/:?"<>|')
        filename = f"{safe_title}.md"
    else:
        # 标准会议文件名格式：YYYYMMDD_标题.md
        safe_title = "".join(c for c in title if c not in r'\/:?"<>|')
        date_formatted = date_raw.replace("-", "") if date_raw else ""
        filename = f"{date_formatted}_{safe_title}.md"
    
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        print(f"  Skip (已存在): {filename}")
        return None  # 返回None表示文件已存在

    parts = []

    if detail:
        dur = format_duration_minutes(detail.get('duration') or (detail.get('mtSummary') or {}).get('duration'))
        date_text = join_metadata_values(detail.get('roadshowDate') or date_raw or '', dur)
        object_text = join_metadata_values(
            detail.get('publishInstitution') or meeting.get('institutionName') or '',
            detail.get('analyst') or meeting.get('analystName') or '',
            detail.get('guest') or meeting.get('guestNames') or '',
        )
        if date_text:
            parts.append(f"- **日期**: {date_text}")
        if object_text:
            parts.append(f"- **对象**: {object_text}")

        stocks = [s.get('stockName', '') for s in (detail.get('stock') or []) if s.get('stockName')]
        if stocks:
            parts.append(f"- **相关股票**: {', '.join(stocks)}")
        industries = [ind.get('industryName', '') for ind in (detail.get('industry') or []) if ind.get('industryName')]
        if industries:
            parts.append(f"- **相关行业**: {', '.join(industries)}")

        # 增加原文链接
        parts.append(f"- **原文链接**: [{title}]({article_url})")
        
        # 添加标签占位符（供后续AI判断和人工填写）
        parts.append("- **行业**: ")
        parts.append("- **公司**: ")
        parts.append("- **人工标签**: ")
        parts.append("- [ ] **是否已读**")
        parts.append("- **我的评价**: ")

    parts += ["", "---", ""]

    # AI 要点（标题提升一级：## → #）
    ai_html = (detail.get('aiSummary') or {}).get('content') if detail else None
    if ai_html:
        ai_md = html_to_markdown(ai_html)
        if ai_md:
            parts += ["# AI 要点", "", ai_md, ""]

    parts += [
        "---", "",
        f"*下载时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        f"*唯一标识: {title}|{date_raw}*"
    ]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(normalize_markdown_output("\n".join(parts)))

    print(f"  ✓ {filename}")
    return filepath  # 返回文件路径而不是布尔值


# ============================================================
if __name__ == "__main__":
    # 记录开始时间
    start_time = time.time()
    
    # 清理过期日志
    clean_old_logs(LOG_DIR, LOG_RETENTION_DAYS)
    
    # 清理过期历史记录
    clean_old_history()
    
    # 归档已读文章（在下载之前执行）
    archive_read_articles()
    
    # 清理过期的已读文章（归档后执行）
    clean_old_read_articles(days_threshold=90)
    
    # 显示当前AI模式
    try:
        from investment_system.common.ai.ai_client import get_current_provider
        current_provider = get_current_provider()
        provider_display = {
            "huoshan": "火山引擎",
            "minimax": "MiniMax",
            "zhongxin": "中信 AI",
            "modelscope": "ModelScope",
        }.get(current_provider, current_provider)
        print(f"\n🤖 当前AI模式: {provider_display} ({current_provider})")
    except:
        pass
    
    parser = argparse.ArgumentParser(description='AlphaPai 会议纪要一键下载')
    parser.add_argument('--auto', action='store_true',
                        help='自动模式：增量下载8个标签的所有新文章')
    parser.add_argument('--tag', type=int, choices=range(1, 9), default=None,
                        help='标签组合 (1-8): 1=A股+宏观, 2=A股+策略, 3=A股+社会服务, 4=H股+社会服务, 5=A股+商贸零售, 6=H股+商贸零售, 7=我预约的会议, 8=录音转记会议')
    parser.add_argument('--count', type=int, default=None,
                        help='下载数量（仅在非自动模式下有效）')
    parser.add_argument('--max-per-tag', type=int, default=None,
                        help='每个标签最大下载数量（仅在自动模式下有效）')
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT,
                        help=f'输出目录（默认 {DEFAULT_OUTPUT}）')
    args = parser.parse_args()

    if args.auto:
        # 自动增量下载模式
        auto_download_all_tags(max_count_per_tag=args.max_per_tag)
    elif args.tag is not None and args.count is not None:
        # 命令行模式（单标签）
        tag_id = args.tag
        if tag_id not in TAG_CONFIGS:
            print(f"❌ 标签 {tag_id} 未在 memo.tag_configs 中配置，请检查 config/config.yaml")
            sys.exit(1)
        tag_config = TAG_CONFIGS[tag_id]
        print(f"\n✅ 使用命令行参数: 标签={tag_config['name']}, 数量={args.count}")
        
        # 确保文件夹结构
        ensure_folder_structure()
        
        # 获取 token
        token_data = get_token_local()
        if not token_data:
            print("❌ 无法获取 token，退出")
            sys.exit(1)
        
        headers = get_headers(token_data)
        
        # 使用增量下载（限制数量）
        count, ids, files = download_meetings_incremental(tag_id, tag_config, headers, max_count=args.count, track_files=True)
        
        # 自动进行AI标签判断和评价
        tagged_count = 0
        analysis_count = 0
        total_industry_time = 0.0
        total_company_time = 0.0
        total_analysis_time = 0.0
        if files:
            print(f"\n{'=' * 70}")
            print("🏷️ 开始AI标签判断和评价")
            print(f"{'=' * 70}")
            print(f"共有 {len(files)} 篇新文章需要处理\n")
            
            try:
                from investment_system.common.ai.ai_client import get_current_provider
                current_provider = get_current_provider()
                provider_display = {
                    "huoshan": "火山引擎",
                    "minimax": "MiniMax",
                    "zhongxin": "中信 AI",
                    "modelscope": "ModelScope",
                }.get(current_provider, current_provider)
                print(f"🤖 当前AI模式: {provider_display} ({current_provider})")
                print(f"{'=' * 70}\n")
                
                from investment_system.common.ai.aicontent_generator import generate_tags_and_analysis_for_batch_parallel
                
                def tag_log(msg, level="INFO"):
                    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
                    print(f"  {prefix} {msg}")
                
                results, ind_time, comp_time, analysis_time = generate_tags_and_analysis_for_batch_parallel(files, log_func=tag_log, return_timing=True)
                
                total_industry_time = ind_time
                total_company_time = comp_time
                total_analysis_time = analysis_time
                
                for ind, comp, analysis_ok in results.values():
                    if ind or comp:
                        tagged_count += 1
                    if analysis_ok:
                        analysis_count += 1
        
            except Exception as e:
                print(f"\n❌ AI处理失败: {e}")
                print("   您可以稍后手动运行标签生成脚本")
        
        # 显示完成通知
        show_completion_notification(count, len(ids), tagged_count)
        
        # 显示总用时
        total_elapsed = time.time() - start_time
        processed_count = max(count, 1)
        avg_time = total_elapsed / processed_count
        print(f"\n⏱️ 总用时: {total_elapsed:.1f}秒, 平均每篇: {avg_time:.1f}秒")
    else:
        # 交互模式
        tag_id, tag_config, count = interactive_mode()
        
        # 确保文件夹结构
        ensure_folder_structure()
        
        # 获取 token
        token_data = get_token_local()
        if not token_data:
            print("❌ 无法获取 token，退出")
            sys.exit(1)
        
        headers = get_headers(token_data)
        
        # 使用增量下载（限制数量）
        count, ids, files = download_meetings_incremental(tag_id, tag_config, headers, max_count=count, track_files=True)
        
        # 自动进行AI标签判断和评价
        tagged_count = 0
        analysis_count = 0
        total_industry_time = 0.0
        total_company_time = 0.0
        total_analysis_time = 0.0
        if files:
            print(f"\n{'=' * 70}")
            print("🏷️ 开始AI标签判断和评价")
            print(f"{'=' * 70}")
            print(f"共有 {len(files)} 篇新文章需要处理\n")
            
            try:
                from investment_system.common.ai.ai_client import get_current_provider
                current_provider = get_current_provider()
                provider_display = {
                    "huoshan": "火山引擎",
                    "minimax": "MiniMax",
                    "zhongxin": "中信 AI",
                    "modelscope": "ModelScope",
                }.get(current_provider, current_provider)
                print(f"🤖 当前AI模式: {provider_display} ({current_provider})")
                print(f"{'=' * 70}\n")
                
                from investment_system.common.ai.aicontent_generator import generate_tags_and_analysis_for_batch_parallel
                
                def tag_log(msg, level="INFO"):
                    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
                    print(f"  {prefix} {msg}")
                
                results, ind_time, comp_time, analysis_time = generate_tags_and_analysis_for_batch_parallel(files, log_func=tag_log, return_timing=True)
                
                total_industry_time = ind_time
                total_company_time = comp_time
                total_analysis_time = analysis_time
                
                for ind, comp, analysis_ok in results.values():
                    if ind or comp:
                        tagged_count += 1
                    if analysis_ok:
                        analysis_count += 1
        
            except Exception as e:
                print(f"\n❌ AI处理失败: {e}")
                print("   您可以稍后手动运行标签生成脚本")
        
        # 显示完成通知
        show_completion_notification(count, len(ids), tagged_count)
        
        # 显示总用时
        total_elapsed = time.time() - start_time
        processed_count = max(count, 1)
        avg_time = total_elapsed / processed_count
        print(f"\n⏱️ 总用时: {total_elapsed:.1f}秒, 平均每篇: {avg_time:.1f}秒")

