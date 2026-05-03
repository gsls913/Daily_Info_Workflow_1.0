"""
Notion微信文章收集器
从Notion数据库读取微信文章链接，下载并保存到Obsidian

工作流程：
1. 连接Notion数据库
2. 查询所有"是否已下载"不为"已下载"的文章
3. 下载文章到Obsidian
4. 更新Notion状态为"已下载"
5. 归档已读文章
6. 发送Windows通知

依赖:
  pip install notion-client requests beautifulsoup4 Pillow tqdm
"""

import os
import sys
import re
import json
import shutil
import uuid
import time
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common_libs.config.config_loader import (
    get as cfg, get_retention_days, get_wechat_read_subfolders
)
from common_libs.utils.notifications import show_windows_notification
from common_libs.utils.paths import PROJECT_ROOT, WECHAT_ARTICLE_BASE_DIR, ATTACHMENT_DIR, NOTION_CONFIG_FILE, NOTION_WECHAT_HISTORY_FILE
from common_libs.utils.logging_config import setup_logging, clean_old_logs, get_log_functions
from common_libs.storage.download_history import save_json_atomic
from common_libs.article.article_manager import (
    check_if_read, extract_date_from_md, extract_images_from_md,
    archive_read_articles as _archive_read_articles,
    clean_old_read_articles as _clean_old_read_articles
)
from common_libs.wechat_downloader.wechat_to_md import download_wechat_article, is_sticker_article
from common_libs.ai.ai_client import AIClient, create_ai_client, get_parallel_workers, AICallType
from common_libs.ai.quality import basic_ai_response_ok
from common_libs.runtime.task_state import update_task

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_RETENTION_DAYS = get_retention_days('log')

FAILED_ARTICLES_FILE = os.path.join(SCRIPT_DIR, "failed_articles.json")

logger = setup_logging(LOG_DIR, log_prefix="notion_collector", retention_days=LOG_RETENTION_DAYS)
log_info, log_warn, log_error = get_log_functions(logger)

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

from notion_client import Client

CONFIG_FILE = NOTION_CONFIG_FILE
OUTPUT_DIR = os.path.join(WECHAT_ARTICLE_BASE_DIR, "0-Inbox")

READ_FOLDER_NAME = cfg('memo.read_folder_name', '已读')
READ_SUBFOLDERS = get_wechat_read_subfolders()
READ_ARTICLE_RETENTION_DAYS = get_retention_days('wechat_read_article')

DATABASE_NAME = cfg('notion.database_name', '微信收藏')
PROPERTY_DOWNLOADED = cfg('notion.property_downloaded', '是否已下载')
VALUE_DOWNLOADED = cfg('notion.value_downloaded', '已下载')
PROPERTY_URL = cfg('notion.property_url', '网址')
MAX_FAILED_ARTICLES = cfg('wechat.max_failed_articles', 500)


def load_processed_articles():
    """加载 Notion 微信文章本地处理记录，用于 Notion 状态更新失败后的去重恢复。"""
    if os.path.exists(NOTION_WECHAT_HISTORY_FILE):
        try:
            with open(NOTION_WECHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            history.setdefault("processed", {})
            return history
        except Exception as e:
            log_warn(f"读取 Notion 文章处理记录失败: {e}")
    return {"processed": {}}


def save_processed_articles(history):
    try:
        save_json_atomic(history, NOTION_WECHAT_HISTORY_FILE)
    except Exception as e:
        log_error(f"保存 Notion 文章处理记录失败: {e}")


def processed_key(page_id, url):
    return page_id or url


def is_processed_locally(history, page_id, url):
    processed = history.get("processed", {})
    key = processed_key(page_id, url)
    return bool(key and key in processed)


def record_processed_article(history, page_id, url, result=None, status="downloaded"):
    key = processed_key(page_id, url)
    if not key:
        return
    result = result or {}
    history.setdefault("processed", {})[key] = {
        "page_id": page_id,
        "url": url,
        "status": status,
        "md_file": result.get("md_file"),
        "md_filename": result.get("md_filename"),
        "title": result.get("title"),
        "processed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_processed_articles(history)


def mark_downloaded_with_recovery(notion, page_id, url, local_history, result=None, status="downloaded"):
    """先写本地成功记录，再更新 Notion；更新失败时下次运行可跳过重复下载并重试标记。"""
    record_processed_article(local_history, page_id, url, result=result, status=status)
    if not mark_as_downloaded(notion, page_id):
        log_warn("  ⚠️ Notion 状态更新失败；已写入本地处理记录，下次会重试标记并避免重复下载")
        return False
    return True


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


MAX_FAILED_ARTICLES = 500  # 最多保留500条失败记录


def add_failed_article(article_info):
    """添加失败文章到重试列表（带自动清理）
    
    Args:
        article_info: dict, 包含以下字段:
            - url: 文章URL
            - title: 文章标题
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


class DownloadStats:
    """下载统计类（线程安全）"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = None
        self.end_time = None
        self.total_articles = 0
        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.total_images = 0
        self.total_videos = 0
        self.errors = []
        self.downloaded_files = []
    
    def start(self):
        self.start_time = datetime.now()
    
    def end(self):
        self.end_time = datetime.now()
    
    def add_success(self, images=0, videos=0, md_file=None):
        with self._lock:
            self.success_count += 1
            self.total_images += images
            self.total_videos += videos
            if md_file:
                self.downloaded_files.append(md_file)
    
    def add_failed(self, title, error):
        with self._lock:
            self.failed_count += 1
            self.errors.append({'title': title[:50] if title else '未知', 'error': str(error)[:100]})
    
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
        report.append(f"📄 处理文章: {self.total_articles} 篇")
        report.append("-" * 40)
        report.append(f"✅ 成功下载: {self.success_count} 篇")
        report.append(f"❌ 下载失败: {self.failed_count} 篇")
        report.append(f"⏭️  跳过: {self.skipped_count} 篇")
        report.append(f"🖼️  下载图片: {self.total_images} 张")
        report.append(f"🎥 包含视频: {self.total_videos} 个")
        report.append("-" * 40)
        report.append(f"📁 输出目录: {OUTPUT_DIR}")
        report.append(f"🖼️  图片目录: {ATTACHMENT_DIR}")
        
        if self.errors and len(self.errors) <= 5:
            report.append("-" * 40)
            report.append("⚠️  错误详情:")
            for err in self.errors:
                report.append(f"   {err['title']}: {err['error']}")
        
        report.append("=" * 60)
        
        return "\n".join(report)


download_stats = DownloadStats()


def ensure_read_folder_structure():
    from common_libs.article.article_manager import ensure_read_folder_structure as _ensure
    _ensure(WECHAT_ARTICLE_BASE_DIR, READ_SUBFOLDERS)


def archive_read_articles():
    log_info("\n📁 开始归档已读文章...")
    ensure_read_folder_structure()

    if not os.path.exists(OUTPUT_DIR):
        log_info("  Inbox文件夹不存在，跳过归档")
        return 0

    total_archived = 0
    md_files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.md')]

    if not md_files:
        log_info("  Inbox中没有文章，跳过归档")
        return 0

    for md_file in md_files:
        md_file_path = os.path.join(OUTPUT_DIR, md_file)

        if check_if_read(md_file_path):
            read_folder = os.path.join(WECHAT_ARTICLE_BASE_DIR, READ_FOLDER_NAME, "6-其他")
            dest_path = os.path.join(read_folder, md_file)

            if os.path.exists(dest_path):
                log_warn(f"目标文件已存在，跳过: {md_file}")
                continue

            try:
                shutil.move(md_file_path, dest_path)
                total_archived += 1
                log_info(f"  ✓ 已归档: {md_file} → 已读/6-其他/")
            except Exception as e:
                log_error(f"移动文件失败 {md_file}: {e}")

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
        log_info(f"📊 清理统计: 总计删除 {total_deleted_articles} 篇过期文章, {total_deleted_images} 张图片")
    return total_deleted_articles, total_deleted_images


def load_config() -> tuple:
    """加载配置文件
    
    Returns:
        tuple: (notion_token, database_id)
    
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置项缺失或无效
    """
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"配置文件不存在: {CONFIG_FILE}\n"
            f"请创建配置文件并添加以下内容:\n"
            f"NotionToken your_notion_token_here\n"
            f"微信收藏 your_database_id_here"
        )
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
    except Exception as e:
        raise RuntimeError(f"读取配置文件失败: {e}")
    
    notion_token = None
    database_id = None
    
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "NotionToken" in line:
            parts = line.split()
            if len(parts) >= 2:
                notion_token = parts[-1]
            else:
                log_warn(f"⚠️  行{line_num}: NotionToken 格式错误")
        elif DATABASE_NAME in line:
            parts = line.split()
            if len(parts) >= 2:
                database_id = parts[-1]
            else:
                log_warn(f"⚠️  行{line_num}: {DATABASE_NAME} 格式错误")
    
    if not notion_token:
        raise ValueError(
            f"未找到 Notion Token\n"
            f"请在配置文件中添加:\n"
            f"NotionToken your_notion_token_here"
        )
    if not database_id:
        raise ValueError(
            f"未找到数据库ID: {DATABASE_NAME}\n"
            f"请在配置文件中添加:\n"
            f"{DATABASE_NAME} your_database_id_here"
        )
    
    # 验证格式
    if not notion_token.startswith("secret_"):
        log_warn("⚠️  Notion Token 格式可能不正确（应以 'secret_' 开头）")
    
    if len(database_id.replace("-", "")) != 32:
        log_warn("⚠️  数据库ID 格式可能不正确（应为32位字符）")
    
    return notion_token, database_id


def query_pending_articles(notion: Client, database_id: str) -> list:
    """查询未下载的文章（带重试机制）
    
    Args:
        notion: Notion客户端
        database_id: 数据库ID
    
    Returns:
        list: 文章列表
    """
    articles = []
    has_more = True
    start_cursor = None
    max_retries = 3
    retry_delay = 2
    
    while has_more:
        for attempt in range(max_retries):
            try:
                response = notion.databases.query(
                    database_id=database_id,
                    filter={
                        "property": PROPERTY_DOWNLOADED,
                        "select": {
                            "does_not_equal": VALUE_DOWNLOADED
                        }
                    },
                    start_cursor=start_cursor,
                    page_size=100
                )
                
                articles.extend(response.get("results", []))
                
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
                
                break  # 成功则跳出重试循环
                
            except Exception as e:
                error_msg = str(e)
                
                # 判断错误类型
                if "rate limit" in error_msg.lower():
                    # API限流，等待更长时间
                    wait_time = retry_delay * (attempt + 1) * 2
                    log_warn(f"⚠️  API限流，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    # 其他错误，正常重试
                    log_warn(f"⚠️  查询失败 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}")
                    time.sleep(retry_delay)
                else:
                    # 最后一次重试失败
                    log_error(f"❌ 查询数据库失败: {error_msg}")
                    has_more = False
                    break
    
    return articles


def mark_as_downloaded(notion: Client, page_id: str) -> bool:
    """标记文章为已下载（带重试机制）
    
    Args:
        notion: Notion客户端
        page_id: 页面ID
    
    Returns:
        bool: 是否成功
    """
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            notion.pages.update(
                page_id=page_id,
                properties={
                    PROPERTY_DOWNLOADED: {
                        "select": {
                            "name": VALUE_DOWNLOADED
                        }
                    }
                }
            )
            return True
            
        except Exception as e:
            error_msg = str(e)
            
            # 判断错误类型
            if "rate limit" in error_msg.lower():
                # API限流，等待更长时间
                wait_time = retry_delay * (attempt + 1) * 2
                log_warn(f"⚠️  API限流，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            elif attempt < max_retries - 1:
                # 其他错误，正常重试
                log_warn(f"⚠️  更新状态失败 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}")
                time.sleep(retry_delay)
            else:
                # 最后一次重试失败
                log_error(f"❌ 更新状态失败: {error_msg}")
                return False
    
    return False


def extract_url_from_page(page: dict) -> str:
    """从页面中提取URL"""
    props = page.get("properties", {})
    url_prop = props.get(PROPERTY_URL, {})
    return url_prop.get("url", "")


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
    """从Markdown内容中提取正文内容"""
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
    """提升标题层级：如果最高层级是一级标题，则所有标题都提升一级"""
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


def generate_ai_analysis_for_article(md_file_path: str, ai_client: AIClient, log_func=None) -> tuple:
    """为单个文章生成AI评价"""
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
    """将AI评价插入到Markdown文件中"""
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
            f.write(new_content)
        
        return True
            
    except Exception as e:
        log_error(f"插入AI评价失败 {md_file_path}: {e}")
        return False


_progress_counter_ai = 0
_progress_lock_ai = threading.Lock()


def _process_single_article_ai(md_file: str, ai_client: AIClient, total_files: int, log_func, delay_seconds: float = 0) -> tuple:
    """处理单个文章AI评价的内部函数"""
    global _progress_counter_ai
    
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    
    md_file_path, ai_analysis, elapsed = generate_ai_analysis_for_article(md_file, ai_client, log_func)
    
    success = False
    if ai_analysis:
        success = insert_ai_analysis_to_md(md_file_path, ai_analysis)
    
    with _progress_lock_ai:
        _progress_counter_ai += 1
        status = "✅" if success else "⏭️"
        log_func(f"[{_progress_counter_ai}/{total_files}] {status} {os.path.basename(md_file)} ({elapsed:.1f}s)", "INFO")
    
    return md_file_path, success, elapsed


def process_ai_analysis_batch(md_files: list, log_func=None, max_workers: int = None) -> dict:
    """并行批量为文章生成AI评价"""
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
    download_stats.start()
    
    log_info("\n" + "=" * 60)
    log_info("📥 Notion微信文章收集器")
    log_info("=" * 60)
    
    clean_old_logs(LOG_DIR, LOG_RETENTION_DAYS)
    
    ensure_read_folder_structure()
    
    archive_read_articles()
    
    clean_old_read_articles()
    
    log_info("\n⏳ 加载配置...")
    try:
        notion_token, database_id = load_config()
        log_info(f"✅ 配置加载成功")
        log_info(f"   数据库: {DATABASE_NAME}")
    except Exception as e:
        log_error(f"配置加载失败: {e}")
        return
    
    log_info("\n⏳ 连接Notion...")
    try:
        notion = Client(auth=notion_token)
        db_info = notion.databases.retrieve(database_id=database_id)
        log_info(f"✅ 连接成功: {db_info.get('title', [{}])[0].get('plain_text', DATABASE_NAME)}")
    except Exception as e:
        log_error(f"连接失败: {e}")
        return
    
    log_info(f"\n⏳ 查询未下载的文章...")
    articles = query_pending_articles(notion, database_id)
    
    if not articles:
        log_info("✅ 没有待处理的文章")
        download_stats.end()
        return
    
    download_stats.total_articles = len(articles)
    log_info(f"✅ 找到 {len(articles)} 篇待处理文章")
    processed_history = load_processed_articles()
    
    article_iterator = enumerate(articles, 1)
    if TQDM_AVAILABLE:
        article_iterator = tqdm(
            enumerate(articles, 1), 
            total=len(articles),
            desc="下载文章",
            unit="篇",
            bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
        )
    
    for i, page in article_iterator:
        page_id = page.get("id", "")
        url = extract_url_from_page(page)
        task_key = page_id or url or f"notion_item_{i}"
        
        if not TQDM_AVAILABLE:
            log_info(f"\n{'='*50}")
            log_info(f"[{i}/{len(articles)}] 处理中...")
            log_info(f"链接: {url[:60]}..." if url else "链接: 无")
        
        if not url:
            if not TQDM_AVAILABLE:
                log_warn("  无有效链接，已跳过")
            download_stats.add_skipped()
            mark_downloaded_with_recovery(notion, page_id, url, processed_history, status="skipped_no_url")
            update_task("notion_wechat", task_key, "skipped_no_url", title="无链接")
            continue
        
        if "mp.weixin.qq.com" not in url:
            if not TQDM_AVAILABLE:
                log_warn("  非微信文章链接，已跳过")
            download_stats.add_skipped()
            mark_downloaded_with_recovery(notion, page_id, url, processed_history, status="skipped_non_wechat")
            update_task("notion_wechat", task_key, "skipped_non_wechat", title=url)
            continue

        if is_processed_locally(processed_history, page_id, url):
            if not TQDM_AVAILABLE:
                log_warn("  本地记录显示已处理，跳过重复下载并重试标记 Notion")
            download_stats.add_skipped()
            mark_as_downloaded(notion, page_id)
            update_task("notion_wechat", task_key, "skipped_local_processed", title=url)
            continue
        
        try:
            update_task("notion_wechat", task_key, "downloading", title=url, meta={"url": url})
            result = download_wechat_article(
                url=url,
                output_dir=OUTPUT_DIR,
                obsidian_mode=True,
                attachment_dir=ATTACHMENT_DIR
            )
            
            if result.get("success"):
                if not TQDM_AVAILABLE:
                    log_info(f"  ✅ 下载成功: {result.get('md_filename', '')}")
                download_stats.add_success(
                    images=result.get('image_count', 0),
                    videos=result.get('video_count', 0),
                    md_file=result.get('md_file')
                )
                mark_downloaded_with_recovery(notion, page_id, url, processed_history, result=result)
                update_task("notion_wechat", task_key, "markdown_saved", title=result.get("title", url), meta={"path": result.get("md_file")})
            else:
                error = result.get("error", "未知错误")
                if "贴图文章" in error:
                    if not TQDM_AVAILABLE:
                        log_warn(f"  ⚠️ 贴图文章，已跳过")
                    download_stats.add_skipped()
                    mark_downloaded_with_recovery(notion, page_id, url, processed_history, result=result, status="skipped_sticker")
                    update_task("notion_wechat", task_key, "skipped_sticker", title=result.get("title", url))
                else:
                    if not TQDM_AVAILABLE:
                        log_error(f"  ❌ 下载失败: {error}")
                    download_stats.add_failed(result.get('title', '未知'), error)
                    update_task("notion_wechat", task_key, "failed", title=result.get("title", url), error=error)
                    add_failed_article({
                        'url': url,
                        'title': result.get('title', '未知'),
                        'error': error
                    })
                    
        except Exception as e:
            if not TQDM_AVAILABLE:
                log_error(f"  ❌ 下载异常: {e}")
            download_stats.add_failed('未知', str(e))
            update_task("notion_wechat", task_key, "failed", title=url, error=str(e))
            add_failed_article({
                'url': url,
                'title': '未知',
                'error': str(e)
            })
    
    download_stats.end()
    
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
    
    show_windows_notification(
        "Notion微信文章收集完成",
        f"成功下载 {download_stats.success_count} 篇，跳过 {download_stats.skipped_count} 篇，失败 {download_stats.failed_count} 篇"
    )


if __name__ == "__main__":
    main()
