"""
Alpha派微信公众号文章下载工具
==============================
功能：
1. 登录Alpha派获取token
2. 获取订阅的公众号列表
3. 获取各公众号的文章列表
4. 下载文章并转换为Markdown
5. 管理下载历史记录（自动清理）

依赖:
  pip install requests playwright beautifulsoup4 Pillow openpyxl
  playwright install chromium
"""

import os
import sys
import re
import json
import time
import uuid
import hashlib
import argparse
import threading
from datetime import datetime
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import requests

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

from bs4 import BeautifulSoup, NavigableString

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from investment_system.common.config.config_loader import (
    get as cfg, get_alphapai_api, get_retention_days,
    get_wechat_category_mapping, get_wechat_read_subfolders
)
from investment_system.common.utils.notifications import show_windows_notification
from investment_system.common.utils.paths import PROJECT_ROOT, CREDENTIALS_DIR, TOKEN_FILE, ALPHAPAI_INFO_FILE, WECHAT_ARTICLE_BASE_DIR, ATTACHMENT_DIR, OBSIDIAN_BASE_DIR, WECHAT_HISTORY_FILE, ALPHA_WECHAT_FAILED_ARTICLES_FILE
from investment_system.common.utils.logging_config import setup_logging, clean_old_logs, get_log_functions
from investment_system.common.alphapai.auth import auto_login, load_token, get_token, get_headers
from investment_system.common.config.source_config import load_wechat_accounts
from investment_system.common.storage.download_history import save_json_atomic
from investment_system.common.article.article_manager import (
    check_if_read, extract_date_from_md, extract_images_from_md,
    archive_read_articles_from_folders, clean_old_read_articles as _clean_old_read_articles
)
from investment_system.common.ai.ai_client import AIClient, create_ai_client, get_parallel_workers, AICallType
from investment_system.common.ai.quality import basic_ai_response_ok
from investment_system.common.markdown_utils import normalize_markdown_output
from investment_system.common.runtime.task_state import update_task
from investment_system.common.runtime.last_downloads import CATEGORY_WECHAT, record_markdown


HISTORY_FILE = WECHAT_HISTORY_FILE

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_RETENTION_DAYS = get_retention_days('log')



CATEGORY_DIR_MAP = get_wechat_category_mapping()

NEW_ACCOUNT_DOWNLOAD_COUNT = cfg('wechat.new_account_download_count', 3)
MAX_DOWNLOAD_PER_ACCOUNT = cfg('wechat.max_download_per_account', 40)

READ_FOLDER_NAME = cfg('memo.read_folder_name', '已读')
READ_SUBFOLDERS = get_wechat_read_subfolders()
READ_ARTICLE_RETENTION_DAYS = get_retention_days('wechat_read_article')

MAX_HISTORY_PER_ACCOUNT = cfg('wechat.max_history_per_account', 100)
HISTORY_CLEAN_THRESHOLD = cfg('wechat.history_clean_threshold', 50)

API_RATE_LIMIT_DELAY = cfg('wechat.api.rate_limit_delay', 0.5)
API_TIMEOUT = cfg('wechat.api.timeout', 30)
MAX_RETRY_ATTEMPTS = cfg('wechat.api.max_retry_attempts', 3)
RETRY_DELAY = cfg('wechat.api.retry_delay', 2)

OBSIDIAN_FULL_WIDTH = cfg('wechat.obsidian_full_width', 697)

FAILED_ARTICLES_FILE = ALPHA_WECHAT_FAILED_ARTICLES_FILE
MAX_FAILED_ARTICLES = cfg('wechat.max_failed_articles', 500)

# 全局线程锁（用于并发安全）
history_lock = threading.Lock()
stats_lock = threading.Lock()

logger = setup_logging(LOG_DIR, log_prefix="fetch", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)


# ============================================================
# 已读文章归档和清理
# ============================================================
def ensure_read_folder_structure():
    from investment_system.common.article.article_manager import ensure_read_folder_structure as _ensure
    _ensure(WECHAT_ARTICLE_BASE_DIR, READ_SUBFOLDERS)


def get_source_subfolder(md_file_path):
    file_dir = os.path.dirname(md_file_path)
    folder_name = os.path.basename(file_dir)
    
    if folder_name in READ_SUBFOLDERS:
        return folder_name
    return "6-其他"


def archive_read_articles():
    log_info("\n📁 开始归档已读文章...")
    ensure_read_folder_structure()
    total_archived = archive_read_articles_from_folders(WECHAT_ARTICLE_BASE_DIR, READ_SUBFOLDERS)
    if total_archived > 0:
        log_info(f"📊 归档统计: 总计归档 {total_archived} 篇已读文章")
    return total_archived


def clean_old_read_articles(days_threshold=READ_ARTICLE_RETENTION_DAYS):
    log_info(f"\n🧹 开始清理过期已读文章（超过{days_threshold}天）...")
    total_deleted_articles, total_deleted_images = _clean_old_read_articles(
        WECHAT_ARTICLE_BASE_DIR, READ_SUBFOLDERS,
        days_threshold=days_threshold, attachment_dir=ATTACHMENT_DIR,
        date_field="发布时间"
    )
    if total_deleted_articles > 0:
        img_msg = f", {total_deleted_images} 张图片" if total_deleted_images > 0 else ""
        log_info(f"📊 清理统计: 总计删除 {total_deleted_articles} 篇过期文章{img_msg}")
    return total_deleted_articles, total_deleted_images


# ============================================================
# 历史记录管理（带自动清理）
# ============================================================
def clean_history_if_needed(history, supplier_id):
    """如果历史记录超过阈值，清理到指定数量"""
    if supplier_id not in history:
        return
    
    urls = history[supplier_id]
    if len(urls) > MAX_HISTORY_PER_ACCOUNT:
        history[supplier_id] = urls[-HISTORY_CLEAN_THRESHOLD:]
        log_info(f"清理公众号 {supplier_id} 的历史记录: {len(urls)} -> {HISTORY_CLEAN_THRESHOLD}")


def is_article_downloaded(history, supplier_id, article_url):
    """检查文章是否已下载"""
    if supplier_id not in history:
        return False
    return article_url in history[supplier_id]


def add_to_history(history, supplier_id, article_url):
    """添加文章到下载历史（线程安全）"""
    with history_lock:
        if supplier_id not in history:
            history[supplier_id] = []
        
        if article_url not in history[supplier_id]:
            history[supplier_id].append(article_url)
        
        clean_history_if_needed(history, supplier_id)


# ============================================================
# 统计报告
# ============================================================
class DownloadStats:
    """下载统计类（线程安全）"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = None
        self.end_time = None
        self.total_accounts = 0
        self.total_articles = 0
        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.total_images = 0
        self.total_videos = 0
        self.by_category = {}
        self.by_account = {}
        self.errors = []
        self.downloaded_files = []
    
    def start(self):
        self.start_time = datetime.now()
    
    def end(self):
        self.end_time = datetime.now()
    
    def add_success(self, account_name, category, images=0, videos=0, md_file=None):
        with self._lock:
            self.success_count += 1
            self.total_images += images
            self.total_videos += videos
            
            if category not in self.by_category:
                self.by_category[category] = {'success': 0, 'failed': 0}
            self.by_category[category]['success'] += 1
            
            if account_name not in self.by_account:
                self.by_account[account_name] = {'success': 0, 'failed': 0}
            self.by_account[account_name]['success'] += 1
            
            if md_file:
                self.downloaded_files.append(md_file)
    
    def add_failed(self, account_name, category, error):
        with self._lock:
            self.failed_count += 1
            
            if category not in self.by_category:
                self.by_category[category] = {'success': 0, 'failed': 0}
            self.by_category[category]['failed'] += 1
            
            if account_name not in self.by_account:
                self.by_account[account_name] = {'success': 0, 'failed': 0}
            self.by_account[account_name]['failed'] += 1
            
            self.errors.append({'account': account_name, 'error': str(error)[:100]})
    
    def add_skipped(self):
        with self._lock:
            self.skipped_count += 1
    
    def generate_report(self):
        """生成统计报告"""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time and self.start_time else 0
        minutes, seconds = divmod(int(elapsed), 60)
        
        report = []
        report.append("\n" + "=" * 60)
        report.append("📊 下载统计报告")
        report.append("=" * 60)
        report.append(f"⏱️  耗时: {minutes}分{seconds}秒")
        report.append(f"📁 处理公众号: {self.total_accounts} 个")
        report.append(f"📄 获取文章: {self.total_articles} 篇")
        report.append("-" * 40)
        report.append(f"✅ 成功下载: {self.success_count} 篇")
        report.append(f"❌ 下载失败: {self.failed_count} 篇")
        report.append(f"⏭️  跳过已下载: {self.skipped_count} 篇")
        report.append(f"🖼️  下载图片: {self.total_images} 张")
        report.append(f"🎥 包含视频: {self.total_videos} 个")
        report.append("-" * 40)
        
        if self.by_category:
            report.append("📈 按分类统计:")
            for cat, stats in self.by_category.items():
                report.append(f"   {cat}: 成功 {stats['success']} 篇, 失败 {stats['failed']} 篇")
        
        if self.errors and len(self.errors) <= 5:
            report.append("-" * 40)
            report.append("⚠️  错误详情:")
            for err in self.errors:
                report.append(f"   {err['account']}: {err['error']}")
        
        report.append("=" * 60)
        
        return "\n".join(report)


# 全局统计对象
download_stats = DownloadStats()


logger = setup_logging(LOG_DIR, log_prefix="fetch", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)


# ============================================================
# 错误重试日志管理
# ============================================================
def load_failed_articles():
    """加载失败文章列表"""
    if os.path.exists(FAILED_ARTICLES_FILE):
        try:
            with open(FAILED_ARTICLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_warn(f"读取失败文章列表失败: {e}")
    return []


def save_failed_articles(failed_list):
    """保存失败文章列表"""
    try:
        save_json_atomic(failed_list, FAILED_ARTICLES_FILE)
    except Exception as e:
        log_error(f"保存失败文章列表失败: {e}")




def add_failed_article(article_info):
    """添加失败文章到重试列表（带自动清理）
    
    Args:
        article_info: dict, 包含以下字段:
            - url: 文章URL
            - title: 文章标题
            - account_name: 公众号名称
            - error: 错误信息
            - failed_at: 失败时间
    """
    failed_list = load_failed_articles()
    
    article_info['failed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    urls = [item.get('url') for item in failed_list]
    if article_info.get('url') not in urls:
        failed_list.append(article_info)
        
        # 清理过期记录
        if len(failed_list) > MAX_FAILED_ARTICLES:
            failed_list = failed_list[-MAX_FAILED_ARTICLES:]
            log_info(f"🧹 清理失败文章列表，保留最近 {MAX_FAILED_ARTICLES} 条")
        
        save_failed_articles(failed_list)
        log_info(f"📝 已记录失败文章: {article_info.get('title', '未知')[:30]}...")


def clear_failed_articles():
    """清空失败文章列表"""
    save_failed_articles([])


# ============================================================
# Windows通知功能
# ============================================================
def show_completion_notification(total_downloaded, total_accounts):
    title = "微信公众号文章下载完成"
    message = f"已成功下载 {total_downloaded} 篇文章，处理 {total_accounts} 个公众号"
    show_windows_notification(title, message)


# ============================================================
# API配置
# ============================================================
BASE_URL = get_alphapai_api('').rstrip('/')
ACCOUNT_LIST_API = get_alphapai_api('account_list')
ARTICLE_LIST_API = get_alphapai_api('article_list')


def load_download_history():
    from investment_system.common.storage.download_history import load_download_history as _load
    return _load(HISTORY_FILE)


def save_download_history(history):
    from investment_system.common.storage.download_history import save_download_history as _save
    _save(history, HISTORY_FILE)


# ============================================================
# 登录模块
# ============================================================
def auto_login_local():
    return auto_login(TOKEN_FILE, ALPHAPAI_INFO_FILE, log_info=log_info, log_error=log_error, screenshot_dir=LOG_DIR)


def load_token_local():
    return load_token(TOKEN_FILE)


def is_token_valid(token_data):
    if not token_data:
        return False
    try:
        headers = get_headers(token_data)
        resp = requests.get(ACCOUNT_LIST_API, headers=headers, timeout=10)
        return resp.status_code == 200
    except:
        return False


def get_token_local():
    token = load_token_local()
    if token and is_token_valid(token):
        return token
    log_info("缓存token不存在或已失效，重新登录...")
    return auto_login_local()


# ============================================================
# 公众号配置读取
# ============================================================
def load_wechat_accounts_from_excel():
    """
    从Excel读取公众号配置
    
    Returns:
        list: 公众号配置列表，每个元素为字典:
            {'name': '公众号名称', 'short_name': '简称', 'category': '分类'}
    
    Raises:
        RuntimeError: openpyxl未安装
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误或数据无效
    """
    accounts = load_wechat_accounts()
    if not accounts:
        raise ValueError("没有找到有效的公众号配置，请在 data/config/set_config.xlsx 的 wechat_account sheet 中添加")
    log_info(f"📋 从配置文件读取到 {len(accounts)} 个公众号")
    return accounts


# ============================================================
# 目录和文件名处理
# ============================================================
def get_output_dir(category):
    """根据分类获取输出目录"""
    dir_name = CATEGORY_DIR_MAP.get(category, "6-其他")
    output_dir = os.path.join(WECHAT_ARTICLE_BASE_DIR, dir_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def ensure_all_category_dirs():
    """确保所有分类目录都存在"""
    for category, dir_name in CATEGORY_DIR_MAP.items():
        dir_path = os.path.join(WECHAT_ARTICLE_BASE_DIR, dir_name)
        os.makedirs(dir_path, exist_ok=True)
    
    # 确保附件目录存在
    os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    log_info("✅ 已确保所有分类目录存在")


def parse_publish_date(publish_date_str):
    """
    解析发布日期字符串，返回YYYYMMDD格式
    
    Args:
        publish_date_str: 日期字符串，如 "2026-04-06 10:30:00" 或 "2026-04-06"
    
    Returns:
        str: YYYYMMDD格式日期，如 "20260406"
    """
    if not publish_date_str:
        return datetime.now().strftime('%Y%m%d')
    
    try:
        # 尝试解析多种格式
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d', '%Y%m%d']:
            try:
                dt = datetime.strptime(publish_date_str[:19], fmt)
                return dt.strftime('%Y%m%d')
            except ValueError:
                continue
        
        # 如果都失败，返回当前日期
        return datetime.now().strftime('%Y%m%d')
    except Exception:
        return datetime.now().strftime('%Y%m%d')


def generate_filename(publish_date, short_name, title):
    """
    生成Markdown文件名
    
    Args:
        publish_date: 发布日期字符串
        short_name: 公众号简称
        title: 文章标题
    
    Returns:
        str: 文件名，如 "20260406_聚义_投资最重要的三件事.md"
    """
    date_str = parse_publish_date(publish_date)
    safe_title = sanitize_filename(title)
    safe_short_name = sanitize_filename(short_name)
    
    filename = f"{date_str}_{safe_short_name}_{safe_title}.md"
    return filename


def get_unique_md_filename(output_dir, base_filename):
    """
    获取唯一的MD文件名，避免冲突
    
    Args:
        output_dir: 输出目录
        base_filename: 基础文件名
    
    Returns:
        str: 唯一的文件名
    """
    filepath = os.path.join(output_dir, base_filename)
    
    if not os.path.exists(filepath):
        return base_filename
    
    # 如果文件已存在，添加序号
    name_without_ext = base_filename[:-3]  # 去掉.md
    counter = 1
    while True:
        new_filename = f"{name_without_ext}_{counter}.md"
        new_filepath = os.path.join(output_dir, new_filename)
        if not os.path.exists(new_filepath):
            return new_filename
        counter += 1
        if counter > 100:  # 防止无限循环
            return f"{name_without_ext}_{uuid.uuid4().hex[:8]}.md"


def get_unique_image_filename(base_dir, short_name, index, ext):
    """
    获取唯一的图片文件名，避免冲突
    
    Args:
        base_dir: 附件目录
        short_name: 公众号简称
        index: 图片序号
        ext: 文件扩展名
    
    Returns:
        str: 唯一的文件名
    """
    base_name = f"{short_name}_image_{index:03d}{ext}"
    filepath = os.path.join(base_dir, base_name)
    
    if not os.path.exists(filepath):
        return base_name
    
    # 如果文件已存在，添加序号
    counter = 1
    while True:
        new_name = f"{short_name}_image_{index:03d}_{counter}{ext}"
        new_filepath = os.path.join(base_dir, new_name)
        if not os.path.exists(new_filepath):
            return new_name
        counter += 1
        if counter > 1000:  # 防止无限循环
            return f"{short_name}_image_{index:03d}_{uuid.uuid4().hex[:8]}{ext}"


def save_image_unique(save_dir: Path, short_name: str, index: int, ext: str, content: bytes) -> str:
    """排他写入图片，避免并发下载时同名覆盖。"""
    save_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(1100):
        filename = get_unique_image_filename(str(save_dir), short_name, index, ext)
        filepath = save_dir / filename
        try:
            with open(filepath, "xb") as f:
                f.write(content)
            return filename
        except FileExistsError:
            index += 1
            continue
    filename = f"{short_name}_image_{index:03d}_{uuid.uuid4().hex[:12]}{ext}"
    filepath = save_dir / filename
    with open(filepath, "xb") as f:
        f.write(content)
    return filename


# ============================================================
# API请求（带自动重新登录和错误重试）
# ============================================================
MAX_API_RETRIES = 3  # API请求最大重试次数


def api_request_with_retry(request_func, headers, max_retries=MAX_API_RETRIES):
    """
    带自动重新登录重试的API请求
    
    Args:
        request_func: 返回 (response, data) 元组的请求函数
        headers: 请求头字典
        max_retries: 最大重试次数
    
    Returns:
        成功返回data字典，失败返回None
    """
    for attempt in range(max_retries + 1):
        try:
            result = request_func()
            if isinstance(result, tuple) and len(result) == 2:
                resp, data = result
            else:
                return result
            
            # 检查HTTP状态码
            if resp.status_code == 401:
                if attempt < max_retries:
                    log_info("🔄 Token已过期，重新登录...")
                    new_token = auto_login_local()
                    if new_token:
                        headers['authorization'] = new_token['authorization']
                        headers['x-device'] = new_token['x_device']
                        continue
                    else:
                        log_error("❌ 重新登录失败")
                        return None
            
            # 检查API返回码
            if data.get('code') == 401001:
                if attempt < max_retries:
                    log_info("🔄 API返回401，重新登录...")
                    new_token = auto_login_local()
                    if new_token:
                        headers['authorization'] = new_token['authorization']
                        headers['x-device'] = new_token['x_device']
                        continue
                    else:
                        log_error("❌ 重新登录失败")
                        return None
            
            return data
            
        except requests.exceptions.Timeout:
            log_error(f"❌ 请求超时 (尝试 {attempt + 1}/{max_retries + 1})")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
            
        except requests.exceptions.ConnectionError as e:
            log_error(f"❌ 网络连接错误: {e} (尝试 {attempt + 1}/{max_retries + 1})")
            if attempt < max_retries:
                time.sleep(3)
                continue
            return None
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401 and attempt < max_retries:
                log_info("🔄 HTTP 401，重新登录...")
                new_token = auto_login_local()
                if new_token:
                    headers['authorization'] = new_token['authorization']
                    headers['x-device'] = new_token['x_device']
                    continue
                else:
                    log_error("❌ 重新登录失败")
                    return None
            log_error(f"❌ HTTP错误: {e}")
            return None
            
        except json.JSONDecodeError as e:
            log_error(f"❌ JSON解析错误: {e}")
            return None
            
        except Exception as e:
            log_error(f"❌ 请求异常: {e}")
            if attempt < max_retries:
                time.sleep(1)
                continue
            return None
    
    return None


def fetch_account_list(headers):
    """获取订阅的公众号列表"""
    def request():
        resp = requests.get(ACCOUNT_LIST_API, headers=headers, timeout=30)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            snippet = (resp.text or "").strip().replace("\n", " ")[:200]
            log_error(
                f"❌ 公众号列表接口返回非JSON内容: HTTP {resp.status_code}, "
                f"Content-Type={resp.headers.get('Content-Type', '')}, 内容片段={snippet!r}"
            )
            raise
        return resp, data
    
    data = api_request_with_retry(request, headers)
    if data is None:
        return None
    
    if data.get('code') != 200000:
        log_error(f"获取公众号列表失败: {data.get('message')}")
        return None
    
    return data.get('data', [])


def fetch_article_list(headers, account_info, page_num=1, page_size=20):
    """获取单个公众号的文章列表
    
    Args:
        headers: 请求头
        account_info: 公众号信息字典，包含id和supplierId
        page_num: 页码，从1开始
        page_size: 每页数量
    """
    payload = {
        "account": [{"id": account_info['id'], "supplierId": account_info['supplierId']}],
        "endTime": "",
        "industry": [],
        "isSelected": True,
        "isSubscribed": True,
        "label": [],
        "pageNum": page_num,
        "pageSize": page_size,
        "startTime": "",
        "word": ""
    }
    
    def request():
        resp = requests.post(ARTICLE_LIST_API, json=payload, headers=headers, timeout=30)
        data = resp.json()
        return resp, data
    
    data = api_request_with_retry(request, headers)
    if data is None:
        return None, 0
    
    if data.get('code') != 200000:
        log_error(f"获取文章列表失败: {data.get('message')}")
        return None, 0
    
    result = data.get('data', {})
    return result.get('list', []), result.get('total', 0)


def fetch_articles_until_downloaded(headers, account_info, history, max_pages=10, max_articles_override=None):
    """
    获取公众号文章，直到找到已下载的为止
    - 新公众号（无历史记录）：下载5篇
    - 有记录的公众号：下载未下载过的文章
    """
    supplier_id = account_info['supplierId']
    downloaded_urls = set(history.get(supplier_id, []))
    is_new_account = len(downloaded_urls) == 0
    max_articles = max_articles_override if max_articles_override is not None else (
        NEW_ACCOUNT_DOWNLOAD_COUNT if is_new_account else MAX_DOWNLOAD_PER_ACCOUNT
    )
    
    all_articles = []
    page_num = 1
    page_count = 0
    
    if max_articles_override is not None:
        max_pages = max(max_pages, 10000)

    while page_count < max_pages:
        articles, total = fetch_article_list(headers, account_info, page_num=page_num)
        
        if articles is None or len(articles) == 0:
            break
        
        for article in articles:
            url = article.get('url', '')
            
            if is_new_account:
                if len(all_articles) < max_articles:
                    all_articles.append(article)
            else:
                if url in downloaded_urls:
                    log_info(f"发现已下载文章，停止获取: {article.get('title', '')[:30]}...")
                    return all_articles
                if len(all_articles) < max_articles:
                    all_articles.append(article)
                else:
                    log_info(f"达到公众号本轮下载上限: {max_articles} 篇")
                    return all_articles
        
        if len(all_articles) >= max_articles:
            break
        
        page_num += 1
        page_count += 1
        time.sleep(0.3)
    
    return all_articles


# ============================================================
# 文章下载转Markdown
# ============================================================
HEADERS_FOR_WECHAT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MIN_IMAGE_WIDTH = 100
MIN_IMAGE_HEIGHT = 100
MIN_IMAGE_FILE_SIZE = 5000
MAX_IMAGE_FILE_SIZE = 500 * 1024
MAX_IMAGE_DIMENSION = 1920


def sanitize_filename(name: str) -> str:
    """清理文件名"""
    invalid_chars = r'[<>:"/\\|?*]'
    name = re.sub(invalid_chars, '_', name)
    name = name.strip()
    while name.endswith('.'):
        name = name[:-1]
    return name[:100] if name else "untitled"


def get_image_dimensions(content: bytes) -> tuple:
    """获取图片尺寸"""
    if not PIL_AVAILABLE:
        return (0, 0)
    try:
        img = Image.open(BytesIO(content))
        return img.size
    except Exception:
        return (0, 0)


def compress_image(content: bytes, max_size: int = MAX_IMAGE_DIMENSION, quality: int = 85) -> bytes:
    """压缩图片"""
    if not PIL_AVAILABLE:
        return content
    
    try:
        img = Image.open(BytesIO(content))
        width, height = img.size
        
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * max_size / width)
            else:
                new_height = max_size
                new_width = int(width * max_size / height)
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        return output.getvalue()
    except Exception:
        return content


def is_valid_image(content: bytes) -> bool:
    """检查图片是否有效"""
    if PIL_AVAILABLE:
        width, height = get_image_dimensions(content)
        if width > 0 and height > 0:
            return width >= MIN_IMAGE_WIDTH and height >= MIN_IMAGE_HEIGHT
    
    if len(content) < MIN_IMAGE_FILE_SIZE:
        return False
    
    return True


def get_image_display_width(img_element) -> int:
    """从HTML img元素获取显示宽度
    
    微信文章中图片宽度可能通过以下方式指定：
    1. style属性中的width: XXXpx
    2. width属性
    3. data-w或data-width属性
    
    Args:
        img_element: BeautifulSoup的img元素
    
    Returns:
        int: 显示宽度（像素），如果无法获取则返回0
    """
    width = 0
    
    # 1. 尝试从style属性获取
    style = img_element.get("style", "")
    if style:
        # 匹配 width: XXXpx 或 width: XXX
        width_match = re.search(r'width\s*:\s*(\d+(?:\.\d+)?)\s*(px|%)?', style, re.IGNORECASE)
        if width_match:
            w = float(width_match.group(1))
            unit = width_match.group(2)
            if unit == '%':
                # 百分比无法直接转换，跳过
                pass
            else:
                width = int(w)
    
    # 2. 尝试从width属性获取
    if width == 0:
        width_attr = img_element.get("width", "")
        if width_attr:
            try:
                width = int(float(width_attr))
            except (ValueError, TypeError):
                pass
    
    # 3. 尝试从data-w属性获取
    if width == 0:
        data_w = img_element.get("data-w", "")
        if data_w:
            try:
                width = int(float(data_w))
            except (ValueError, TypeError):
                pass
    
    # 4. 尝试从data-width属性获取
    if width == 0:
        data_width = img_element.get("data-width", "")
        if data_width:
            try:
                width = int(float(data_width))
            except (ValueError, TypeError):
                pass
    
    return width


def calculate_obsidian_width(img_original_width: int, display_width: int) -> int:
    """计算Obsidian中的显示宽度
    
    根据图片原始宽度和HTML中指定的显示宽度，计算Obsidian中应该显示的宽度。
    
    微信文章的默认内容区域宽度约为677px（接近Obsidian的满屏宽度697px）。
    
    逻辑：
    1. 如果display_width为0（无法获取），返回0（使用默认大小）
    2. 如果display_width >= img_original_width，说明图片没有缩小，返回0（使用默认大小）
    3. 否则，计算缩放比例，应用到Obsidian满屏宽度上
    
    Args:
        img_original_width: 图片原始宽度（像素）
        display_width: HTML中指定的显示宽度（像素）
    
    Returns:
        int: Obsidian中的显示宽度，0表示使用默认大小
    """
    WECHAT_CONTENT_WIDTH = 677  # 微信文章内容区域宽度
    
    # 无法获取显示宽度，使用默认
    if display_width == 0:
        return 0
    
    # 图片原始宽度无效，使用默认
    if img_original_width <= 0:
        return 0
    
    # 显示宽度大于等于原始宽度，说明没有缩小，使用默认
    if display_width >= img_original_width:
        return 0
    
    # 计算缩放比例
    scale = display_width / WECHAT_CONTENT_WIDTH
    
    # 如果缩放比例接近1（满屏），返回满屏宽度
    if scale >= 0.95:
        return OBSIDIAN_FULL_WIDTH
    
    # 计算Obsidian中的宽度
    obsidian_width = int(OBSIDIAN_FULL_WIDTH * scale)
    
    # 确保宽度在合理范围内
    if obsidian_width < 100:
        return 0  # 太小，使用默认
    
    return obsidian_width


def download_image(url: str, save_dir: Path, short_name: str, index: int) -> tuple:
    """下载图片到本地，使用公众号简称作为前缀避免冲突（带重试机制）
    
    Args:
        url: 图片URL
        save_dir: 保存目录
        short_name: 公众号简称
        index: 图片序号
    
    Returns:
        tuple: (文件名, 图片宽度) 或 ("", 0) 失败时
    """
    url = normalize_image_download_url(url)
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=HEADERS_FOR_WECHAT, timeout=API_TIMEOUT)
            resp.raise_for_status()
            
            content = resp.content
            original_size = len(content)
            
            if not is_valid_image(content):
                return "", 0
            
            # 获取图片原始尺寸
            img_width = 0
            if PIL_AVAILABLE:
                try:
                    img = Image.open(BytesIO(content))
                    img_width = img.size[0]  # 获取宽度
                except:
                    pass
            
            if original_size > MAX_IMAGE_FILE_SIZE:
                content = compress_image(content)
                # 压缩后重新获取宽度
                if PIL_AVAILABLE:
                    try:
                        img = Image.open(BytesIO(content))
                        img_width = img.size[0]
                    except:
                        pass
            
            content_type = resp.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"
            elif "webp" in content_type:
                ext = ".webp"
            else:
                url_lower = url.lower()
                if ".png" in url_lower:
                    ext = ".png"
                elif ".gif" in url_lower:
                    ext = ".gif"
                elif ".webp" in url_lower:
                    ext = ".webp"
                else:
                    ext = ".jpg"
            
            # 使用公众号简称作为前缀，并以排他创建确保并发时不覆盖已有图片。
            filename = save_image_unique(save_dir, short_name, index, ext, content)
            
            return filename, img_width
            
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                log_warn(f"⏱️ 图片下载超时，重试 ({attempt + 2}/{MAX_RETRY_ATTEMPTS}): {url[:50]}...")
                time.sleep(RETRY_DELAY)
                continue
            log_warn(f"❌ 图片下载超时失败: {url[:50]}...")
            return "", 0
            
        except requests.exceptions.ConnectionError as e:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                log_warn(f"🔌 网络连接错误，重试 ({attempt + 2}/{MAX_RETRY_ATTEMPTS})")
                time.sleep(RETRY_DELAY)
                continue
            log_warn(f"❌ 图片下载连接失败: {str(e)[:50]}")
            return "", 0
            
        except Exception as e:
            log_warn(f"下载图片失败: {url[:50]}... - {e}")
            return "", 0
    
    return "", 0


def normalize_image_download_url(url: str) -> str:
    """修正 CloudFront 图片 URL 中被拼到路径后的微信参数。"""
    url = (url or "").replace("&amp;", "&").strip()
    if "cloudfront-s3.rabyte.cn" not in url:
        return url
    match = re.match(r"^(https?://[^?#]+?\.(?:png|jpe?g|gif|webp))(?:[?&].*)?$", url, flags=re.I)
    if match:
        return match.group(1)
    return url


# ============================================================
# 颜色和格式处理函数（从wechat_to_md.py合并）
# ============================================================
LANGUAGE_MAP = {
    "python": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "csharp": "csharp",
    "c#": "csharp",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "kotlin": "kotlin",
    "scala": "scala",
    "r": "r",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "shell": "shell",
    "bash": "bash",
    "sh": "bash",
    "powershell": "powershell",
    "json": "json",
    "xml": "xml",
    "yaml": "yaml",
    "yml": "yaml",
    "markdown": "markdown",
    "md": "markdown",
}


def is_near_black(r: int, g: int, b: int) -> bool:
    """判断颜色是否接近黑色
    
    满足以下任一条件即为近似黑色：
    1. r + g + b < 80
    2. r, g, b 彼此相差不超过20，且均小于140
    3. r, g, b 都小于60
    """
    if r + g + b < 80:
        return True
    
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    if max_val - min_val <= 20 and r < 140 and g < 140 and b < 140:
        return True
    
    if r < 60 and g < 60 and b < 60:
        return True
    
    return False


def is_near_white(r: int, g: int, b: int, alpha: float = 1.0) -> bool:
    """判断颜色是否接近白色
    
    与黑色逻辑对称，使用 (256 - 值) 计算：
    1. (256-r) + (256-g) + (256-b) < 80，即 r + g + b > 688
    2. 三个差值彼此相差不超过20，且三个差值均小于140
    3. 三个差值都小于60，即 r, g, b 都大于196
    """
    if alpha < 0.5:
        return True
    
    if r + g + b > 688:
        return True
    
    dr, dg, db = 256 - r, 256 - g, 256 - b
    max_diff = max(dr, dg, db)
    min_diff = min(dr, dg, db)
    if max_diff - min_diff <= 20 and dr < 140 and dg < 140 and db < 140:
        return True
    
    if dr < 60 and dg < 60 and db < 60:
        return True
    
    return False


def parse_color(style: str) -> str:
    """从style属性中解析颜色，过滤接近黑色的颜色"""
    if not style:
        return ""
    style_lower = style.lower()
    color_match = re.search(r'(?<!background-)color\s*:\s*([^;]+)', style_lower)
    if color_match:
        color = color_match.group(1).strip()
        if 'rgba(0, 0, 0, 0)' in color or 'rgba(0,0,0,0)' in color:
            return ""
        if color == 'transparent':
            return ""
        
        rgb = parse_rgb_values(color)
        if rgb and is_near_black(rgb[0], rgb[1], rgb[2]):
            return ""
        
        return color
    return ""


def should_display_color(color: str) -> bool:
    """判断颜色是否需要显示"""
    if not color:
        return False
    color_lower = color.lower()
    if 'transparent' in color_lower:
        return False
    if 'rgba(0, 0, 0, 0)' in color_lower or 'rgba(0,0,0,0)' in color_lower:
        return False
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        if is_near_black(r, g, b):
            return False
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
        if is_near_black(r, g, b):
            return False
    return True


def parse_rgb_values(color: str) -> tuple:
    """从颜色字符串中解析RGB值"""
    if not color:
        return None
    color_lower = color.lower()
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        return (int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3)))
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        return (int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3)))
    hex_match = re.match(r'^#([0-9a-f]{6})', color_lower)
    if hex_match:
        return (
            int(hex_match.group(1)[0:2], 16),
            int(hex_match.group(1)[2:4], 16),
            int(hex_match.group(1)[4:6], 16)
        )
    return None


def calculate_luminance(r: int, g: int, b: int) -> float:
    """计算相对亮度"""
    def adjust(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def calculate_contrast(rgb1: tuple, rgb2: tuple) -> float:
    """计算两个颜色之间的对比度"""
    l1 = calculate_luminance(rgb1[0], rgb1[1], rgb1[2])
    l2 = calculate_luminance(rgb2[0], rgb2[1], rgb2[2])
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def is_valid_background(color: str) -> bool:
    """判断背景色是否有效"""
    if not color:
        return False
    color_lower = color.lower()
    if 'transparent' in color_lower:
        return False
    if 'rgba(0, 0, 0, 0)' in color_lower or 'rgba(0,0,0,0)' in color_lower:
        return False
    hex_match = re.match(r'^#([0-9a-f]{6})([0-9a-f]{2})?$', color_lower)
    if hex_match:
        r = int(hex_match.group(1)[0:2], 16)
        g = int(hex_match.group(1)[2:4], 16)
        b = int(hex_match.group(1)[4:6], 16)
        alpha_hex = hex_match.group(2)
        if alpha_hex:
            alpha = int(alpha_hex, 16) / 255.0
            if alpha < 0.5:
                return False
        if is_near_white(r, g, b):
            return False
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        if is_near_white(r, g, b):
            return False
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
        a = float(rgba_match.group(4))
        if is_near_white(r, g, b, a):
            return False
    return True


def parse_background_color(style: str) -> str:
    """从style属性中解析背景色"""
    if not style:
        return ""
    style_lower = style.lower()
    bg_match = re.search(r'background(?:-color)?\s*:\s*([^;]+)', style_lower)
    if bg_match:
        color = bg_match.group(1).strip()
        if not is_valid_background(color):
            return ""
        return color
    return ""


def rgb_to_hex(color: str) -> str:
    """将RGB颜色转换为十六进制"""
    if not color:
        return ""
    if color.startswith('#'):
        return color
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        return f"#{r:02X}{g:02X}{b:02X}"
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color)
    if rgba_match:
        r, g, b, a = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3)), float(rgba_match.group(4))
        hex_alpha = int(a * 255)
        return f"#{r:02X}{g:02X}{b:02X}{hex_alpha:02X}"
    return color


def parse_spacing(style: str) -> dict:
    """从style属性中解析间距信息"""
    result = {
        "line_height": 1.0,
        "margin_top": 0,
        "margin_bottom": 0,
        "padding_top": 0,
        "padding_bottom": 0
    }
    if not style:
        return result
    style = style.lower()
    line_height_match = re.search(r'line-height\s*:\s*([\d.]+)(em|px)?', style)
    if line_height_match:
        val = float(line_height_match.group(1))
        unit = line_height_match.group(2)
        if unit == 'px':
            val = val / 16
        result["line_height"] = val
    def to_em(val: float, unit: str) -> float:
        if unit == 'px':
            return val / 16
        return val
    def parse_value(pattern, key):
        match = re.search(pattern, style)
        if match:
            val = float(match.group(1))
            unit = match.group(2) or 'em'
            result[key] = to_em(val, unit)
    parse_value(r'margin-top\s*:\s*([\d.]+)(em|px)?', "margin_top")
    parse_value(r'margin-bottom\s*:\s*([\d.]+)(em|px)?', "margin_bottom")
    parse_value(r'padding-top\s*:\s*([\d.]+)(em|px)?', "padding_top")
    parse_value(r'padding-bottom\s*:\s*([\d.]+)(em|px)?', "padding_bottom")
    return result


def detect_code_language(element) -> str:
    """检测代码块的语言"""
    classes = element.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        cls_lower = cls.lower()
        if cls_lower.startswith("language-"):
            lang = cls_lower[9:]
            return LANGUAGE_MAP.get(lang, lang)
        if cls_lower.startswith("lang-"):
            lang = cls_lower[5:]
            return LANGUAGE_MAP.get(lang, lang)
        if cls_lower in LANGUAGE_MAP:
            return LANGUAGE_MAP[cls_lower]
    code_elem = element.find("code")
    if code_elem:
        code_classes = code_elem.get("class", [])
        if isinstance(code_classes, str):
            code_classes = code_classes.split()
        for cls in code_classes:
            cls_lower = cls.lower()
            if cls_lower.startswith("language-"):
                lang = cls_lower[9:]
                return LANGUAGE_MAP.get(lang, lang)
            if cls_lower.startswith("lang-"):
                lang = cls_lower[5:]
                return LANGUAGE_MAP.get(lang, lang)
    return ""


def extract_urls_from_text(text: str) -> list:
    """从文本中提取URL"""
    url_pattern = r'(https?://[^\s<>"{}|\\^`\[\]]+)'
    return re.findall(url_pattern, text)


def extract_article_content(html: str) -> dict:
    """从微信文章HTML中提取内容"""
    soup = BeautifulSoup(html, "html.parser")
    
    result = {
        "title": "",
        "author": "",
        "account_name": "",
        "publish_time": "",
        "content": "",
        "original_url": ""
    }
    
    og_title = soup.find("meta", property="og:title")
    if og_title:
        result["title"] = og_title.get("content", "")
    
    if not result["title"]:
        title_tag = soup.find("h1", class_="rich_media_title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)
    
    if not result["title"]:
        title_tag = soup.find("h1")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)
    
    author_tag = soup.find("a", id="js_name")
    if author_tag:
        result["author"] = author_tag.get_text(strip=True)
    
    if not result["author"]:
        author_tag = soup.find("span", class_="rich_media_meta_nickname")
        if author_tag:
            result["author"] = author_tag.get_text(strip=True)
    
    account_tag = soup.find("strong", class_="profile_nickname")
    if account_tag:
        result["account_name"] = account_tag.get_text(strip=True)
    
    publish_time_tag = soup.find("em", id="publish_time")
    if publish_time_tag:
        result["publish_time"] = publish_time_tag.get_text(strip=True)
    
    if not result["publish_time"]:
        publish_time_tag = soup.find("span", class_="rich_media_meta_date")
        if publish_time_tag:
            result["publish_time"] = publish_time_tag.get_text(strip=True)
    
    content_div = soup.find("div", id="js_content")
    if not content_div:
        content_div = soup.find("div", class_="rich_media_content")
    if not content_div:
        content_div = soup.find("div", class_="article-content")
    
    if content_div:
        result["content"] = str(content_div)
    
    return result


def html_to_markdown(html_content: str, image_dir: Path, short_name: str = "") -> tuple:
    """将HTML内容转换为Markdown（增强版：支持颜色、下划线、背景色、代码语言检测、图片大小）
    
    Args:
        html_content: HTML内容
        image_dir: 图片保存目录
        short_name: 公众号简称（用于图片命名）
    
    Returns:
        tuple: (markdown内容, 图片列表, 视频数量)
    """
    soup = BeautifulSoup(html_content, "html.parser")
    images = []
    image_index = 0
    video_count = 0
    failed_images = []  # 记录失败的图片
    
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src and src.startswith("http"):
            image_index += 1
            filename, img_width = download_image(src, image_dir, short_name, image_index)
            if filename:
                # 获取HTML中指定的显示宽度
                display_width = get_image_display_width(img)
                
                # 计算Obsidian中的显示宽度
                obsidian_width = calculate_obsidian_width(img_width, display_width)
                
                images.append((src, filename, obsidian_width))
                img["src"] = filename
                img["data-src"] = filename
                img["obsidian_width"] = obsidian_width  # 存储计算后的宽度
            else:
                # 保留图片占位符，记录失败
                failed_images.append(src)
                img.replace_with(f"\n> ⚠️ **图片下载失败**: [查看原图]({src})\n")
    
    if failed_images:
        log_warn(f"⚠️  {len(failed_images)} 张图片下载失败")
    
    for video in soup.find_all("mpvideo"):
        video_count += 1
        video.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在原文中观看\n\n")
    
    for video in soup.find_all("video"):
        video_count += 1
        video.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在原文中观看\n\n")
    
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "video" in src.lower() or "player" in src.lower():
            video_count += 1
            iframe.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在原文中观看\n\n")
    
    result_lines = []
    last_was_block = True
    prev_margin_bottom_px = 0
    SPACING_THRESHOLD_PX = 10
    
    def format_image(filename: str, alt: str = "图片", width: int = 0) -> str:
        """格式化图片为Obsidian格式
        
        Args:
            filename: 图片文件名
            alt: 图片描述
            width: 显示宽度，0表示使用默认大小
        
        Returns:
            str: Obsidian格式的图片引用
        """
        if width > 0:
            return f"![[{filename}|{width}]]\n"
        return f"![[{filename}]]\n"
    
    def get_element_style(element) -> dict:
        if not element or not hasattr(element, 'get'):
            return {"margin_top": 0, "margin_bottom": 0, "padding_top": 0, "padding_bottom": 0}
        style = element.get("style", "")
        return parse_spacing(style)
    
    def em_to_px(val_em: float) -> float:
        return val_em * 16
    
    def check_spacing_and_add_break(spacing: dict):
        nonlocal prev_margin_bottom_px, last_was_block
        current_margin_top_px = em_to_px(spacing.get("margin_top", 0) + spacing.get("padding_top", 0))
        total_spacing_px = prev_margin_bottom_px + current_margin_top_px
        if total_spacing_px > SPACING_THRESHOLD_PX and not last_was_block:
            result_lines.append("")
    
    def update_prev_margin_bottom(spacing: dict):
        nonlocal prev_margin_bottom_px
        prev_margin_bottom_px = em_to_px(spacing.get("margin_bottom", 0) + spacing.get("padding_bottom", 0))
    
    def process_table(table_node):
        rows = []
        for tr in table_node.find_all("tr"):
            cells = []
            for cell in tr.find_all(["th", "td"]):
                cell_text = cell.get_text(strip=True)
                cell_text = cell_text.replace("|", "\\|").replace("\n", " ")
                cells.append(cell_text)
            if cells:
                rows.append(cells)
        
        if not rows:
            return ""
        
        max_cols = max(len(row) for row in rows)
        
        md_lines = []
        for i, row in enumerate(rows):
            while len(row) < max_cols:
                row.append("")
            md_lines.append("| " + " | ".join(row) + " |")
            if i == 0:
                md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        
        return "\n".join(md_lines) + "\n"
    
    def process_node(node, parent_style=None, inherited_tags=None):
        nonlocal last_was_block
        
        if inherited_tags is None:
            inherited_tags = {"bold": False, "italic": False, "underline": False, "color": "", "background": ""}
        
        if isinstance(node, NavigableString):
            text = str(node)
            if not text.strip():
                return
            
            formatted = text
            
            if inherited_tags["italic"]:
                formatted = f"<i>{formatted}</i>"
            
            if inherited_tags["bold"]:
                formatted = f"<b>{formatted}</b>"
            
            if inherited_tags["underline"]:
                formatted = f"<u>{formatted}</u>"
            
            display_color = inherited_tags["color"]
            display_background = inherited_tags["background"]
            
            if display_color and display_background:
                text_rgb = parse_rgb_values(display_color)
                bg_rgb = parse_rgb_values(display_background)
                
                if text_rgb and bg_rgb:
                    contrast = calculate_contrast(text_rgb, bg_rgb)
                    if contrast < 4.5:
                        bg_brightness = (bg_rgb[0] * 299 + bg_rgb[1] * 587 + bg_rgb[2] * 114) / 1000
                        if bg_brightness > 128:
                            display_color = ""
                        else:
                            display_color = "rgb(255, 255, 255)"
            
            if display_color and should_display_color(display_color):
                formatted = f'<span style="color:{display_color}">{formatted}</span>'
            
            if display_background:
                bg_hex = rgb_to_hex(display_background)
                if bg_hex:
                    formatted = f'<mark style="background: {bg_hex};">{formatted}</mark>'
            
            urls = extract_urls_from_text(formatted)
            for url in urls:
                if f"[{url}]" not in formatted:
                    formatted = formatted.replace(url, f"[{url}]({url})")
            
            if result_lines and not result_lines[-1].endswith('\n'):
                result_lines.append(formatted)
            else:
                result_lines.append(formatted)
            last_was_block = False
            return
        
        if node.name in [None, "script", "style", "noscript"]:
            return
        
        new_tags = inherited_tags.copy()
        
        if node.name in ["strong", "b"]:
            new_tags["bold"] = True
        elif node.name in ["em", "i"]:
            new_tags["italic"] = True
        elif node.name == "u":
            new_tags["underline"] = True
        
        style = node.get("style", "") if hasattr(node, 'get') else ""
        color = parse_color(style)
        if color:
            new_tags["color"] = color
        
        bg_color = parse_background_color(style)
        if bg_color:
            new_tags["background"] = bg_color
        
        if node.name == "img":
            src = node.get("src", "")
            alt = node.get("alt", "图片")
            obsidian_width = node.get("obsidian_width", 0)
            if src and not src.startswith("http"):
                result_lines.append(format_image(src, alt, obsidian_width))
                last_was_block = True
            return
        
        if node.name == "br":
            result_lines.append("\n")
            return
        
        if node.name == "table":
            table_md = process_table(node)
            if table_md:
                result_lines.append("\n" + table_md)
                last_was_block = True
            return
        
        if node.name == "pre":
            code_node = node.find("code")
            if code_node:
                code = code_node.get_text()
            else:
                code = node.get_text()
            if code:
                lang = detect_code_language(node)
                result_lines.append(f"\n```{lang}\n{code.strip()}\n```\n")
                last_was_block = True
            return
        
        if node.name == "blockquote":
            for child in node.children:
                if isinstance(child, str):
                    text = child.strip()
                    if text:
                        result_lines.append(f"> {text}\n")
                elif child.name == "p":
                    text = child.get_text(strip=True)
                    if text:
                        result_lines.append(f"> {text}\n")
                elif hasattr(child, 'children'):
                    process_node(child, inherited_tags=new_tags)
            last_was_block = True
            return
        
        if node.name == "ul":
            def process_list_item(li, indent_level):
                indent = "\t" * indent_level
                for child in li.children:
                    if child.name in ["ul", "ol"]:
                        for nested_li in child.find_all("li", recursive=False):
                            process_list_item(nested_li, indent_level + 1)
                    elif isinstance(child, str):
                        text = str(child).strip()
                        if text:
                            result_lines.append(f"{indent}- {text}\n")
                    elif hasattr(child, 'children'):
                        text = child.get_text(strip=True)
                        if text:
                            result_lines.append(f"{indent}- {text}\n")
            
            for li in node.find_all("li", recursive=False):
                process_list_item(li, 0)
            last_was_block = True
            return
        
        if node.name == "ol":
            for idx, li in enumerate(node.find_all("li", recursive=False), 1):
                text = li.get_text(strip=True)
                if text:
                    result_lines.append(f"{idx}. {text}\n")
            last_was_block = True
            return
        
        if node.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            level = int(node.name[1])
            prefix = "#" * level
            text = node.get_text(strip=True)
            if text:
                result_lines.append(f"{prefix} {text}\n")
                last_was_block = True
            return
        
        if node.name == "p":
            spacing = get_element_style(node)
            check_spacing_and_add_break(spacing)
            for child in node.children:
                process_node(child, inherited_tags=new_tags)
            result_lines.append("\n")
            update_prev_margin_bottom(spacing)
            last_was_block = True
            return
        
        if node.name == "section":
            for child in node.children:
                process_node(child, inherited_tags=new_tags)
            result_lines.append("\n")
            last_was_block = True
            return
        
        if node.name == "code":
            text = node.get_text(strip=True)
            if text:
                result_lines.append(f"`{text}`")
            return
        
        if node.name == "a":
            href = node.get("href", "")
            text = node.get_text(strip=True)
            if href and text and not href.startswith("#"):
                result_lines.append(f"[{text}]({href})")
                return
        
        for child in node.children:
            process_node(child, inherited_tags=new_tags)
    
    content_div = soup.find("div", id="js_content")
    if content_div:
        for child in content_div.children:
            process_node(child)
    else:
        for child in soup.children:
            process_node(child)
    
    markdown = "".join(result_lines)
    
    markdown = re.sub(r'\n[ \t]+\n', '\n\n', markdown)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    markdown = markdown.strip()
    
    return markdown, images, video_count


def fetch_wechat_html_with_playwright(url: str) -> str:
    """使用Playwright获取微信文章HTML（处理JavaScript渲染）"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)
            
            # 等待内容加载
            try:
                page.wait_for_selector("#js_content", timeout=10000)
            except:
                pass
            
            html = page.content()
            return html
        except Exception as e:
            log_error(f"Playwright获取失败: {e}")
            return ""
        finally:
            browser.close()


def download_wechat_article(url: str, output_dir: str, attachment_dir: str) -> dict:
    """下载微信文章并转换为Markdown"""
    download_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        log_info(f"正在获取文章: {url[:60]}...")
        
        # 先尝试用requests获取
        resp = requests.get(url, headers=HEADERS_FOR_WECHAT, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        html = resp.text
        
        # 检查是否有内容，如果没有则用playwright
        article = extract_article_content(html)
        if not article["content"] or not article["title"]:
            log_info("使用Playwright重新获取...")
            html = fetch_wechat_html_with_playwright(url)
            if html:
                article = extract_article_content(html)
        
        if not article["title"]:
            article["title"] = f"微信文章_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        log_info(f"标题: {article['title']}")
        
        safe_title = sanitize_filename(article["title"])
        
        # 创建附件目录
        os.makedirs(attachment_dir, exist_ok=True)
        image_dir = Path(attachment_dir)
        
        markdown, images, video_count = html_to_markdown(article["content"], image_dir)
        
        md_content = []
        
        if article["author"]:
            md_content.append(f"- **公众号**: {article['author']}")
        if article["account_name"]:
            md_content.append(f"- **作者**: {article['account_name']}")
        if article["publish_time"]:
            md_content.append(f"- **发布时间**: {article['publish_time']}")
        md_content.append(f"- **原文链接**: [{article['title']}]({url})")
        md_content.append(f"- [ ] **是否已读**")
        md_content.append(f"- **我的评价**: ")
        
        md_content = [line for line in md_content if line]
        
        md_content.append("")
        md_content.append("---")
        md_content.append("")
        md_content.append(markdown)
        
        md_content.append("")
        md_content.append("---")
        md_content.append(f"下载时间: {download_time}")
        md_content.append(f"唯一标识: {url}")
        
        final_md = normalize_markdown_output("\n".join(md_content))
        
        md_file = os.path.join(output_dir, f"{safe_title}.md")
        os.makedirs(output_dir, exist_ok=True)
        
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        
        log_info(f"保存成功: {md_file}")
        log_info(f"图片数量: {len(images)}")
        
        return {
            "success": True,
            "title": article["title"],
            "author": article["author"],
            "md_file": md_file,
            "image_count": len(images),
            "video_count": video_count
        }
        
    except Exception as e:
        log_error(f"下载失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 主流程
# ============================================================
def match_accounts(target_accounts, account_list):
    """匹配公众号名称，返回匹配的账号信息
    
    Args:
        target_accounts: 目标公众号列表，每个元素为 {'name': ..., 'short_name': ..., 'category': ...}
        account_list: Alpha派返回的账号列表
    
    Returns:
        list: 匹配的账号信息列表
    """
    matched = []
    
    # 创建目标账号名称到详细信息的映射
    target_map = {t['name']: t for t in target_accounts}
    
    for account in account_list:
        name = account.get('name', '')
        if name in target_map:
            target_info = target_map[name]
            matched.append({
                'id': account.get('id'),
                'supplierId': account.get('supplierId'),
                'name': name,
                'short_name': target_info.get('short_name', name[:2]),
                'category': target_info.get('category', '其他')
            })
            log_info(f"✅ 匹配到公众号: {name} (简称: {target_info.get('short_name')}, 分类: {target_info.get('category')})")
    
    # 检查未匹配的
    matched_names = [a['name'] for a in matched]
    for target in target_accounts:
        if target['name'] not in matched_names:
            log_warn(f"⚠️ 未匹配到公众号: {target['name']}")
    
    return matched


def match_single_account(query, account_list):
    """按 supplierId/id/公众号名称匹配单个公众号。"""
    query = (query or "").strip()
    for account in account_list:
        if query in {
            str(account.get("supplierId", "")).strip(),
            str(account.get("id", "")).strip(),
            str(account.get("name", "")).strip(),
        }:
            name = account.get("name", "") or query
            return {
                "id": account.get("id"),
                "supplierId": account.get("supplierId"),
                "name": name,
                "short_name": name[:2],
                "category": "其他",
            }
    return None


CLOUDFRONT_BASE_URL = "https://cloudfront-s3.rabyte.cn"


def fetch_html_from_cloudfront(html_path: str) -> str:
    """从CloudFront获取文章HTML内容（带重试机制）
    
    Args:
        html_path: HTML文件路径
    
    Returns:
        str: HTML内容
    """
    if not html_path:
        return ""
    
    url = f"{CLOUDFRONT_BASE_URL}/{html_path}"
    
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=HEADERS_FOR_WECHAT, timeout=API_TIMEOUT)
            resp.raise_for_status()
            
            # 智能编码检测
            if resp.encoding and resp.encoding.lower() != 'utf-8':
                try:
                    resp.encoding = resp.apparent_encoding or 'utf-8'
                except:
                    resp.encoding = 'utf-8'
            else:
                resp.encoding = 'utf-8'
            
            return resp.text
            
        except requests.exceptions.Timeout:
            log_warn(f"⏱️ 获取HTML超时 (尝试 {attempt + 1}/{MAX_RETRY_ATTEMPTS}): {url[:60]}...")
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
                continue
            return ""
            
        except requests.exceptions.ConnectionError as e:
            log_warn(f"🔌 网络连接错误 (尝试 {attempt + 1}/{MAX_RETRY_ATTEMPTS}): {str(e)[:50]}...")
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY * 2)
                continue
            return ""
            
        except requests.exceptions.HTTPError as e:
            log_error(f"❌ HTTP错误: {e}")
            return ""
            
        except Exception as e:
            log_error(f"❌ 获取HTML失败: {url} - {e}")
            return ""
    
    return ""


def save_article_from_api(article: dict, short_name: str, category: str, output_dir_override: str = None) -> dict:
    """使用API返回的html字段获取完整文章内容
    
    Args:
        article: 文章信息字典
        short_name: 公众号简称
        category: 公众号分类
    
    Returns:
        dict: 保存结果
    """
    download_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        title = article.get('title', '未知标题')
        wechat_url = article.get('url', '')
        html_path = article.get('html', '')
        account_name = article.get('accountName', '')
        publish_date = article.get('publishDate', '')
        summary = article.get('selectedRecommendContent', '')
        content_text = article.get('content', '')
        
        log_info(f"标题: {title}")
        
        # 根据分类获取输出目录
        output_dir = output_dir_override or get_output_dir(category)
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成文件名（处理冲突）
        base_filename = generate_filename(publish_date, short_name, title)
        md_filename = get_unique_md_filename(output_dir, base_filename)
        
        article_content = ""
        image_count = 0
        video_count = 0
        images = []
        
        if html_path:
            log_info(f"从CloudFront获取HTML: {html_path}")
            html_content = fetch_html_from_cloudfront(html_path)
            
            if html_content:
                article_data = extract_article_content(html_content)
                
                if article_data.get("content"):
                    image_dir = Path(ATTACHMENT_DIR)
                    
                    markdown, images, videos = html_to_markdown(article_data["content"], image_dir, short_name)
                    article_content = markdown
                    image_count = len(images)
                    video_count = videos
        
        if not article_content and content_text:
            log_info("使用API返回的content字段")
            article_content = content_text
        
        if not article_content:
            article_content = "*内容获取失败*"
        
        md_content = []
        
        if account_name:
            md_content.append(f"- **公众号**: #{account_name}")
        if publish_date:
            md_content.append(f"- **发布时间**: {publish_date}")
        md_content.append(f"- **原文链接**: [{title}]({wechat_url})")
        md_content.append(f"- [ ] **是否已读**")
        md_content.append(f"- **人工标签**: ")
        md_content.append(f"- **我的评价**: ")
        
        md_content = [line for line in md_content if line]
        
        md_content.append("")
        md_content.append("---")
        md_content.append("")
        md_content.append("# 正文")
        md_content.append("")
        md_content.append(article_content)
        
        md_content.append("")
        md_content.append("---")
        md_content.append(f"下载时间: {download_time}")
        md_content.append(f"唯一标识: {wechat_url}")
        
        final_md = normalize_markdown_output("\n".join(md_content))
        
        md_file = os.path.join(output_dir, md_filename)
        
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        
        log_info(f"保存成功: {md_file}")
        log_info(f"图片数量: {image_count}")
        
        return {
            "success": True,
            "title": title,
            "author": account_name,
            "md_file": md_file,
            "md_filename": md_filename,
            "image_count": image_count,
            "video_count": video_count,
            "images": images
        }
        
    except Exception as e:
        log_error(f"保存失败: {e}")
        return {"success": False, "error": str(e)}


def fetch_and_download_articles(
    headers,
    account_info,
    history,
    account_index=0,
    total_accounts=1,
    max_articles_override=None,
    output_dir_override=None
):
    """获取并下载公众号文章
    
    Args:
        headers: 请求头
        account_info: 公众号信息
        history: 下载历史
        account_index: 当前公众号索引（用于进度显示）
        total_accounts: 公众号总数（用于进度显示）
    """
    account_id = account_info['id']
    supplier_id = account_info['supplierId']
    account_name = account_info['name']
    short_name = account_info.get('short_name', account_name[:2])
    category = account_info.get('category', '其他')
    
    log_info(f"\n{'='*60}")
    log_info(f"[{account_index + 1}/{total_accounts}] 处理公众号: {account_name}")
    log_info(f"supplierId: {supplier_id}")
    log_info(f"{'='*60}")
    
    downloaded_urls = set(history.get(supplier_id, []))
    is_new_account = len(downloaded_urls) == 0
    
    if is_new_account:
        log_info(f"🆕 新公众号，将下载最近 {NEW_ACCOUNT_DOWNLOAD_COUNT} 篇文章")
    else:
        log_info(f"📚 已有下载记录 {len(downloaded_urls)} 条，本轮最多下载 {MAX_DOWNLOAD_PER_ACCOUNT} 篇新文章")
    
    articles = fetch_articles_until_downloaded(
        headers,
        account_info,
        history,
        max_articles_override=max_articles_override
    )
    log_info(f"📋 获取到 {len(articles)} 篇待下载文章")
    
    download_stats.total_articles += len(articles)
    
    if not articles:
        return 0
    
    downloaded_count = 0
    failed_count = 0
    total_to_download = len(articles)
    
    # 使用进度条（如果tqdm可用）
    article_iterator = enumerate(articles)
    if TQDM_AVAILABLE:
        article_iterator = tqdm(enumerate(articles), total=total_to_download, 
                                 desc=f"下载 {short_name}", unit="篇",
                                 bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
    
    for idx, article in article_iterator:
        url = article.get('url', '')
        title = article.get('title', '未知标题')
        task_key = url or f"{supplier_id}:{title}"
        update_task("wechat", task_key, "downloading", title=title, meta={"account": account_name, "category": category})
        
        if url in downloaded_urls:
            download_stats.add_skipped()
            update_task("wechat", task_key, "skipped_downloaded", title=title, meta={"account": account_name})
            continue
        
        result = save_article_from_api(article, short_name, category, output_dir_override=output_dir_override)
        
        if result.get('success'):
            add_to_history(history, supplier_id, url)
            save_download_history(history)
            downloaded_count += 1
            update_task("wechat", task_key, "markdown_saved", title=title, meta={"account": account_name, "path": result.get("md_file")})
            record_markdown(
                CATEGORY_WECHAT,
                result.get("md_file"),
                title=title,
                meta={
                    "account": account_name,
                    "category": category,
                    "url": url,
                    "image_count": result.get("image_count", 0),
                },
            )
            download_stats.add_success(
                account_name, category,
                images=result.get('image_count', 0),
                videos=result.get('video_count', 0),
                md_file=result.get('md_file')
            )
        else:
            failed_count += 1
            error_msg = result.get('error', '未知错误')
            update_task("wechat", task_key, "failed", title=title, meta={"account": account_name}, error=error_msg)
            download_stats.add_failed(account_name, category, error_msg)
            # 记录失败文章到重试日志
            add_failed_article({
                'url': url,
                'title': title,
                'account_name': account_name,
                'short_name': short_name,
                'category': category,
                'error': error_msg
            })
            log_error(f"❌ 下载失败: {title[:30]}... - {error_msg}")
        
        time.sleep(API_RATE_LIMIT_DELAY)
    
    log_info(f"\n📊 公众号 [{account_name}] 完成: 成功 {downloaded_count} 篇, 失败 {failed_count} 篇")
    return downloaded_count


# ============================================================
# AI 评价功能
# ============================================================

def remove_think_tags(text: str) -> str:
    """移除AI响应中的<think>标签内容
    
    Args:
        text: AI响应文本
    
    Returns:
        str: 清理后的文本
    """
    import re
    # 移除<think>...</think>标签及其内容
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # 移除多余的空行
    text = re.sub(r'\n\n+', '\n\n', text)
    return text.strip()


ARTICLE_ANALYSIS_PROMPT = """你是一位资深的投资研究员和深度思考者，擅长从文章中提炼核心价值、发现隐藏逻辑、建立跨领域联系。请对以下文章进行深度分析和评价。

【文章正文】
{text}

请从以下几个维度进行分析（总字数控制在500-800字）：

## 一、内容概要

用2-3句话精炼概括文章的核心内容和主旨。

## 二、值得关注的信息点

请列出3-5个特别值得关注的信息点、数据或观点，并简要说明为什么值得关注。
（请使用列表形式）

## 三、深度解读与思辨

请从以下角度中选择适合本文的角度进行深入分析（选择2-4个最相关的角度）：

1. **核心逻辑**：文章的核心论证逻辑是什么？是否严密？有什么漏洞或盲点？
2. **深层含义**：字里行间透露了哪些隐含信息？作者的立场和倾向是什么？
3. **趋势洞察**：揭示了哪些行业趋势或市场变化？对未来有何启示？
4. **历史/类比**：是否有类似的历史案例、其他行业案例可以类比？有什么相似之处和差异？
5. **反向思考**：如果结论相反，会是什么情况？有什么被忽略的可能性？
6. **关联联想**：这篇文章与其他领域、其他投资主题有什么联系？能否形成知识网络？

【输出要求】
- 语言简洁专业，有深度、有见解、有联想力
- 观点要有逻辑支撑，体现思辨性
- 选择最适合本文的分析角度，不必强行使用所有角度
- 可以使用Markdown格式（如 ## 标题、**粗体**、- 列表 等）
- **禁止使用表格**，请使用列表或段落形式呈现内容"""


def extract_article_content_from_md(md_content: str) -> str:
    """
    从Markdown内容中提取正文内容
    
    Args:
        md_content: Markdown文件内容
    
    Returns:
        正文内容（从"# 正文"后到最后一个分割线前）
    """
    content_start = md_content.find("# 正文")
    if content_start == -1:
        return ""
    
    content_start = md_content.find("\n", content_start)
    if content_start == -1:
        return ""
    
    last_separator = md_content.rfind("\n---")
    if last_separator == -1:
        return ""
    
    content = md_content[content_start:last_separator].strip()
    return content


def upgrade_headings(text: str) -> str:
    """
    提升标题层级：如果最高层级是一级标题，则所有标题都提升一级
    
    Args:
        text: 原始文本
    
    Returns:
        处理后的文本
    """
    lines = text.split('\n')
    
    heading_levels = []
    for line in lines:
        match = re.match(r'^(#{1,6})\s+', line)
        if match:
            heading_levels.append(len(match.group(1)))
    
    if not heading_levels:
        return text
    
    min_level = min(heading_levels)
    
    if min_level == 1:
        result_lines = []
        for line in lines:
            match = re.match(r'^(#{1,5})(\s+.*)$', line)
            if match:
                rest = match.group(2)
                new_line = '#' + match.group(1) + rest
                result_lines.append(new_line)
            else:
                result_lines.append(line)
        return '\n'.join(result_lines)
    
    return text


def generate_ai_analysis_for_article(
    md_file_path: str,
    ai_client: AIClient,
    log_func=None
) -> tuple:
    """
    为单个文章生成AI评价
    
    Args:
        md_file_path: Markdown文件路径
        ai_client: AI客户端实例
        log_func: 日志函数
    
    Returns:
        Tuple[文件路径, AI评价内容, 用时秒数]
    """
    start_time = time.time()
    
    if log_func is None:
        log_func = lambda msg, level: log_info(msg) if level == "INFO" else log_error(msg)
    
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        log_func(f"读取文件失败 {md_file_path}: {e}", "ERROR")
        return md_file_path, "", 0.0
    
    article_content = extract_article_content_from_md(content)
    
    if len(article_content) < 100:
        log_func(f"正文内容过短（{len(article_content)}字），跳过AI评价: {os.path.basename(md_file_path)}", "WARN")
        return md_file_path, "", 0.0
    
    article_content = article_content[:4000]
    
    prompt = ARTICLE_ANALYSIS_PROMPT.format(text=article_content)
    
    try:
        response, metadata = ai_client.call_for_long_thinking(
            prompt=prompt,
            temperature=0.7,
            max_tokens=2000,
            max_attempts_per_model=3
        )
        
        # 移除<think>标签内容
        cleaned_response = remove_think_tags(response)
        ok, reason = basic_ai_response_ok(cleaned_response, min_chars=80)
        if not ok:
            log_func(f"AI评价结果质量检查未通过，跳过写入: {reason}", "WARN")
            return md_file_path, "", 0.0
        processed_response = upgrade_headings(cleaned_response)
        
        elapsed = time.time() - start_time
        
        return md_file_path, processed_response, elapsed
        
    except Exception as e:
        log_func(f"AI评价生成失败: {e}", "ERROR")
        return md_file_path, "", 0.0


def insert_ai_analysis_to_md(md_file_path: str, ai_analysis: str) -> bool:
    """
    将AI评价插入到Markdown文件中
    
    插入位置：最后一个分割线之前（下载时间标识之前）
    
    Args:
        md_file_path: Markdown文件路径
        ai_analysis: AI评价内容
    
    Returns:
        是否成功更新
    """
    if not ai_analysis:
        return False
    
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        ai_section = f"\n---\n\n# AI 评价\n\n{ai_analysis}\n"
        
        last_separator = content.rfind("\n---")
        if last_separator != -1:
            new_content = content[:last_separator] + ai_section + content[last_separator:]
        else:
            new_content = content + ai_section
        
        with open(md_file_path, 'w', encoding='utf-8') as f:
            f.write(normalize_markdown_output(new_content))
        
        return True
            
    except Exception as e:
        log_error(f"插入AI评价失败 {md_file_path}: {e}")
        return False


_progress_counter_ai = 0
_progress_lock_ai = threading.Lock()


def _process_single_article_ai(
    md_file: str,
    ai_client: AIClient,
    total_files: int,
    log_func,
    delay_seconds: float = 0
) -> tuple:
    """
    处理单个文章AI评价的内部函数（用于并行调用）
    
    Args:
        md_file: 文件路径
        ai_client: AI客户端（线程安全）
        total_files: 总文件数
        log_func: 日志函数
        delay_seconds: 启动延迟秒数
    
    Returns:
        (文件路径, 是否成功, 用时秒数)
    """
    global _progress_counter_ai
    
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    
    md_file_path, ai_analysis, elapsed = generate_ai_analysis_for_article(
        md_file, ai_client, log_func
    )
    
    success = False
    if ai_analysis:
        success = insert_ai_analysis_to_md(md_file_path, ai_analysis)
    
    with _progress_lock_ai:
        _progress_counter_ai += 1
        status = "✅" if success else "⏭️"
        log_func(f"[{_progress_counter_ai}/{total_files}] {status} {os.path.basename(md_file)} ({elapsed:.1f}s)", "INFO")
    
    return md_file_path, success, elapsed


def process_ai_analysis_batch(
    md_files: list,
    log_func=None,
    max_workers: int = None
) -> dict:
    """
    并行批量为文章生成AI评价
    
    Args:
        md_files: Markdown文件路径列表
        log_func: 日志函数
        max_workers: 最大并行工作线程数
    
    Returns:
        Dict[文件路径, 是否成功]
    """
    global _progress_counter_ai
    _progress_counter_ai = 0
    
    if log_func is None:
        log_func = lambda msg, level: log_info(msg) if level == "INFO" else log_error(msg)
    
    if not md_files:
        log_func("没有需要AI评价的文章", "INFO")
        return {}
    
    if max_workers is None:
        max_workers = get_parallel_workers()
    
    log_func(f"\n{'='*60}", "INFO")
    log_func("🤖 开始AI评价处理", "INFO")
    log_func(f"{'='*60}", "INFO")
    log_func(f"待处理文章数: {len(md_files)}", "INFO")
    log_func(f"并行工作线程: {max_workers}", "INFO")
    
    try:
        ai_client = create_ai_client(log_func=log_func)
        log_func(f"AI客户端初始化成功 (提供商: {ai_client.get_provider()})", "INFO")
    except Exception as e:
        log_func(f"AI客户端初始化失败: {e}", "ERROR")
        return {}
    
    results = {}
    total_files = len(md_files)
    total_time = 0.0
    success_count = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {}
        for idx, md_file in enumerate(md_files):
            delay = idx * 1.0
            future = executor.submit(
                _process_single_article_ai,
                md_file,
                ai_client,
                total_files,
                log_func,
                delay
            )
            future_to_file[future] = md_file
        
        for future in as_completed(future_to_file):
            md_file = future_to_file[future]
            try:
                file_path, success, elapsed = future.result()
                results[file_path] = success
                total_time += elapsed
                if success:
                    success_count += 1
            except Exception as e:
                log_func(f"处理文件失败 {os.path.basename(md_file)}: {e}", "ERROR")
                results[md_file] = False
    
    log_func(f"\n📊 AI评价统计: 成功 {success_count}/{total_files} 篇, 总耗时 {total_time:.1f}s", "INFO")
    
    return results


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='Alpha派微信公众号文章下载工具')
    parser.add_argument('--auto', action='store_true', help='自动模式：下载所有配置的公众号')
    parser.add_argument('--single-account', type=str, default=None, help='只下载单个公众号；可填 supplierId、id 或完整公众号名称')
    parser.add_argument('--count', type=int, default=None, help='单账号模式下最多下载靠前的 N 篇；不填则按常规上限')
    parser.add_argument('--all', action='store_true', help='单账号模式下尽量下载所有能抓到且未下载的文章')
    parser.add_argument('--output-inbox', action='store_true', help='单账号模式下保存到 C微信文章\\0-Inbox')
    args = parser.parse_args()
    
    # 初始化统计
    download_stats.start()
    
    log_info("\n" + "=" * 60)
    log_info("📰 Alpha派微信公众号文章下载工具")
    log_info("=" * 60)
    
    # 清理过期日志
    clean_old_logs(LOG_DIR, LOG_RETENTION_DAYS)
    
    # 归档已读文章（在下载之前执行）
    archive_read_articles()
    
    # 清理过期的已读文章（归档后执行）
    clean_old_read_articles(days_threshold=READ_ARTICLE_RETENTION_DAYS)
    
    # 确保所有分类目录存在
    ensure_all_category_dirs()
    
    # 获取token
    token_data = get_token_local()
    if not token_data:
        log_error("❌ 无法获取token，退出")
        return 1
    
    headers = get_headers(token_data)
    
    # 获取公众号列表
    log_info("\n📋 获取订阅的公众号列表...")
    account_list = fetch_account_list(headers)
    if not account_list:
        log_error("❌ 获取公众号列表失败，退出")
        return 1
    
    log_info(f"✅ 获取到 {len(account_list)} 个订阅的公众号")

    if args.single_account:
        account = match_single_account(args.single_account, account_list)
        if not account:
            log_error(f"❌ 未匹配到公众号: {args.single_account}")
            return 1
        matched_accounts = [account]
        log_info(f"🎯 单账号模式: {account['name']} supplierId={account.get('supplierId')}")
    else:
        target_accounts = load_wechat_accounts_from_excel()
        if not target_accounts:
            log_error("❌ 没有配置公众号，退出")
            return 1
        matched_accounts = match_accounts(target_accounts, account_list)

    if not matched_accounts:
        log_error("❌ 没有匹配到任何公众号，退出")
        return 1
    
    download_stats.total_accounts = len(matched_accounts)
    log_info(f"\n✅ 匹配到 {download_stats.total_accounts} 个公众号")
    
    # 加载下载历史
    history = load_download_history()
    
    # 下载文章
    for idx, account_info in enumerate(matched_accounts):
        try:
            count = fetch_and_download_articles(
                headers, account_info, history,
                account_index=idx,
                total_accounts=download_stats.total_accounts,
                max_articles_override=(
                    10 ** 9 if args.single_account and args.all
                    else args.count if args.single_account
                    else None
                ),
                output_dir_override=(
                    os.path.join(WECHAT_ARTICLE_BASE_DIR, "0-Inbox")
                    if args.single_account and args.output_inbox
                    else None
                )
            )
        except Exception as e:
            account_name = account_info.get("name", account_info.get("supplierId", "未知公众号"))
            log_error(f"公众号处理失败，已跳过该账号并继续后续账号: {account_name}: {e}")
            download_stats.add_failed(account_name, account_info.get("category", "其他"), str(e))
            count = 0
        
        # 保存历史记录
        save_download_history(history)
        
        # API限流：公众号之间等待
        if idx < download_stats.total_accounts - 1:
            time.sleep(API_RATE_LIMIT_DELAY * 4)  # 公众号之间等待更长时间
    
    # 结束统计
    download_stats.end()
    
    # 生成统计报告
    report = download_stats.generate_report()
    log_info(report)
    
    # AI评价处理
    if download_stats.downloaded_files:
        def ai_log_func(msg, level="INFO"):
            prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
            log_info(f"  {prefix} {msg}")
        
        process_ai_analysis_batch(
            download_stats.downloaded_files,
            log_func=ai_log_func
        )
    
    # 发送Windows通知
    show_completion_notification(download_stats.success_count, download_stats.total_accounts)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

