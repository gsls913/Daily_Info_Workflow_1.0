"""
Daily Info Workflow System - 统一工作流启动器
=====================================
功能：
1. 依次执行五个信息收集脚本
2. 提供统一的错误处理和日志记录
3. 显示进度和统计信息
4. 支持错误恢复和断点续传
5. 提供详细的错误诊断和建议

执行顺序：
1. AlphaPai会议纪要下载 (alphapai_download.py)
2. Notion微信文章收集 (notion_collector.py)
3. Notion链接收藏收集 (notion_link_collector.py)
4. Alpha派微信公众号文章下载 (fetch_wechat_articles.py)
5. 小宇宙播客处理 (podcast_workflow.py)

使用方法：
  python run_workflow.py              # 执行完整工作流
  python run_workflow.py --step 1     # 只执行第1步
  python run_workflow.py --step 2     # 只执行第2步
  python run_workflow.py --step 3     # 只执行第3步
  python run_workflow.py --step 4     # 只执行第4步
  python run_workflow.py --step 5     # 只执行第5步
  python run_workflow.py --resume     # 从上次失败处继续执行
  python run_workflow.py --status     # 查看上次执行状态
"""

import os
import sys
import time
import json
import argparse
import subprocess
import traceback
import unicodedata
import shutil
import msvcrt
import re
from datetime import datetime
from pathlib import Path
import yaml

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from investment_system.common.utils.notifications import show_windows_notification
from investment_system.common.ai.health_check import check_ai_health
from investment_system.common.config.config_loader import get as cfg
from investment_system.common.config.config_loader import reload_config
from investment_system.common.config.source_config import (
    SourceConfigError,
    add_podcast_account,
    add_tag_option,
    add_wechat_account,
    load_podcast_accounts,
    load_tag_options,
    load_wechat_accounts,
    remove_podcast_account,
    remove_tag_option,
    remove_wechat_account,
)
from investment_system.common.config.validator import validate_config
from investment_system.common.runtime.daily_digest import (
    archive_read_daily_digests,
    claim_first_full_run_today,
    claim_first_full_run_this_week,
    generate_daily_digest,
    generate_weekly_digest,
    STATE_FILE as DAILY_DIGEST_STATE_FILE,
)
from investment_system.common.runtime.daily_report import write_workflow_report
from investment_system.common.runtime.last_downloads import (
    CATEGORY_ALPHA_MEMO,
    CATEGORY_PODCAST,
    CATEGORY_WECHAT,
    load_manifest as load_last_download_manifest,
    save_manifest as save_last_download_manifest,
    start_new_manifest as start_new_download_manifest,
)
from investment_system.common.runtime.maintenance import run_startup_maintenance
from investment_system.common.runtime.obsidian_entity_maintenance import (
    ENTITY_COMPANY,
    ENTITY_INDUSTRY,
    ENTITY_STRATEGY,
    create_info_collector_entity,
    create_investment_notes_entity,
    create_windows_shortcuts,
    info_collector_shortcut_specs,
    investment_entity_dir_name,
    investment_notes_shortcut_specs,
    investment_template_files,
    list_investment_category_dirs,
    scan_entity,
    shortcut_staging_dir,
)
from investment_system.common.runtime.recycle_bin import move_to_recycle_bin, recycle_bin_root
from investment_system.common.runtime.task_result import TaskResult
from investment_system.common.storage.download_history import save_json_atomic
from investment_system.common.article.article_manager import extract_images_from_md
from investment_system.common.utils.paths import (
    MEMO_HISTORY_FILE, WECHAT_HISTORY_FILE,
    PODCAST_HISTORY_FILE, ATTACHMENT_DIR, MEMO_BASE_DIR,
    WECHAT_ARTICLE_BASE_DIR, PODCAST_BASE_DIR,
    WORKFLOW_PROGRESS_FILE, WORKFLOW_ERROR_LOG_FILE,
    SET_CONFIG_FILE,
    PROJECT_ROOT as PATHS_PROJECT_ROOT
)
from investment_system.launcher.launcher_ui import (
    UI_ACCENT,
    fit_text as _fit_text,
    print_box as _print_box,
    print_rule as _print_rule,
    style as _style,
    terminal_width as _terminal_width,
    ui_error,
    ui_menu,
    ui_panel,
    ui_pause_confirm,
    ui_prompt,
    ui_success,
    ui_warn,
    wrap_text as _wrap_text,
)
from investment_system.launcher.workflow_steps import (
    TRACKED_MARKDOWN_STEP_INDICES,
    build_workflow_steps,
)

PROGRESS_FILE = WORKFLOW_PROGRESS_FILE
ERROR_LOG_FILE = WORKFLOW_ERROR_LOG_FILE
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config", "config.yaml")
AI_MODELS_CONFIG_FILE = os.path.join(PROJECT_ROOT, "data", "config", "ai_models.json")
REPORT_DIR = os.path.join(PROJECT_ROOT, "data", "reports")
DEPENDENCY_STATUS_TEXT = ""
UI_STATUS_PENDING = "\033[38;5;39m"
UI_STATUS_DONE = "\033[38;5;82m"

WORKFLOW_STEPS = build_workflow_steps(PROJECT_ROOT)


class ProgressManager:
    """进度管理器 - 负责保存和恢复执行进度"""

    @staticmethod
    def _ensure_progress_dir():
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)

    @staticmethod
    def _write_progress(progress):
        ProgressManager._ensure_progress_dir()
        save_json_atomic(progress, PROGRESS_FILE)

    @staticmethod
    def save_step_started(step_idx, step_name):
        """记录步骤已开始，便于异常中断后恢复"""
        progress = ProgressManager.load_progress()
        progress["steps"][str(step_idx)] = {
            "name": step_name,
            "status": "running",
            "success": False,
            "error": None,
            "elapsed": 0.0,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        progress["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        progress["completed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "success" or s.get("success") is True
        ]
        progress["failed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") in ("failed", "running") or (s.get("success") is False and s.get("error"))
        ]
        progress["running_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "running"
        ]

        try:
            ProgressManager._write_progress(progress)
        except Exception as e:
            print(f"⚠️  保存进度失败: {e}")
    
    @staticmethod
    def save_progress(step_idx, step_name, success, error_msg=None, elapsed=0.0):
        """保存执行进度"""
        progress = ProgressManager.load_progress()
        
        progress["steps"][str(step_idx)] = {
            "name": step_name,
            "status": "success" if success else "failed",
            "success": success,
            "error": error_msg,
            "elapsed": elapsed,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        progress["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        progress["completed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "success" or s.get("success") is True
        ]
        progress["failed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "failed" or (s.get("success") is False and s.get("error"))
        ]
        progress["running_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "running"
        ]
        
        try:
            ProgressManager._write_progress(progress)
        except Exception as e:
            print(f"⚠️  保存进度失败: {e}")
    
    @staticmethod
    def load_progress():
        """加载执行进度"""
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        return {
            "steps": {},
            "last_update": None,
            "completed_steps": [],
            "failed_steps": [],
            "running_steps": []
        }
    
    @staticmethod
    def get_last_failed_step():
        """获取需要恢复的最早失败/中断步骤"""
        progress = ProgressManager.load_progress()
        failed_steps = progress.get("failed_steps", [])
        running_steps = progress.get("running_steps", [])
        candidates = []
        for item in failed_steps + running_steps:
            try:
                candidates.append(int(item))
            except (TypeError, ValueError):
                pass
        
        if candidates:
            return min(candidates)
        return None
    
    @staticmethod
    def clear_progress():
        """清空进度记录"""
        ProgressManager._ensure_progress_dir()
        if os.path.exists(PROGRESS_FILE):
            try:
                os.remove(PROGRESS_FILE)
            except Exception:
                pass


class ErrorAnalyzer:
    """错误分析器 - 分析错误并提供诊断建议"""
    
    ERROR_PATTERNS = {
        "token_expired": {
            "patterns": ["token", "401", "unauthorized", "登录", "认证"],
            "diagnosis": "登录凭证过期或无效",
            "suggestions": [
                "检查配置文件中的登录凭证是否正确",
                "尝试手动运行脚本重新登录",
                "检查账号是否被锁定或限制"
            ]
        },
        "network_error": {
            "patterns": ["network", "connection", "timeout", "网络", "连接", "超时"],
            "diagnosis": "网络连接问题",
            "suggestions": [
                "检查网络连接是否正常",
                "检查防火墙或代理设置",
                "稍后重试（服务器可能暂时不可用）"
            ]
        },
        "playwright_error": {
            "patterns": ["playwright", "browser", "chromium", "浏览器"],
            "diagnosis": "Playwright浏览器问题",
            "suggestions": [
                "运行: playwright install chromium",
                "检查系统是否支持无头浏览器",
                "更新Playwright: pip install --upgrade playwright"
            ]
        },
        "module_not_found": {
            "patterns": ["modulenotfounderror", "no module named", "importerror"],
            "diagnosis": "Python依赖包缺失",
            "suggestions": [
                "检查requirements.txt是否完整安装",
                "运行: pip install -r requirements.txt",
                "检查Python环境是否正确"
            ]
        },
        "file_not_found": {
            "patterns": ["filenotfounderror", "no such file", "文件不存在"],
            "diagnosis": "文件或目录不存在",
            "suggestions": [
                "检查配置文件路径是否正确",
                "检查输出目录是否存在",
                "检查文件权限"
            ]
        },
        "excel_error": {
            "patterns": ["excel", "openpyxl", "xlsx"],
            "diagnosis": "Excel文件问题",
            "suggestions": [
                "检查Excel文件是否存在",
                "检查Excel文件格式是否正确",
                "安装openpyxl: pip install openpyxl"
            ]
        }
    }
    
    @staticmethod
    def analyze_error(error_msg, step_info):
        """分析错误并返回诊断结果"""
        if not error_msg:
            return None
        
        error_lower = error_msg.lower()
        
        for error_type, config in ErrorAnalyzer.ERROR_PATTERNS.items():
            for pattern in config["patterns"]:
                if pattern in error_lower:
                    return {
                        "type": error_type,
                        "diagnosis": config["diagnosis"],
                        "suggestions": config["suggestions"],
                        "original_error": error_msg[:500]
                    }
        
        return {
            "type": "unknown",
            "diagnosis": "未知错误",
            "suggestions": [
                "查看详细错误日志",
                "检查脚本是否正常运行",
                "联系开发者获取支持"
            ],
            "original_error": error_msg[:500]
        }
    
    @staticmethod
    def save_error_log(step_idx, step_name, error_msg, diagnosis=None):
        """保存错误日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        jsonl_path = Path(ERROR_LOG_FILE).with_suffix(".jsonl")
        
        log_entry = f"""
{'='*70}
时间: {timestamp}
步骤: {step_idx} - {step_name}
{'='*70}
错误信息:
{error_msg}
"""
        
        if diagnosis:
            log_entry += f"""
诊断结果:
{diagnosis['diagnosis']}

建议解决方案:
"""
            for idx, suggestion in enumerate(diagnosis['suggestions'], 1):
                log_entry += f"  {idx}. {suggestion}\n"
        
        log_entry += "\n"
        
        try:
            os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)
            with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            structured = {
                "timestamp": timestamp,
                "step": step_idx,
                "name": step_name,
                "error": str(error_msg)[:4000],
                "diagnosis": diagnosis or {},
            }
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(structured, ensure_ascii=False) + "\n")
        except Exception:
            pass


def print_header(title):
    """打印标题头"""
    ui_panel(title, [])


def _latest_report_label() -> str:
    report_dir = Path(REPORT_DIR)
    reports = sorted(report_dir.glob("daily_run_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return "暂无运行摘要"
    latest = reports[0]
    try:
        ts = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%m-%d %H:%M")
        return f"{ts}  {latest.name}"
    except Exception:
        return latest.name


def _latest_run_label() -> str:
    report_dir = Path(REPORT_DIR)
    reports = sorted(report_dir.glob("daily_run_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return "暂无记录"
    latest = reports[0]
    try:
        return datetime.fromtimestamp(latest.stat().st_mtime).strftime("%m-%d %H:%M")
    except Exception:
        return latest.name


def _current_ai_label() -> str:
    path = Path(AI_MODELS_CONFIG_FILE)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        provider = config.get("ai_provider", "modelscope")
        provider_label = ai_provider_display(provider) if "ai_provider_display" in globals() else provider
        if provider == "zhongxin":
            model = config.get("zhongxin_default_model", "")
        elif provider == "minimax":
            model = config.get("minimax_default_model", "")
        elif provider == "huoshan":
            model = "kimi-k2.5"
        else:
            order = config.get("modelscope_model_order", {})
            model = ((order.get("long_thinking") or order.get("short_text") or order.get("tag_judgment") or [""])[0])
        return f"{provider_label}{' / ' + model if model else ''}"
    except Exception:
        return "未读取到 AI 配置"


def _full_run_today_label() -> str:
    today_compact = datetime.now().strftime("%Y%m%d")
    report_dir = Path(REPORT_DIR)
    reports = sorted(report_dir.glob(f"daily_run_{today_compact}_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for report in reports:
        try:
            text = report.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if (
            "- 运行模式: 正式运行" in text
            and "- 步骤数: 5" in text
            and "- 成功: 5" in text
            and "- 失败: 0" in text
        ):
            try:
                return "已完整运行 " + datetime.fromtimestamp(report.stat().st_mtime).strftime("%H:%M")
            except Exception:
                return "已完整运行"

    try:
        state = json.loads(Path(DAILY_DIGEST_STATE_FILE).read_text(encoding="utf-8"))
    except Exception:
        state = {}
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("first_full_run_date") == today or state.get("digest_generated_date") == today:
        at = state.get("first_full_run_at") or state.get("digest_finished_at") or ""
        if at:
            try:
                return "已完整运行 " + datetime.fromisoformat(at[:19]).strftime("%H:%M")
            except Exception:
                return "已完整运行"
        return "已完整运行"
    if state.get("candidate_full_run_date") == today and state.get("digest_status") == "pending":
        return "今天有完整运行候选，日报待生成"
    return "今天尚未完整运行"


def _display_project_dir() -> str:
    return os.path.basename(PROJECT_ROOT.rstrip("\\/")) or PROJECT_ROOT


def _pixel_logo_lines() -> list[str]:
    return [
        "      ██        ██",
        "  ██  ██  ██    ██",
        "  ██████████  ██████",
        "  ██  ██  ██    ██",
        "      ██        ██",
        "   Info Flow Console",
    ]


def _startup_dashboard_lines(width: int) -> list[str]:
    ai_label = _current_ai_label()
    latest_run = _latest_run_label()
    full_run_today = _full_run_today_label()
    full_run_status = _style(full_run_today, UI_STATUS_DONE if full_run_today.startswith("已完整运行") else UI_STATUS_PENDING)
    if width >= 86:
        inner_width = width - 2
        left_width = 32
        gap = 3
        right_width = inner_width - left_width - gap
        left_lines = _pixel_logo_lines()
        right_lines = [
            "Daily Info Workflow System",
            "日常信息收集控制台",
            "",
            f"当前 AI   {ai_label}",
            f"上次运行  {latest_run}",
            f"今日状态  {full_run_status}",
        ]
        row_count = max(len(left_lines), len(right_lines))
        lines = []
        for idx in range(row_count):
            left = left_lines[idx] if idx < len(left_lines) else ""
            right = right_lines[idx] if idx < len(right_lines) else ""
            lines.append(
                _fit_text(left, left_width)
                + " " * gap
                + _fit_text(right, right_width)
            )
        return lines

    return [
        "  Daily Info Workflow System  ·  日常信息收集控制台",
        "",
        *_pixel_logo_lines(),
        "",
        f"  当前 AI   {_fit_text(ai_label, max(16, width - 14)).rstrip()}",
        f"  上次运行  {latest_run}",
        f"  今日状态  {full_run_status}",
    ]


def print_startup_dashboard():
    """打印自适应启动首页。"""
    width = _terminal_width()
    print()
    _print_box(_startup_dashboard_lines(width), title="信息流控制台", width=width)


def _menu_option_line(key: str, label: str) -> str:
    return _style(f"  {key:<3}{label}", UI_ACCENT)


def _main_mode_lines() -> list[str]:
    return [
        _menu_option_line("1", "今日完整信息流"),
        "     运行 5 个信息收集步骤，并可选择结束后的睡眠、休眠或关机。",
        "",
        _menu_option_line("2", "单独下载与转换"),
        "     不启动完整工作流，用于 AlphaPai 链接、有道云、单个公众号/播客等。",
        "",
        _menu_option_line("3", "维护与清理"),
        "     清理下载记录、删除上次下载内容、管理本地 Markdown。",
        "",
        _menu_option_line("4", "配置与状态"),
        "     查看运行摘要，调整程序参数和 AI API / 模型配置。",
    ]


def _workflow_action_lines() -> list[str]:
    return [
        _menu_option_line("1", "立即运行完整工作流"),
        "     运行结束后不做额外电脑动作。",
        "",
        _menu_option_line("2", "运行完整工作流，结束 5 分钟后睡眠"),
        "     适合临时离开电脑。",
        "",
        _menu_option_line("3", "运行完整工作流，结束 5 分钟后休眠"),
        "     适合睡前，电脑还有未完成任务需要保存现场。",
        "",
        _menu_option_line("4", "运行完整工作流，结束 5 分钟后关机"),
        "     适合睡前且无需保留电脑工作状态。",
        "",
        _menu_option_line("0", "返回主菜单"),
    ]


def print_start_screen():
    width = _terminal_width()
    print("\033[2J\033[H", end="")
    _print_box(_startup_dashboard_lines(width), title="信息流控制台", width=width)
    print()
    _print_box(_main_mode_lines(), title="主菜单 > 模式选择", width=width)
    print()


def _enter_tui_screen():
    print("\033[?1049h\033[?25l", end="", flush=True)


def _exit_tui_screen():
    print("\033[?25h\033[?1049l", end="", flush=True)


def _power_choice_from_menu(choice: str) -> dict | None:
    if choice == "1":
        return {
            'mode': 1,
            'action': 'none',
            'delay': 0,
            'description': '正常运行，结束后无额外操作'
        }
    if choice == "2":
        return {
            'mode': 2,
            'action': 'sleep',
            'delay': 300,
            'description': '运行结束后5分钟睡眠'
        }
    if choice == "3":
        return {
            'mode': 3,
            'action': 'hibernate',
            'delay': 300,
            'description': '运行结束后5分钟休眠'
        }
    if choice == "4":
        return {
            'mode': 4,
            'action': 'shutdown',
            'delay': 300,
            'description': '运行结束后5分钟关机'
        }
    return None


def _read_single_key_menu(valid_chars: set[str], prompt_label: str, redraw_func, help_func=None) -> str:
    typed = ""
    last_width = None
    redraw_func()
    while True:
        width = _terminal_width()
        if width != last_width:
            redraw_func()
            last_width = width
        prompt = _style("› ", UI_ACCENT) + f"{prompt_label}: {typed}"
        print("\r\033[K" + prompt, end="", flush=True)

        if msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if char == "\x1b":
                while msvcrt.kbhit():
                    msvcrt.getwch()
                continue
            if char in ("\r", "\n"):
                selected = typed.strip()
                if selected in valid_chars:
                    print()
                    return selected
                typed = ""
                redraw_func()
                ui_error(f"无效选项，请输入 {'、'.join(sorted(valid_chars))}")
                last_width = None
                continue
            if char == "\x03":
                typed = ""
                redraw_func()
                ui_success("已取消当前输入。")
                last_width = None
                continue
            if char == "\x08":
                typed = typed[:-1]
                continue
            if char == "?" and "?" in valid_chars:
                print()
                if help_func:
                    help_func()
                typed = ""
                last_width = None
                continue
            if char in valid_chars and char != "?":
                typed = char
                continue
        time.sleep(0.08)


def _print_workflow_action_screen():
    width = _terminal_width()
    print("\033[2J\033[H", end="")
    _print_box(_startup_dashboard_lines(width), title="信息流控制台", width=width)
    print()
    _print_box(_workflow_action_lines(), title="主菜单 > 今日完整信息流", width=width)
    print()


def _get_workflow_action_choice_tui() -> dict | None:
    while True:
        choice = _read_single_key_menu(
            {"0", "1", "2", "3", "4"},
            "选择 1-4，0 返回",
            _print_workflow_action_screen,
        )
        if choice == "0":
            return None
        selected = _power_choice_from_menu(choice)
        if selected:
            return selected


def _get_main_mode_choice_tui() -> str:
    return _read_single_key_menu(
        {"1", "2", "3", "4"},
        "选择模式 1-4",
        print_start_screen,
    )


def get_main_mode_choice() -> str:
    if sys.stdin.isatty() and sys.stdout.isatty():
        _enter_tui_screen()
        try:
            return _get_main_mode_choice_tui()
        finally:
            _exit_tui_screen()

    print_startup_dashboard()
    print()
    _print_box(_main_mode_lines(), title="主菜单 > 模式选择")
    print()
    while True:
        try:
            choice = input(_style("› ", UI_ACCENT) + "选择模式 1-4: ").strip()
            if choice in {"1", "2", "3", "4"}:
                return choice
            ui_error("无效选项，请输入 1-4")
        except KeyboardInterrupt:
            print()
            ui_success("已取消当前输入，仍停留在主菜单。")


def get_workflow_action_choice() -> dict | None:
    if sys.stdin.isatty() and sys.stdout.isatty():
        _enter_tui_screen()
        try:
            return _get_workflow_action_choice_tui()
        finally:
            _exit_tui_screen()

    _print_box(_workflow_action_lines(), title="主菜单 > 今日完整信息流")
    while True:
        try:
            choice = input(_style("› ", UI_ACCENT) + "选择 1-4，0 返回: ").strip()
            if choice == "0":
                return None
            selected = _power_choice_from_menu(choice)
            if selected and selected["mode"] in {1, 2, 3, 4}:
                return selected
            ui_error("无效选项，请输入 1-4，或 0 返回")
        except KeyboardInterrupt:
            print()
            ui_success("已取消，返回主菜单。")
            return None


def print_step_header(step_num, total_steps, step_info):
    """打印步骤标题"""
    ui_panel(
        f"步骤 {step_num}/{total_steps}",
        [
            step_info["name"],
            f"说明: {step_info['description']}",
        ],
    )


def print_diagnosis(diagnosis):
    """打印诊断结果"""
    lines = [f"类型: {diagnosis['diagnosis']}", "", "建议解决方案:"]
    for idx, suggestion in enumerate(diagnosis['suggestions'], 1):
        lines.append(f"  {idx}. {suggestion}")
    ui_panel("错误诊断", lines)


def get_user_power_choice() -> dict:
    """
    获取用户的电源操作选择
    
    Returns:
        dict: {
            'mode': int,  # 1-6
            'action': str,  # 'none', 'sleep', 'hibernate', 'shutdown'
            'delay': int,  # 延迟秒数
            'description': str  # 描述文本
        }
    """
    selected = get_workflow_action_choice()
    if selected is None:
        return {
            'mode': 0,
            'action': 'back',
            'delay': 0,
            'description': '返回主菜单'
        }
    return selected


def get_power_choice_from_args(power_mode: str) -> dict:
    """将非交互式电源参数转换为内部选择结构"""
    choices = {
        "none": {
            'mode': 1,
            'action': 'none',
            'delay': 0,
            'description': '正常运行，结束后无额外操作'
        },
        "sleep": {
            'mode': 2,
            'action': 'sleep',
            'delay': 300,
            'description': '运行结束后5分钟睡眠'
        },
        "hibernate": {
            'mode': 3,
            'action': 'hibernate',
            'delay': 300,
            'description': '运行结束后5分钟休眠'
        },
        "shutdown": {
            'mode': 4,
            'action': 'shutdown',
            'delay': 300,
            'description': '运行结束后5分钟关机'
        },
    }
    return choices[power_mode]


def show_other_operations_menu():
    """显示其他操作菜单"""
    while True:
        ui_menu(
            "主菜单 > 维护与清理 > 其他操作",
            [
                ("1", "清除下载记录", "按类别清空本地历史记录，后续会按新来源规则重新抓取。"),
                ("2", "删除上次下载的内容", "只处理最近一次工作流记录到的 Markdown 及微信图片。"),
                ("3", "仅单独下载特定账号下内容", "不跑完整工作流，单独下载公司纪要、公众号或播客。"),
                ("4", "查看上次运行情况", "打开最近一次工作流摘要。"),
                ("5", "更改程序参数", "调整下载数量、安全开关、听悟处理等通用参数。"),
                ("6", "删除某种类型的本地 Markdown 文档", "按 AlphaPai、微信、小宇宙类别批量删除本地文档。"),
                ("7", "更改 AI API / 模型配置", "切换 AI 接入方式、模型、并发和 max_tokens 等参数。"),
                ("8", "返回上一层", None),
            ],
            subtitle="维护、配置与单独执行入口。",
        )
        
        try:
            choice = ui_prompt("选择 1-8，然后回车: ")
            
            if choice == "1":
                clear_download_history()
            elif choice == "2":
                delete_last_downloaded_content_menu()
            elif choice == "3":
                run_single_source_download_menu()
            elif choice == "4":
                show_last_run_report()
            elif choice == "5":
                edit_config_menu()
            elif choice == "6":
                delete_local_markdowns_by_type_menu()
            elif choice == "7":
                edit_ai_config_menu()
            elif choice == "8":
                return  # 返回上一层
            else:
                ui_error("无效选项，请输入 1-8")
        except KeyboardInterrupt:
            print()
            ui_success("返回上一层")
            return


def show_download_convert_menu():
    while True:
        ui_menu(
            "主菜单 > 单独下载与转换",
            [
                ("1", "单独下载特定账号下内容", "下载单家公司纪要、单个公众号或单个小宇宙播客。"),
                ("2", "有道云文档链接转为 Markdown 文档", "输入一个或多个有道云分享链接，保存到会议纪要 Inbox。"),
                ("3", "AlphaPai 纪要链接转为 Markdown 文档", "输入一个或多个 AlphaPai 纪要详情链接，保存到会议纪要 Inbox，并生成 AI 评价。"),
                ("0", "返回主菜单", None),
            ],
            subtitle="不启动完整工作流，只处理你指定的内容。",
            notes=["统一规则：0 返回，Ctrl+C 取消当前操作。"],
        )
        try:
            choice = ui_prompt("选择 0-3: ")
            if choice == "1":
                run_single_source_download_menu()
            elif choice == "2":
                run_youdao_note_to_md()
            elif choice == "3":
                run_alphapai_links_to_md()
            elif choice == "0":
                return
            else:
                ui_error("无效选项，请输入 0、1、2 或 3")
        except KeyboardInterrupt:
            print()
            ui_success("返回主菜单")
            return


def show_maintenance_menu():
    while True:
        ui_menu(
            "主菜单 > 维护与清理",
            [
                ("1", "清除下载记录", "按类别清空历史记录，后续会按新来源规则重新抓取。"),
                ("2", "删除上次下载的内容", "只处理最近一次工作流记录到的 Markdown 及微信图片。"),
                ("3", "删除某种类型的本地 Markdown 文档", "按 AlphaPai、微信、小宇宙类别批量处理本地文档。"),
                ("4", "标记听悟历史转录为已处理", "用于处理小宇宙历史积压：只标记，不生成 Markdown。"),
                ("5", "检索/创建公司行业笔记骨架", "检查信息收集器和投资笔记中是否已有公司/行业文件夹。"),
                ("0", "返回主菜单", None),
            ],
            subtitle="集中处理本地记录、Markdown 和回收站相关维护动作。",
            notes=["高风险操作会先展示影响范围，再要求二次确认。"],
        )
        try:
            choice = ui_prompt("选择 0-5: ")
            if choice == "1":
                clear_download_history()
            elif choice == "2":
                delete_last_downloaded_content_menu()
            elif choice == "3":
                delete_local_markdowns_by_type_menu()
            elif choice == "4":
                mark_tingwu_completed_processed_menu()
            elif choice == "5":
                obsidian_entity_skeleton_menu()
            elif choice == "0":
                return
            else:
                ui_error("无效选项，请输入 0-5")
        except KeyboardInterrupt:
            print()
            ui_success("返回主菜单")
            return


def show_config_status_menu():
    while True:
        ui_menu(
            "主菜单 > 配置与状态",
            [
                ("1", "查看上次运行情况", "打开最近一次工作流摘要。"),
                ("2", "更改程序参数", "调整下载数量、安全开关、听悟处理等通用参数。"),
                ("3", "更改 AI API / 模型配置", "切换 AI 接入方式、模型、并发和 max_tokens 等参数。"),
                ("4", "管理来源与标签配置", "增删公众号、小宇宙播客、公司/行业标签。"),
                ("5", "查看执行状态", "查看步骤成功、失败和恢复提示。"),
                ("0", "返回主菜单", None),
            ],
            subtitle="查看系统状态，或调整影响后续运行的配置。",
            notes=["统一规则：0 返回，Ctrl+C 取消当前操作。"],
        )
        try:
            choice = ui_prompt("选择 0-5: ")
            if choice == "1":
                show_last_run_report()
            elif choice == "2":
                edit_config_menu()
            elif choice == "3":
                edit_ai_config_menu()
            elif choice == "4":
                manage_source_config_menu()
            elif choice == "5":
                show_status()
            elif choice == "0":
                return
            else:
                ui_error("无效选项，请输入 0-5")
        except KeyboardInterrupt:
            print()
            ui_success("返回主菜单")
            return


def _source_config_error(exc: Exception):
    ui_error(str(exc))


def open_source_config_excel():
    path = Path(SET_CONFIG_FILE)
    if not path.exists():
        ui_error(f"配置 Excel 不存在: {path}")
        return
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])
        ui_success(f"已打开配置 Excel: {path}")
    except Exception as exc:
        ui_error(f"打开配置 Excel 失败: {exc}")


def _choose_wechat_category() -> str | None:
    categories = list((cfg("wechat.category_mapping", {}) or {}).keys())
    if not categories:
        categories = ["投资", "宏观", "商业", "科技", "工作", "其他"]
    items = [(str(idx), item, None) for idx, item in enumerate(categories, 1)]
    items.append(("0", "取消", None))
    ui_menu("主菜单 > 配置与状态 > 来源与标签 > 选择公众号分类", items)
    while True:
        raw = ui_prompt(f"选择 0-{len(categories)}: ")
        if raw == "0":
            return None
        try:
            idx = int(raw)
        except ValueError:
            ui_error("请输入数字")
            continue
        if 1 <= idx <= len(categories):
            return categories[idx - 1]
        ui_error("无效选项")


def _show_wechat_accounts():
    try:
        accounts = load_wechat_accounts()
    except SourceConfigError as exc:
        _source_config_error(exc)
        return
    lines = [f"当前公众号数量: {len(accounts)}", ""]
    lines += [
        f"{idx}. {item['name']} / {item['short_name']} / {item['category']}"
        for idx, item in enumerate(accounts, 1)
    ] or ["暂无公众号配置。"]
    ui_panel("主菜单 > 配置与状态 > 来源与标签 > 公众号列表", lines)


def _add_wechat_account_menu():
    name = ui_prompt("公众号名称: ")
    short_name = ui_prompt("简称（用于文件命名）: ")
    category = _choose_wechat_category()
    if category is None:
        return
    mode = ui_prompt("单篇/聚合（默认 单篇）: ") or "单篇"
    if not confirm_operation("确认添加公众号", [f"公众号: {name}", f"简称: {short_name}", f"分类: {category}", f"模式: {mode}"], danger=True):
        return
    try:
        add_wechat_account(name, short_name, category, mode)
        ui_success("公众号已添加。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def _remove_wechat_account_menu():
    name = ui_prompt("要删除的公众号完整名称: ")
    if not name:
        ui_error("公众号名称不能为空")
        return
    if not confirm_operation("确认删除公众号", [f"将从 Excel 配置中删除公众号: {name}"], danger=True):
        return
    try:
        removed = remove_wechat_account(name)
        if removed:
            ui_success(f"已删除 {removed} 条公众号配置。")
        else:
            ui_warn("没有找到匹配的公众号。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def _show_podcast_accounts():
    try:
        accounts = load_podcast_accounts()
    except SourceConfigError as exc:
        _source_config_error(exc)
        return
    lines = [f"当前小宇宙播客数量: {len(accounts)}", ""]
    lines += [
        f"{idx}. {item['name']} / {item['short_name']} / {item['url']}"
        for idx, item in enumerate(accounts, 1)
    ] or ["暂无小宇宙播客配置。"]
    ui_panel("主菜单 > 配置与状态 > 来源与标签 > 小宇宙播客列表", lines)


def _add_podcast_account_menu():
    name = ui_prompt("播客名称: ")
    url = ui_prompt("小宇宙播客主页 URL: ")
    short_name = ui_prompt("简称（用于音频文件命名）: ")
    if not confirm_operation("确认添加小宇宙播客", [f"播客: {name}", f"简称: {short_name}", f"URL: {url}"], danger=True):
        return
    try:
        add_podcast_account(name, url, short_name)
        ui_success("小宇宙播客账号已添加。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def _remove_podcast_account_menu():
    query = ui_prompt("要删除的播客名称或 URL: ")
    if not query:
        ui_error("播客名称或 URL 不能为空")
        return
    if not confirm_operation("确认删除小宇宙播客", [f"将从 Excel 配置中删除播客: {query}"], danger=True):
        return
    try:
        removed = remove_podcast_account(query)
        if removed:
            ui_success(f"已删除 {removed} 条小宇宙播客配置。")
        else:
            ui_warn("没有找到匹配的小宇宙播客。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def _show_tag_options():
    try:
        options = load_tag_options()
    except SourceConfigError as exc:
        _source_config_error(exc)
        return
    lines = [
        f"公司标签: {len(options.get('公司', []))} 个",
        f"行业标签: {len(options.get('行业', []))} 个",
        "",
        "最近公司标签: " + "、".join(options.get("公司", [])[-12:]),
        "最近行业标签: " + "、".join(options.get("行业", [])[-12:]),
    ]
    ui_panel("主菜单 > 配置与状态 > 来源与标签 > 标签概览", lines)


def _choose_tag_kind() -> str | None:
    ui_menu(
        "主菜单 > 配置与状态 > 来源与标签 > 选择标签类型",
        [("1", "公司", None), ("2", "行业", None), ("0", "取消", None)],
    )
    raw = ui_prompt("选择 0-2: ")
    if raw == "1":
        return "公司"
    if raw == "2":
        return "行业"
    return None


def _add_tag_option_menu():
    kind = _choose_tag_kind()
    if not kind:
        return
    value = ui_prompt(f"新增{kind}标签: ")
    try:
        added = add_tag_option(kind, value)
        if added:
            ui_success(f"{kind}标签已添加。")
        else:
            ui_warn("标签为空或已存在，本次未写入。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def _remove_tag_option_menu():
    kind = _choose_tag_kind()
    if not kind:
        return
    value = ui_prompt(f"要删除的{kind}标签: ")
    if not confirm_operation("确认删除标签", [f"标签类型: {kind}", f"标签: {value}"], danger=True):
        return
    try:
        removed = remove_tag_option(kind, value)
        if removed:
            ui_success(f"已删除 {removed} 个{kind}标签。")
        else:
            ui_warn("没有找到匹配标签。")
    except SourceConfigError as exc:
        _source_config_error(exc)


def manage_source_config_menu():
    while True:
        ui_menu(
            "主菜单 > 配置与状态 > 管理来源与标签配置",
            [
                ("1", "查看公众号", "读取 Excel 的 wechat_account sheet。"),
                ("2", "添加公众号", "填写公众号名称、简称、分类和单篇/聚合。"),
                ("3", "删除公众号", "按公众号完整名称精确删除。"),
                ("4", "查看小宇宙播客", "读取 Excel 的 podcast_account sheet；首次会从 config.yaml 迁移。"),
                ("5", "添加小宇宙播客", "填写播客名称、主页 URL 和简称。"),
                ("6", "删除小宇宙播客", "按播客名称或 URL 精确删除。"),
                ("7", "查看标签概览", "显示公司/行业标签数量和最近条目。"),
                ("8", "添加公司/行业标签", "写入 memo_tag_options sheet，并自动去重。"),
                ("9", "删除公司/行业标签", "按标签文本精确删除。"),
                ("10", "直接打开配置 Excel", "用系统默认程序打开 data/config/set_config.xlsx。"),
                ("0", "返回上一层", None),
            ],
            subtitle="所有来源和标签配置统一维护在 data/config/set_config.xlsx。",
            notes=["写入前会自动生成 .bak 备份。"],
        )
        try:
            choice = ui_prompt("选择 0-10: ")
            if choice == "1":
                _show_wechat_accounts()
            elif choice == "2":
                _add_wechat_account_menu()
            elif choice == "3":
                _remove_wechat_account_menu()
            elif choice == "4":
                _show_podcast_accounts()
            elif choice == "5":
                _add_podcast_account_menu()
            elif choice == "6":
                _remove_podcast_account_menu()
            elif choice == "7":
                _show_tag_options()
            elif choice == "8":
                _add_tag_option_menu()
            elif choice == "9":
                _remove_tag_option_menu()
            elif choice == "10":
                open_source_config_excel()
            elif choice == "0":
                return
            else:
                ui_error("无效选项，请输入 0-10")
        except KeyboardInterrupt:
            print()
            ui_success("返回上一层")
            return


def clear_download_history():
    """清除下载记录"""
    if not cfg("safety.allow_local_delete", True):
        ui_warn("安全开关 safety.allow_local_delete=false，禁止清除本地历史记录。")
        return
    ui_menu(
        "主菜单 > 维护与清理 > 清除下载记录",
        [
            ("1", "AlphaPai 纪要历史记录", f"清除后，再下载会默认下载每个类型下的前 {cfg('memo.new_source_download_count', 10)} 篇。"),
            ("2", "微信公众号文章下载记录", f"清除后，再下载会默认下载每个公众号下的前 {cfg('wechat.new_account_download_count', 3)} 篇。"),
            ("3", "小宇宙播客上传记录", f"清除后，再上传会默认抓取每个播客靠前的前 {cfg('podcast.new_account_download_count', 3)} 期。"),
            ("4", "小宇宙已处理转录记录", "清除后，听悟里已完成但仍存在的转录会被再次整理成 Markdown。"),
            ("5", "返回上一层", None),
        ],
        subtitle="可多选，用逗号分隔。",
        notes=['示例：输入 "1" 只清除 AlphaPai，输入 "3,4" 清除两类小宇宙记录。', "选项 5 必须单选。"],
    )
    
    while True:
        try:
            choice = ui_prompt("请输入选项: ")
            
            # 解析用户输入
            choices = [c.strip() for c in choice.split(',')]
            valid_choices = []
            
            for c in choices:
                if c in ['1', '2', '3', '4', '5']:
                    if c not in valid_choices:
                        valid_choices.append(c)
            
            if not valid_choices:
                ui_error("无效选项，请输入 1-5，或用逗号分隔多个选项")
                continue
            
            # 检查返回选项的特殊规则
            if '5' in valid_choices:
                if len(valid_choices) > 1:
                    ui_error("无效选择：选项 5（返回上一层）必须单选，不能与其他选项组合")
                    continue
                else:
                    return
            
            # 执行清除操作
            confirm_clear_operation(valid_choices)
            return  # 清除完成后返回主菜单
            
        except KeyboardInterrupt:
            print()
            ui_success("返回上一层")
            return


def parse_download_count(raw: str):
    raw = (raw or "").strip()
    if raw.lower() in {"all", "所有", "全部"}:
        return None
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        print("❌ 数量请输入正整数，或输入“所有”/“all”。")
        return "invalid"


def confirm_and_run_command(summary_lines: list[str], cmd: list[str], cwd: str, task_label: str = "任务"):
    if not confirm_operation(
        "确认执行",
        [*summary_lines, "", f"命令: {' '.join(cmd)}"],
        danger=False,
    ):
        return
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode == 0:
        ui_success(f"{task_label}执行完成")
    else:
        ui_error(f"{task_label}执行失败，退出码: {result.returncode}")


def mark_tingwu_completed_processed_menu():
    script_path = os.path.join(PROJECT_ROOT, "investment_system", "collectors", "podcast", "podcast_workflow.py")
    max_pages = cfg("podcast.completed_transcript_max_pages", 0)
    cmd = [
        sys.executable,
        script_path,
        "--phase",
        "mark-completed-processed",
    ]
    if max_pages:
        cmd += ["--max-completed-pages", str(max_pages)]
    confirm_and_run_command(
        [
            "将通义听悟中当前已完成的转录标记为“已处理”。",
            "这个操作不会导出转录、不会调用 AI、不会生成 Markdown。",
            "适合你确认听悟里积压的是历史内容、不希望系统逐个整理时使用。",
            "后续只有新完成且未标记的转录会进入小宇宙 AI 要点整理流程。",
        ],
        cmd,
        PROJECT_ROOT,
        task_label="标记听悟历史转录为已处理",
    )


def confirm_operation(title: str, summary_lines: list[str], *, danger: bool = False) -> bool:
    header = "高风险操作确认" if danger else title
    lines = []
    if danger:
        lines.extend([
            "请先确认影响范围。这个操作会修改本地记录或文件。",
            "如涉及 Markdown / 图片，会优先进入本地回收站；日志和运行产物按清理策略直接处理。",
            "",
        ])
    lines.extend(summary_lines)
    footer = "按 Enter 确认执行，按 Ctrl+C 取消并返回上一层。"
    ui_panel(header, lines, footer=footer)
    try:
        ui_pause_confirm()
        return True
    except KeyboardInterrupt:
        print()
        ui_success("已取消")
        return False


def _yes_no_prompt(label: str, default: bool = False) -> bool:
    suffix = "(Y/n)" if default else "(y/N)"
    while True:
        raw = ui_prompt(f"{label} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "是", "要"}:
            return True
        if raw in {"n", "no", "否", "不", "不要"}:
            return False
        ui_error("请输入 y/yes/是 或 n/no/否，也可以直接回车使用默认值。")


def _ask_entity_kind() -> str | None:
    ui_menu(
        "主菜单 > 维护与清理 > 公司行业笔记骨架 > 确认属性",
        [
            ("1", "公司", "在信息收集器 B公司、投资笔记 {公司-名称} 下创建。"),
            ("2", "行业", "在信息收集器 B行业、投资笔记 {行业-名称} 下创建。"),
            ("0", "取消", None),
        ],
        subtitle="新建只支持公司/行业；策略只做检索展示。",
    )
    while True:
        raw = ui_prompt("请选择属性 0-2: ")
        if raw == "1":
            return ENTITY_COMPANY
        if raw == "2":
            return ENTITY_INDUSTRY
        if raw == "0":
            return None
        ui_error("无效选项，请输入 0、1 或 2")


def _format_scan_result_lines(result) -> list[str]:
    def kind_label(matches) -> str:
        kinds = []
        for kind in (ENTITY_COMPANY, ENTITY_INDUSTRY, ENTITY_STRATEGY):
            if any(item.kind == kind for item in matches):
                kinds.append("策略（仅展示）" if kind == ENTITY_STRATEGY else kind)
        return "、".join(kinds)

    info_label = kind_label(result.info_collector_matches)
    investment_label = kind_label(result.investment_notes_matches)
    info_exists = bool(info_label)
    investment_exists = bool(investment_label)
    name = f"“{result.name}”"

    if info_exists and investment_exists:
        return [
            _style(
                f"在 Obsidian 信息收集器、投资笔记库下均有 {name}；"
                f"属性分别为：信息收集器={info_label}，投资笔记={investment_label}。",
                UI_STATUS_DONE,
            )
        ]

    if not info_exists and not investment_exists:
        return [
            _style(
                f"在 Obsidian 信息收集器、投资笔记库下均没有 {name}。",
                UI_STATUS_PENDING,
            )
        ]

    if info_exists:
        return [
            _style(f"在 Obsidian 信息收集器库下有 {name}，属性为 {info_label}；", UI_STATUS_DONE)
            + _style(f"投资笔记库中没有 {name}。", UI_STATUS_PENDING)
        ]

    return [
        _style(f"在 Obsidian 信息收集器库中没有 {name}；", UI_STATUS_PENDING)
        + _style(f"在投资笔记库下有 {name}，属性为 {investment_label}。", UI_STATUS_DONE)
    ]


def _choose_investment_category_dir(investment_root: Path) -> Path | None:
    category_dirs = list_investment_category_dirs(investment_root)
    if not category_dirs:
        ui_error(f"投资笔记下没有找到 A/B/C 开头的一级文件夹: {investment_root}")
        return None

    while True:
        menu_items = [
            (str(index), path.name, str(path))
            for index, path in enumerate(category_dirs, 1)
        ]
        menu_items.append(("0", "取消", None))
        ui_menu(
            "主菜单 > 维护与清理 > 公司行业笔记骨架 > 选择投资笔记目录",
            menu_items,
            subtitle="列表来自投资笔记根目录下 A/B/C 开头的一级文件夹。",
        )
        raw = ui_prompt(f"请选择 0-{len(category_dirs)}: ")
        if raw == "0":
            return None
        try:
            index = int(raw)
        except ValueError:
            ui_error("请输入列表中的数字。")
            continue
        if 1 <= index <= len(category_dirs):
            return category_dirs[index - 1]
        ui_error(f"无效选项，请输入 0-{len(category_dirs)}")


def _create_result_lines(title: str, result) -> list[str]:
    lines = [title, f"  目标目录: {result.root}"]
    if result.created_dirs:
        lines.append(f"  新建文件夹: {len(result.created_dirs)} 个")
    if result.created_files:
        lines.append(f"  新建 Markdown: {len(result.created_files)} 个")
    if result.skipped_existing:
        lines.append(f"  已存在跳过: {len(result.skipped_existing)} 个")
    if not result.changed:
        lines.append("  没有新增内容，目标骨架已存在。")
    return lines


def _shortcut_result_lines(title: str, result) -> list[str]:
    lines = [title]
    if result.created:
        lines.append(f"  新建快捷方式: {len(result.created)} 个")
        for path in result.created:
            lines.append(f"    - {path.name}")
    if result.skipped_existing:
        lines.append(f"  快捷方式已存在跳过: {len(result.skipped_existing)} 个")
    if result.failed:
        lines.append(f"  快捷方式创建失败: {len(result.failed)} 个")
        for path, message in result.failed:
            lines.append(f"    - {path.name}: {message[:120]}")
    if not result.created and not result.skipped_existing and not result.failed:
        lines.append("  没有快捷方式变更。")
    return lines


def obsidian_entity_skeleton_menu():
    ui_panel(
        "主菜单 > 维护与清理 > 公司行业笔记骨架",
        [
            "输入公司或行业名称后，会严格检索信息收集器与投资笔记。",
            "投资笔记中的策略文件夹只会展示，不会创建策略模板。",
        ],
    )
    raw_name = ui_prompt("请输入名称（留空返回）: ")
    if not raw_name.strip():
        return

    try:
        result = scan_entity(raw_name)
    except ValueError as exc:
        ui_error(str(exc))
        return

    ui_panel(
        "主菜单 > 维护与清理 > 公司行业笔记骨架 > 检索结果",
        _format_scan_result_lines(result),
    )

    kind = _ask_entity_kind()
    if not kind:
        ui_success("已取消")
        return

    actions = []
    if result.info_collector_has_kind(kind):
        ui_success(f"信息收集器中已存在 [{kind}] {result.name}，不会重复创建。")
    elif _yes_no_prompt(f"信息收集器中没有 [{kind}] {result.name}，是否创建？"):
        actions.append(("info", None))

    if result.investment_notes_has_kind(kind):
        ui_success(f"投资笔记中已存在 [{kind}] {result.name}，不会重复创建。")
    elif _yes_no_prompt(f"投资笔记中没有 [{kind}] {result.name}，是否创建？"):
        category_dir = _choose_investment_category_dir(result.investment_notes_root)
        if category_dir:
            actions.append(("investment", category_dir))

    if not actions:
        ui_success("没有选择创建操作。")
        return

    preview_lines = [f"名称: {result.name}", f"属性: {kind}", ""]
    shortcut_dir = shortcut_staging_dir()
    for action, category_dir in actions:
        if action == "info":
            info_target = result.info_collector_root / ("B公司" if kind == ENTITY_COMPANY else "B行业") / result.name
            preview_lines += [
                "信息收集器:",
                f"  目标目录: {info_target}",
                f"  空白定位页: 【{result.name}】.md",
                f"  快捷方式: {shortcut_dir / (result.name + '【信息搜集】.lnk')}",
            ]
        else:
            entity_dir = Path(category_dir) / investment_entity_dir_name(result.name, kind)
            preview_lines += [
                "投资笔记:",
                f"  目标目录: {entity_dir}",
                "  模板页:",
            ]
            for filename in investment_template_files(result.name, kind):
                preview_lines.append(f"    - {filename}")
            preview_lines.append(f"  快捷方式目录: {shortcut_dir}")
        preview_lines.append("")

    if not confirm_operation("确认创建公司行业笔记骨架", preview_lines, danger=False):
        return

    created_lines = []
    for action, category_dir in actions:
        try:
            if action == "info":
                create_result = create_info_collector_entity(result.name, kind, result.info_collector_root)
                created_lines += _create_result_lines("信息收集器:", create_result)
                shortcut_result = create_windows_shortcuts(info_collector_shortcut_specs(result.name, kind))
                created_lines += _shortcut_result_lines("信息收集器快捷方式:", shortcut_result)
            else:
                create_result = create_investment_notes_entity(result.name, kind, category_dir)
                created_lines += _create_result_lines("投资笔记:", create_result)
                shortcut_result = create_windows_shortcuts(investment_notes_shortcut_specs(result.name, kind, category_dir))
                created_lines += _shortcut_result_lines("投资笔记快捷方式:", shortcut_result)
        except Exception as exc:
            created_lines.append(f"创建失败: {exc}")
        created_lines.append("")

    ui_panel("主菜单 > 维护与清理 > 公司行业笔记骨架 > 完成", created_lines or ["没有新增内容。"])


def run_youdao_note_to_md():
    ui_panel(
        "主菜单 > 单独下载与转换 > 有道云转 Markdown",
        [
            "请输入一个或多个有道云文档分享链接。",
            "支持每行一个链接；也可以一次粘贴多行。输入空行结束。",
        ],
    )
    lines = []
    while True:
        raw = ui_prompt("有道云链接（空行结束）: ")
        if not raw:
            break
        lines.append(raw)
    from investment_system.micro_programs.youdao_note_to_md import parse_url_inputs

    urls = parse_url_inputs(lines)
    if not urls:
        ui_error("没有解析到有效链接")
        return

    script_path = os.path.join(PROJECT_ROOT, "investment_system", "micro_programs", "youdao_note_to_md.py")
    output_dir = os.path.join(MEMO_BASE_DIR, "0-Inbox") if "MEMO_BASE_DIR" in globals() else r"D:\softwares\Obsidian\MyNotes\信息收集器\C会议纪要\0-Inbox"
    attachment_dir = ATTACHMENT_DIR if "ATTACHMENT_DIR" in globals() else r"D:\softwares\Obsidian\MyNotes\信息收集器\_overall\_attachment"
    cmd = [
        sys.executable,
        script_path,
        *urls,
        "--out-dir",
        output_dir,
        "--attachment-dir",
        attachment_dir,
    ]
    confirm_and_run_command(
        [
            f"将 {len(urls)} 个有道云文档分享链接转换为 Markdown 文档",
            f"保存位置: {output_dir}",
            f"图片附件位置: {attachment_dir}",
            "这个微程序独立运行，不会修改主工作流的下载记录。",
        ],
        cmd,
        PROJECT_ROOT,
        task_label="有道云转 Markdown 微程序",
    )


def run_alphapai_links_to_md():
    ui_panel(
        "主菜单 > 单独下载与转换 > AlphaPai 链接转 Markdown",
        [
            "请输入一个或多个 AlphaPai 纪要详情链接。",
            "支持每行一个链接；也可以一次粘贴多行。输入空行结束。",
            "保存后会复用批量下载流程生成行业/公司标签和 AI 评价。",
        ],
    )
    lines = []
    while True:
        raw = ui_prompt("AlphaPai 链接（空行结束）: ")
        if not raw:
            break
        lines.append(raw)
    from investment_system.collectors.alpha_memo.link_memos import extract_urls

    urls = extract_urls(lines)
    if not urls:
        ui_error("没有解析到有效链接")
        return

    output_dir = os.path.join(MEMO_BASE_DIR, "0-Inbox")
    script_path = os.path.join(PROJECT_ROOT, "investment_system", "collectors", "alpha_memo", "link_memos.py")
    cmd = [
        sys.executable,
        script_path,
        *urls,
        "--out-dir",
        output_dir,
    ]
    confirm_and_run_command(
        [
            f"将 {len(urls)} 个 AlphaPai 纪要链接转换为 Markdown 文档",
            f"保存位置: {output_dir}",
            "文档格式会沿用 AlphaPai 批量下载流程：基本信息、AI 要点、下载信息。",
            "保存成功后会继续生成行业/公司标签和 AI 评价；如果 AI API 不可用，Markdown 已保存的部分仍会保留。",
        ],
        cmd,
        PROJECT_ROOT,
        task_label="AlphaPai 链接转 Markdown 微程序",
    )


def run_single_source_download_menu():
    ui_menu(
        "主菜单 > 单独下载与转换 > 单独下载",
        [
            ("1", "下载 AlphaPai 中某个公司的历史纪要", "按股票代码下载单家公司历史纪要。"),
            ("2", "下载某个公众号的历史文章", "按 supplierId、公众号 ID 或完整公众号名下载。"),
            ("3", "上传某个小宇宙播客的历史节目到听悟", "只下载音频并上传转录，不生成 Markdown。"),
            ("4", "返回上一层", None),
        ],
        subtitle="只执行一个来源，不启动完整工作流。",
    )
    choice = ui_prompt("选择 1-4，然后回车: ")
    if choice == "1":
        run_single_company_memo_download()
    elif choice == "2":
        run_single_wechat_account_download()
    elif choice == "3":
        run_single_podcast_upload()
    elif choice == "4":
        return
    else:
        ui_error("无效选项")


def run_single_company_memo_download():
    ui_panel("主菜单 > 单独下载与转换 > 单独下载 > 下载公司纪要", ["请输入公司股票代码，格式示例：601888.SH、000001.SZ、01880.HK。"])
    stock = ui_prompt("股票代码: ")
    if not stock:
        ui_error("股票代码不能为空")
        return
    name = ui_prompt("公司名称（可选，示例：中国中免；不知道可直接回车）: ")
    count = parse_download_count(ui_prompt("要下载靠前多少条？输入数字，或输入“所有”/“all”: "))
    if count == "invalid":
        return

    script_path = os.path.join(PROJECT_ROOT, "investment_system", "collectors", "alpha_memo", "company_memos.py")
    cmd = [sys.executable, script_path, "--stock", stock]
    if name:
        cmd += ["--name", name]
    if count is not None:
        cmd += ["--count", str(count)]
    confirm_and_run_command(
        [
            f"下载 AlphaPai 公司纪要: {stock}{' / ' + name if name else ''}",
            f"数量: {'全部能下载的纪要' if count is None else '靠前 ' + str(count) + ' 条'}",
            "保存位置和 AI 处理逻辑沿用 AlphaPai 公司纪要下载模块。",
        ],
        cmd,
        PROJECT_ROOT,
    )


def run_single_wechat_account_download():
    ui_panel(
        "主菜单 > 单独下载与转换 > 单独下载 > 下载单个公众号",
        [
            "请输入公众号标识。最推荐输入 AlphaPai 公众号列表里的 supplierId。",
            "也可以输入 AlphaPai 返回的公众号 id，或完整公众号名称，例如：晚点LatePost。",
        ],
    )
    query = ui_prompt("公众号 supplierId / id / 完整名称: ")
    if not query:
        ui_error("公众号标识不能为空")
        return
    count = parse_download_count(ui_prompt("要下载靠前多少篇？输入数字，或输入“所有”/“all”: "))
    if count == "invalid":
        return
    if count is None:
        ui_warn("当前 AlphaPai 公众号接口为分页增量抓取；单账号模式下“所有”将按常规接口分页尽量抓取，直到无更多文章或遇到已下载记录。")

    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "investment_system", "collectors", "alpha_wechat", "fetch_wechat_articles.py"),
        "--single-account",
        query,
        "--output-inbox",
    ]
    if count is not None:
        cmd += ["--count", str(count)]
    else:
        cmd += ["--all"]
    confirm_and_run_command(
        [
            f"下载单个公众号: {query}",
            f"数量: {'所有能抓到的新文章' if count is None else '靠前 ' + str(count) + ' 篇'}",
            r"保存位置: D:\softwares\Obsidian\MyNotes\信息收集器\C微信文章\0-Inbox",
        ],
        cmd,
        PROJECT_ROOT,
    )


def run_single_podcast_upload():
    ui_panel(
        "主菜单 > 单独下载与转换 > 单独下载 > 上传单个播客",
        [
            "请输入小宇宙播客主页 URL。",
            "推荐格式：https://www.xiaoyuzhoufm.com/podcast/播客ID",
            "不要输入单集 episode 链接；这里需要播客账号主页。",
        ],
    )
    url = ui_prompt("小宇宙播客主页 URL: ")
    if not url.startswith("http"):
        ui_error("请输入完整 URL")
        return
    name = ui_prompt("播客名称（可选，用于记录展示；不知道可回车）: ")
    short_name = ui_prompt("播客简称（可选，用于音频文件名；不知道可回车）: ")
    count = parse_download_count(ui_prompt("要上传靠前多少期？输入数字，或输入“所有”/“all”: "))
    if count == "invalid":
        return

    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "investment_system", "collectors", "podcast", "podcast_workflow.py"),
        "--phase", "upload-new",
        "--single-podcast-url", url,
    ]
    if name:
        cmd += ["--single-podcast-name", name]
    if short_name:
        cmd += ["--single-podcast-short-name", short_name]
    if count is not None:
        cmd += ["--count", str(count)]
    else:
        cmd += ["--all"]
    confirm_and_run_command(
        [
            f"上传小宇宙播客到听悟: {url}",
            f"数量: {'所有能抓到且未上传的节目' if count is None else '靠前 ' + str(count) + ' 期'}",
            "这次只下载音频并上传听悟转录，不生成 Markdown；下一次完整工作流会处理已完成转录并生成笔记。",
        ],
        cmd,
        PROJECT_ROOT,
    )


def show_last_run_report():
    report_dir = Path(REPORT_DIR)
    reports = sorted(report_dir.glob("daily_run_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        ui_panel("主菜单 > 配置与状态 > 上次运行情况", ["暂无运行摘要。"])
        return
    latest = reports[0]
    ui_panel("主菜单 > 配置与状态 > 上次运行情况", [str(latest)])
    print(latest.read_text(encoding="utf-8", errors="replace"))
    _print_rule()


CONFIG_EDIT_OPTIONS = [
    ("memo.new_source_download_count", "AlphaPai 新来源默认下载篇数", int),
    ("memo.max_download_per_source", "AlphaPai 已有历史来源每轮最多下载篇数", int),
    ("wechat.new_account_download_count", "新公众号默认下载篇数", int),
    ("wechat.max_download_per_account", "已有历史公众号每轮最多下载篇数", int),
    ("podcast.new_account_download_count", "新小宇宙播客默认上传期数", int),
    ("podcast.max_download_per_account", "已有历史小宇宙播客每轮最多上传期数", int),
    ("podcast.completed_transcript_max_pages", "听悟已完成转录扫描最大页数（0=扫到最后）", int),
    ("podcast.max_completed_transcripts_per_run", "听悟已完成转录每轮最多整理数量（0=不限制）", int),
    ("podcast.delete_tingwu_record_after_process", "播客笔记生成后删除听悟云端记录", bool),
    ("safety.allow_local_delete", "允许本地删除/清理操作", bool),
    ("safety.allow_cloud_delete", "允许云端删除操作", bool),
]


AI_PROVIDER_CHOICES = [
    ("zhongxin", "中信 AI", "内网 OpenAI Chat Completions 兼容网关"),
    ("minimax", "MiniMax", "MiniMax 官方 OpenAI 兼容接口"),
    ("huoshan", "火山引擎", "火山 Ark 接口，固定使用 kimi-k2.5"),
    ("modelscope", "ModelScope", "ModelScope 多模型轮换与配额管理"),
]

ZHONGXIN_MODEL_CHOICES = [
    "DeepSeek-V4-Pro",
    "glm-5.1",
    "deepseek-v4-flash",
    "kimi-k2.6",
]

AI_CALL_TYPE_CHOICES = [
    ("tag_judgment", "标签判断", "用于行业/公司标签判断，通常偏短、要求稳定"),
    ("short_text", "简短文本", "用于短回答或轻量整理"),
    ("long_thinking", "长篇思考", "用于长文本分析、总结和深度评价"),
]

AI_API_CONFIG_FIELDS = [
    ("base_url", "API 地址", str),
    ("timeout_seconds", "请求超时时间（秒）", int),
    ("max_retries", "最大重试次数", int),
    ("retry_delay_seconds", "重试等待时间（秒）", int),
]


def ai_provider_display(provider: str) -> str:
    mapping = {key: label for key, label, _ in AI_PROVIDER_CHOICES}
    return mapping.get(provider, provider)


def make_ai_model_stats_template(daily_limit: int = 999999) -> dict:
    return {
        "daily_limit": daily_limit,
        "daily_remaining": daily_limit,
        "daily_used": 0,
        "total_calls": 0,
        "calls_by_type": {
            "tag_judgment": 0,
            "short_text": 0,
            "long_thinking": 0,
        },
        "success_by_type": {
            "tag_judgment": 0,
            "short_text": 0,
            "long_thinking": 0,
        },
        "avg_duration_today": None,
        "avg_duration_total": None,
        "total_duration_ms": 0,
        "successful_calls": 0,
        "last_success_at": None,
        "error_counts": {
            "rate_limit": 0,
            "timeout": 0,
            "empty_response": 0,
            "other": 0,
            "validation_failed": 0,
        },
        "consecutive_rate_limit_count": 0,
        "last_call_time": None,
    }


def ensure_zhongxin_models(config: dict) -> bool:
    models = config.setdefault("zhongxin_models", {})
    changed = False
    for model in ZHONGXIN_MODEL_CHOICES:
        if model not in models:
            models[model] = make_ai_model_stats_template()
            changed = True
    if config.get("zhongxin_default_model") not in ZHONGXIN_MODEL_CHOICES:
        config["zhongxin_default_model"] = ZHONGXIN_MODEL_CHOICES[0]
        changed = True
    return changed


def load_ai_models_config() -> dict | None:
    path = Path(AI_MODELS_CONFIG_FILE)
    if not path.exists():
        print(f"❌ AI 模型配置文件不存在: {path}")
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ AI 模型配置读取失败: {e}")
        return None
    if ensure_zhongxin_models(config):
        save_ai_models_config(config)
    return config


def save_ai_models_config(config: dict):
    save_json_atomic(config, Path(AI_MODELS_CONFIG_FILE))


def print_ai_config_summary(config: dict):
    provider = config.get("ai_provider", "modelscope")
    provider_label = ai_provider_display(provider)
    lines = [f"当前接入方式: {provider_label} ({provider})"]
    if provider == "zhongxin":
        lines.append(f"中信模型: {config.get('zhongxin_default_model', ZHONGXIN_MODEL_CHOICES[0])}")
    elif provider == "minimax":
        lines.append(f"MiniMax 模型: {config.get('minimax_default_model', 'MiniMax-M2.7')}")
    elif provider == "huoshan":
        lines.append("火山模型: kimi-k2.5（固定）")
    else:
        order = config.get("modelscope_model_order", {})
        first = (order.get("long_thinking") or order.get("short_text") or order.get("tag_judgment") or ["未配置"])[0]
        lines.append(f"ModelScope 模型轮换: 已配置，首选示例 {first}")
    workers_key = f"{provider}_parallel_workers"
    lines.append(f"并发数: {config.get(workers_key, 5)}")
    api_config = config.get(f"{provider}_api_config", {})
    if api_config:
        lines.append(f"API 地址: {api_config.get('base_url', '未配置')}")
        lines.append(
            "请求参数: "
            f"timeout={api_config.get('timeout_seconds', '未配置')}s, "
            f"max_retries={api_config.get('max_retries', '未配置')}, "
            f"retry_delay={api_config.get('retry_delay_seconds', '未配置')}s"
        )
    lines.append("")
    lines.append("场景参数:")
    for call_type, label, _ in AI_CALL_TYPE_CHOICES:
        params = config.get("default_params", {}).get(call_type, {})
        lines.append(f"  - {label}: temperature={params.get('temperature', '未配置')}, max_tokens={params.get('max_tokens', '未配置')}")
    ui_panel("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 当前配置", lines)


def edit_ai_config_menu():
    """编辑 AI API / 模型配置。"""
    while True:
        config = load_ai_models_config()
        if config is None:
            return
        print_ai_config_summary(config)
        ui_menu(
            "主菜单 > 配置与状态 > 更改 AI API / 模型配置",
            [
                ("1", "切换模型接入方式", "在中信 AI、MiniMax、火山引擎、ModelScope 之间切换。"),
                ("2", "选择中信 AI 模型", "可选 DeepSeek-V4-Pro、glm-5.1、deepseek-v4-flash、kimi-k2.6。"),
                ("3", "调整 AI 并发数", "控制批量处理时并行调用数量。"),
                ("4", "调整 API 地址 / 超时 / 重试参数", "修改 base_url、timeout、max_retries、retry_delay。"),
                ("5", "调整三类调用场景参数", "修改 tag_judgment、short_text、long_thinking 的 temperature 和 max_tokens。"),
                ("6", "返回上一层", None),
            ],
        )
        choice = ui_prompt("选择 1-6，然后回车: ")
        if choice == "1":
            edit_ai_provider(config)
        elif choice == "2":
            edit_zhongxin_model(config)
        elif choice == "3":
            edit_ai_parallel_workers(config)
        elif choice == "4":
            edit_ai_api_runtime_params(config)
        elif choice == "5":
            edit_ai_default_params(config)
        elif choice == "6":
            return
        else:
            ui_error("无效选项，请输入 1-6")


def confirm_and_save_ai_config(config: dict, summary_lines: list[str]) -> bool:
    if not confirm_operation("确认 AI 配置修改", summary_lines, danger=True):
        return False
    try:
        backup_path = Path(f"{AI_MODELS_CONFIG_FILE}.bak")
        source_path = Path(AI_MODELS_CONFIG_FILE)
        backup_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        ui_warn(f"备份 AI 配置失败，继续保存: {e}")
    save_ai_models_config(config)
    ui_success("AI 配置已更新。")
    return True


def edit_ai_provider(config: dict):
    current = config.get("ai_provider", "modelscope")
    items = []
    for idx, (provider, label, desc) in enumerate(AI_PROVIDER_CHOICES, 1):
        marker = "（当前）" if provider == current else ""
        items.append((str(idx), f"{label} ({provider}) {marker}".rstrip(), desc))
    items.append((str(len(AI_PROVIDER_CHOICES) + 1), "返回", None))
    ui_menu("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 选择接入方式", items)
    raw = ui_prompt("请输入选项: ")
    try:
        idx = int(raw)
    except ValueError:
        ui_error("请输入数字")
        return
    if idx == len(AI_PROVIDER_CHOICES) + 1:
        return
    if idx < 1 or idx > len(AI_PROVIDER_CHOICES):
        ui_error("无效选项")
        return
    provider, label, _ = AI_PROVIDER_CHOICES[idx - 1]
    if provider == current:
        ui_success("当前已经是这个接入方式。")
        return
    ensure_zhongxin_models(config)
    old_label = ai_provider_display(current)
    config["ai_provider"] = provider
    if confirm_and_save_ai_config(
        config,
        [
            f"接入方式: {old_label} ({current}) -> {label} ({provider})",
            "后续完整工作流会使用新的 AI provider。",
            "同时会同步 config.yaml 的 ai.default_provider，避免配置来源混乱。",
        ],
    ):
        if update_yaml_scalar_preserving_comments(CONFIG_FILE, "ai.default_provider", provider):
            reload_config()
            ui_success("已同步 config.yaml: ai.default_provider。")
        else:
            ui_warn("AI provider 已生效，但同步 config.yaml 的 ai.default_provider 失败。")


def edit_zhongxin_model(config: dict):
    ensure_zhongxin_models(config)
    current = config.get("zhongxin_default_model", ZHONGXIN_MODEL_CHOICES[0])
    items = []
    for idx, model in enumerate(ZHONGXIN_MODEL_CHOICES, 1):
        marker = "（当前）" if model == current else ""
        items.append((str(idx), f"{model} {marker}".rstrip(), None))
    items.append((str(len(ZHONGXIN_MODEL_CHOICES) + 1), "返回", None))
    ui_menu("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 选择中信 AI 模型", items)
    raw = ui_prompt("请输入选项: ")
    try:
        idx = int(raw)
    except ValueError:
        ui_error("请输入数字")
        return
    if idx == len(ZHONGXIN_MODEL_CHOICES) + 1:
        return
    if idx < 1 or idx > len(ZHONGXIN_MODEL_CHOICES):
        ui_error("无效选项")
        return
    model = ZHONGXIN_MODEL_CHOICES[idx - 1]
    if model == current:
        ui_success("当前已经是这个模型。")
        return
    config["zhongxin_default_model"] = model
    confirm_and_save_ai_config(
        config,
        [
            f"中信 AI 模型: {current} -> {model}",
            "只有当前 provider 为 zhongxin 时，这个模型选择才会直接生效。",
        ],
    )


def edit_ai_parallel_workers(config: dict):
    provider = choose_ai_provider_for_edit(config, "请选择要调整并发数的接入方式")
    if not provider:
        return
    key = f"{provider}_parallel_workers"
    current = config.get(key, 5)
    ui_panel("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 调整 AI 并发数", [f"当前 {ai_provider_display(provider)} 并发数为 {current}。"])
    raw = ui_prompt("请输入新的正整数（直接回车取消）: ")
    if not raw:
        return
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
    except ValueError:
        ui_error("并发数请输入正整数")
        return
    config[key] = value
    confirm_and_save_ai_config(
        config,
        [f"{ai_provider_display(provider)} 并发数: {current} -> {value}"],
    )


def choose_ai_provider_for_edit(config: dict, title: str) -> str | None:
    current = config.get("ai_provider", "modelscope")
    items = []
    for idx, (provider, label, _) in enumerate(AI_PROVIDER_CHOICES, 1):
        marker = "（当前使用）" if provider == current else ""
        items.append((str(idx), f"{label} ({provider}) {marker}".rstrip(), None))
    items.append((str(len(AI_PROVIDER_CHOICES) + 1), "返回", None))
    ui_menu(f"主菜单 > 配置与状态 > 更改 AI API / 模型配置 > {title}", items)
    raw = ui_prompt("请输入选项: ")
    try:
        idx = int(raw)
    except ValueError:
        ui_error("请输入数字")
        return None
    if idx == len(AI_PROVIDER_CHOICES) + 1:
        return None
    if idx < 1 or idx > len(AI_PROVIDER_CHOICES):
        ui_error("无效选项")
        return None
    return AI_PROVIDER_CHOICES[idx - 1][0]


def edit_ai_api_runtime_params(config: dict):
    provider = choose_ai_provider_for_edit(config, "请选择要调整 API 参数的接入方式")
    if not provider:
        return
    key = f"{provider}_api_config"
    api_config = config.setdefault(key, {})
    items = [
        (str(idx), label, f"{field} = {api_config.get(field, '未配置')}")
        for idx, (field, label, _) in enumerate(AI_API_CONFIG_FIELDS, 1)
    ]
    items.append((str(len(AI_API_CONFIG_FIELDS) + 1), "返回", None))
    ui_menu(f"主菜单 > 配置与状态 > 更改 AI API / 模型配置 > {ai_provider_display(provider)} API 参数", items)
    raw = ui_prompt("请选择要修改的参数: ")
    try:
        idx = int(raw)
    except ValueError:
        ui_error("请输入数字")
        return
    if idx == len(AI_API_CONFIG_FIELDS) + 1:
        return
    if idx < 1 or idx > len(AI_API_CONFIG_FIELDS):
        ui_error("无效选项")
        return
    field, label, value_type = AI_API_CONFIG_FIELDS[idx - 1]
    current = api_config.get(field)
    new_raw = ui_prompt(f"当前 {label} 为 {current}，请输入新值（直接回车取消）: ")
    if not new_raw:
        return
    if value_type is int:
        try:
            value = int(new_raw)
            if value < 0:
                raise ValueError
        except ValueError:
            ui_error("请输入非负整数")
            return
    else:
        value = new_raw
    api_config[field] = value
    confirm_and_save_ai_config(
        config,
        [f"{ai_provider_display(provider)} {label}: {current} -> {value}"],
    )


def edit_ai_default_params(config: dict):
    defaults = config.setdefault("default_params", {})
    items = []
    for idx, (call_type, label, desc) in enumerate(AI_CALL_TYPE_CHOICES, 1):
        params = defaults.get(call_type, {})
        items.append((
            str(idx),
            f"{label} ({call_type})",
            f"{desc}；当前 temperature={params.get('temperature', '未配置')}, max_tokens={params.get('max_tokens', '未配置')}",
        ))
    items.append((str(len(AI_CALL_TYPE_CHOICES) + 1), "返回", None))
    ui_menu("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 调整调用场景参数", items)
    raw = ui_prompt("请输入选项: ")
    try:
        idx = int(raw)
    except ValueError:
        ui_error("请输入数字")
        return
    if idx == len(AI_CALL_TYPE_CHOICES) + 1:
        return
    if idx < 1 or idx > len(AI_CALL_TYPE_CHOICES):
        ui_error("无效选项")
        return
    call_type, label, _ = AI_CALL_TYPE_CHOICES[idx - 1]
    params = defaults.setdefault(call_type, {})
    old_temperature = params.get("temperature", 0.4)
    old_max_tokens = params.get("max_tokens", 128)
    ui_panel("主菜单 > 配置与状态 > 更改 AI API / 模型配置 > 编辑场景参数", [f"{label} 当前 temperature={old_temperature}, max_tokens={old_max_tokens}。"])
    temp_raw = ui_prompt("temperature 请输入 0-2 的数字（直接回车保持不变）: ")
    tokens_raw = ui_prompt("max_tokens 请输入正整数（直接回车保持不变）: ")
    if not temp_raw and not tokens_raw:
        return
    new_temperature = old_temperature
    new_max_tokens = old_max_tokens
    if temp_raw:
        try:
            new_temperature = float(temp_raw)
            if new_temperature < 0 or new_temperature > 2:
                raise ValueError
        except ValueError:
            ui_error("temperature 请输入 0 到 2 之间的数字")
            return
    if tokens_raw:
        try:
            new_max_tokens = int(tokens_raw)
            if new_max_tokens <= 0:
                raise ValueError
        except ValueError:
            ui_error("max_tokens 请输入正整数")
            return
    params["temperature"] = new_temperature
    params["max_tokens"] = new_max_tokens
    confirm_and_save_ai_config(
        config,
        [
            f"{label} temperature: {old_temperature} -> {new_temperature}",
            f"{label} max_tokens: {old_max_tokens} -> {new_max_tokens}",
        ],
    )


def set_nested_value(data, key_path, value):
    keys = key_path.split(".")
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).strip()


def update_yaml_scalar_preserving_comments(file_path: str, key_path: str, value) -> bool:
    """Update a simple scalar config value while preserving comments/order."""
    path = Path(file_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    keys = key_path.split(".")
    stack = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        indent = len(line) - len(line.lstrip(" "))
        key = line.lstrip(" ").split(":", 1)[0].strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        current_path = [item[1] for item in stack] + [key]

        if current_path == keys:
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            body = line[:-len(newline)] if newline else line
            before_comment, sep, comment = body.partition("#")
            prefix = before_comment.split(":", 1)[0]
            spacing = " "
            comment_suffix = f" #{comment}" if sep else ""
            lines[idx] = f"{prefix}:{spacing}{yaml_scalar(value)}{comment_suffix}{newline}"
            path.write_text("".join(lines), encoding="utf-8")
            return True

        value_part = line.split(":", 1)[1].strip()
        if not value_part or value_part.startswith("#"):
            stack.append((indent, key))

    return False


def edit_config_menu():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items = [
        (str(idx), label, f"{key_path} = {cfg(key_path)}")
        for idx, (key_path, label, _) in enumerate(CONFIG_EDIT_OPTIONS, 1)
    ]
    items.append((str(len(CONFIG_EDIT_OPTIONS) + 1), "返回上一层", None))
    ui_menu("主菜单 > 配置与状态 > 更改程序参数", items, subtitle="修改通用工作流配置。每次只改一个参数，保存前会再次确认。")
    choice = ui_prompt("请选择要修改的参数: ")
    try:
        idx = int(choice)
    except ValueError:
        ui_error("请输入数字")
        return
    if idx == len(CONFIG_EDIT_OPTIONS) + 1:
        return
    if idx < 1 or idx > len(CONFIG_EDIT_OPTIONS):
        ui_error("无效选项")
        return
    key_path, label, value_type = CONFIG_EDIT_OPTIONS[idx - 1]
    current = cfg(key_path)
    ui_panel("主菜单 > 配置与状态 > 更改程序参数 > 编辑参数", [f"参数: {label}", f"路径: {key_path}", f"当前值: {current}"])
    if value_type is bool:
        raw = ui_prompt("请输入新值 true/false（直接回车取消）: ").lower()
        if not raw:
            return
        if raw not in {"true", "false"}:
            ui_error("布尔值只能输入 true 或 false")
            return
        value = raw == "true"
    else:
        raw = ui_prompt("请输入新的正整数或 0（直接回车取消）: ")
        if not raw:
            return
        try:
            value = int(raw)
            if value < 0:
                raise ValueError
        except ValueError:
            ui_error("请输入非负整数")
            return
    if not confirm_operation("确认参数修改", [f"将把「{label}」从 {current} 改为 {value}。"], danger=True):
        return
    backup_path = f"{CONFIG_FILE}.bak"
    try:
        Path(backup_path).write_text(Path(CONFIG_FILE).read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        ui_warn(f"配置备份失败，继续写入: {e}")
    if not update_yaml_scalar_preserving_comments(CONFIG_FILE, key_path, value):
        ui_warn("未能定位原配置行，回退为 YAML 全量写入。")
        set_nested_value(data, key_path, value)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    reload_config()
    ui_success("参数已更新。")


def confirm_clear_operation(choices: list):
    """确认清除操作"""
    clear_alpha = '1' in choices
    clear_wechat = '2' in choices
    clear_podcast_uploads = '3' in choices
    clear_podcast_processed = '4' in choices
    lines = ["即将执行以下操作："]
    if clear_alpha:
        alpha_file = MEMO_HISTORY_FILE
        lines += [
            "",
            "清除 AlphaPai 纪要历史下载记录",
            f"  文件: {alpha_file}",
            f"  影响: 清除后，再下载会默认下载每个类型下的前 {cfg('memo.new_source_download_count', 10)} 篇",
        ]
    if clear_wechat:
        wechat_file = WECHAT_HISTORY_FILE
        lines += [
            "",
            "清除微信公众号文章下载记录",
            f"  文件: {wechat_file}",
            f"  影响: 清除后，再下载会默认下载每个公众号下的前 {cfg('wechat.new_account_download_count', 3)} 篇",
        ]
    if clear_podcast_uploads:
        podcast_file = PODCAST_HISTORY_FILE
        lines += [
            "",
            "清除小宇宙播客上传记录",
            f"  文件: {podcast_file}",
            f"  影响: 清除后，再上传会默认抓取每个播客靠前的前 {cfg('podcast.new_account_download_count', 3)} 期",
        ]
    if clear_podcast_processed:
        podcast_file = PODCAST_HISTORY_FILE
        lines += [
            "",
            "清除小宇宙已处理转录记录",
            f"  文件: {podcast_file}",
            "  影响: 听悟里已完成但仍存在的转录会被再次整理成 Markdown；这是高风险重复处理操作",
        ]
    if not confirm_operation("确认清除操作", lines, danger=True):
        return
    
    # 执行清除操作
    execute_clear_operation(clear_alpha, clear_wechat, clear_podcast_uploads, clear_podcast_processed)


def execute_clear_operation(
    clear_alpha: bool,
    clear_wechat: bool,
    clear_podcast_uploads: bool = False,
    clear_podcast_processed: bool = False,
):
    """执行清除操作 - 保留JSON结构，只清空列表"""
    ui_panel("主菜单 > 维护与清理 > 清除下载记录 > 正在执行", ["保留 JSON 文件结构，只清空对应记录列表。"])
    
    success_count = 0
    
    if clear_alpha:
        alpha_file = MEMO_HISTORY_FILE
        print(f"  📁 目标文件: {alpha_file}")
        
        try:
            if not os.path.exists(alpha_file):
                print(f"  ❌ 文件不存在！")
            else:
                with open(alpha_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                print(f"  📊 读取到 {len(data)} 个分类")
                
                cleared_types = []
                total_cleared = 0
                for key in data:
                    if isinstance(data[key], list):
                        count = len(data[key])
                        if count > 0:
                            cleared_types.append(f"{key}({count}条)")
                            total_cleared += count
                        data[key] = []
                
                with open(alpha_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"  ✅ 已清除 AlphaPai纪要的历史下载记录")
                print(f"     共清除 {total_cleared} 条记录")
                if cleared_types:
                    print(f"     清除类型: {', '.join(cleared_types)}")
                success_count += 1
                
        except Exception as e:
            print(f"  ❌ 清除 AlphaPai纪要记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    if clear_wechat:
        wechat_file = WECHAT_HISTORY_FILE
        print(f"  📁 目标文件: {wechat_file}")
        
        try:
            if not os.path.exists(wechat_file):
                print(f"  ❌ 文件不存在！")
            else:
                with open(wechat_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                print(f"  📊 读取到 {len(data)} 个公众号")
                
                cleared_accounts = []
                total_cleared = 0
                for key in data:
                    if isinstance(data[key], list):
                        count = len(data[key])
                        if count > 0:
                            cleared_accounts.append(f"{key}({count}条)")
                            total_cleared += count
                        data[key] = []
                
                with open(wechat_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"  ✅ 已清除 微信公众号文章下载记录")
                print(f"     共清除 {total_cleared} 条记录")
                if cleared_accounts:
                    print(f"     清除公众号: {', '.join(cleared_accounts)}")
                success_count += 1
                
        except Exception as e:
            print(f"  ❌ 清除 微信公众号记录失败: {e}")
            import traceback
            traceback.print_exc()

    if clear_podcast_uploads or clear_podcast_processed:
        podcast_file = PODCAST_HISTORY_FILE
        print(f"  📁 目标文件: {podcast_file}")

        try:
            if os.path.exists(podcast_file):
                with open(podcast_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}

            uploaded_count = sum(
                len(items) for items in data.get("uploaded_episodes", {}).values()
                if isinstance(items, list)
            ) if isinstance(data.get("uploaded_episodes"), dict) else 0
            uploads_count = len(data.get("uploads", {})) if isinstance(data.get("uploads"), dict) else 0
            processed_count = len(data.get("processed_transcripts", [])) if isinstance(data.get("processed_transcripts"), list) else 0

            if clear_podcast_uploads:
                data["uploaded_episodes"] = {}
                data["uploads"] = {}
            if clear_podcast_processed:
                data["processed_transcripts"] = []
                data["processed_notes"] = {}

            os.makedirs(os.path.dirname(podcast_file), exist_ok=True)
            temp_file = f"{podcast_file}.tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, podcast_file)

            print(f"  ✅ 已清除 小宇宙播客记录")
            if clear_podcast_uploads:
                print(f"     已清除上传记录: 已上传节目 {uploaded_count} 条，上传映射 {uploads_count} 条")
            if clear_podcast_processed:
                print(f"     已清除转录记录: 已处理转录 {processed_count} 条")
            success_count += 1

        except Exception as e:
            print(f"  ❌ 清除 小宇宙播客记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    if success_count > 0:
        ui_success(f"清除完成，成功清除 {success_count} 个记录文件")
    else:
        ui_error("清除失败，请检查文件路径")
    _print_rule()


CATEGORY_LABELS = {
    CATEGORY_ALPHA_MEMO: "AlphaPai纪要",
    CATEGORY_WECHAT: "微信公众号文章",
    CATEGORY_PODCAST: "小宇宙播客",
}


def _parse_multi_choice(raw: str, valid_values: set[str]) -> list[str]:
    choices = []
    for item in (raw or "").split(","):
        item = item.strip()
        if item and item in valid_values and item not in choices:
            choices.append(item)
    return choices


def _safe_markdown_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.suffix.lower() != ".md":
        return None
    return path


def _normalize_image_name(image_name: str) -> str:
    image_name = (image_name or "").strip().strip("<>").strip()
    if "|" in image_name:
        image_name = image_name.split("|", 1)[0].strip()
    image_name = image_name.replace("/", os.sep).replace("\\", os.sep)
    return image_name


def _safe_attachment_path(image_name: str) -> Path | None:
    image_name = _normalize_image_name(image_name)
    if not image_name:
        return None
    candidate = Path(image_name)
    if candidate.is_absolute():
        return None
    base = Path(ATTACHMENT_DIR).resolve()
    image_path = (base / candidate).resolve()
    try:
        image_path.relative_to(base)
    except ValueError:
        return None
    return image_path


def _items_for_category(manifest: dict, category: str) -> list[dict]:
    items = manifest.get("categories", {}).get(category, [])
    return [item for item in items if isinstance(item, dict)]


def _markdown_root_for_category(category: str) -> Path:
    roots = {
        CATEGORY_ALPHA_MEMO: MEMO_BASE_DIR,
        CATEGORY_WECHAT: WECHAT_ARTICLE_BASE_DIR,
        CATEGORY_PODCAST: PODCAST_BASE_DIR,
    }
    return Path(roots[category])


def _read_folder_names_for_category(category: str) -> set[str]:
    names = {"已读"}
    if category == CATEGORY_PODCAST:
        names.add(str(cfg("podcast.read_folder_name", "已读")))
    else:
        names.add(str(cfg("memo.read_folder_name", "已读")))
    return {name for name in names if name}


def _is_in_read_folder(path: Path, root: Path, category: str) -> bool:
    safe_path = _safe_markdown_path_under_root(path, root)
    if not safe_path:
        return False
    try:
        relative_parts = safe_path.relative_to(root.resolve()).parts
    except ValueError:
        return False
    read_folder_names = _read_folder_names_for_category(category)
    return any(part in read_folder_names for part in relative_parts[:-1])


def _safe_markdown_path_under_root(path: Path, root: Path) -> Path | None:
    if path.suffix.lower() != ".md":
        return None
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_path


def _collect_markdown_files_under_root(
    root: Path,
    *,
    include_read: bool = True,
    category: str | None = None,
) -> list[Path]:
    if not root.exists():
        return []
    files = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        safe_path = _safe_markdown_path_under_root(path, root)
        if not safe_path:
            continue
        if not include_read and category and _is_in_read_folder(safe_path, root, category):
            continue
        if safe_path:
            files.append(safe_path)
    return sorted(files, key=lambda p: str(p).lower())


def ask_include_read_folder() -> bool:
    ui_panel(
        "主菜单 > 维护与清理 > 删除本地 Markdown 文档 > 已读文件夹",
        [
            "是否也删除“已读”文件夹里的 Markdown 文档？",
            "输入 y / yes / 是：同时删除已读。",
            "直接回车或输入 n / no / 否：只删除未归档在已读目录外的文档。",
        ],
    )
    while True:
        raw = ui_prompt("是否删除已读文件夹内容？(y/N): ").lower()
        if raw in {"", "n", "no", "否", "不", "不要"}:
            return False
        if raw in {"y", "yes", "是", "要"}:
            return True
        ui_error("请输入 y/yes/是 或 n/no/否，也可以直接回车表示不删除已读。")


def delete_local_markdowns_by_type_menu():
    """按内容类型删除本地 Markdown 文档。"""
    if not cfg("safety.allow_local_delete", True):
        ui_warn("安全开关 safety.allow_local_delete=false，禁止删除本地文件。")
        return

    ui_menu(
        "主菜单 > 维护与清理 > 删除本地 Markdown 文档",
        [
            ("1", "AlphaPai 纪要", "删除本地 AlphaPai 纪要 Markdown。"),
            ("2", "微信文章", "删除公众号下载与 Notion 收藏下载的 Markdown，并同步删除文内引用图片。"),
            ("3", "小宇宙播客", "删除本地小宇宙播客 Markdown。"),
            ("4", "返回上一层", None),
        ],
        subtitle="可多选，用逗号分隔。",
        notes=['示例：输入 "2" 只删除微信文章，输入 "1,2,3" 删除三类。', "选项 4 必须单选。"],
    )

    while True:
        try:
            raw = ui_prompt("请输入选项: ")
            choices = _parse_multi_choice(raw, {"1", "2", "3", "4"})
            if not choices:
                ui_error("无效选项，请输入 1、2、3 或 4，或用逗号分隔多个选项")
                continue
            if "4" in choices:
                if len(choices) > 1:
                    ui_error("无效选择：选项 4（返回上一层）必须单选")
                    continue
                return
            include_read = ask_include_read_folder()
            confirm_delete_local_markdowns_by_type(choices, include_read=include_read)
            return
        except KeyboardInterrupt:
            print()
            ui_success("返回上一层")
            return


def _categories_from_delete_type_choices(choices: list[str]) -> list[str]:
    selected_categories = []
    if "1" in choices:
        selected_categories.append(CATEGORY_ALPHA_MEMO)
    if "2" in choices:
        selected_categories.append(CATEGORY_WECHAT)
    if "3" in choices:
        selected_categories.append(CATEGORY_PODCAST)
    return selected_categories


def confirm_delete_local_markdowns_by_type(choices: list[str], include_read: bool = False):
    selected_categories = _categories_from_delete_type_choices(choices)
    delete_plan = {
        category: _collect_markdown_files_under_root(
            _markdown_root_for_category(category),
            include_read=include_read,
            category=category,
        )
        for category in selected_categories
    }

    lines = ["即将执行以下操作：", f"已读文件夹: {'会一起删除' if include_read else '不会删除'}"]

    for category in selected_categories:
        root = _markdown_root_for_category(category)
        files = delete_plan[category]
        lines += [
            "",
            CATEGORY_LABELS[category],
            f"  扫描目录: {root}",
            f"  已读目录: {'包含' if include_read else '排除'}",
            f"  将删除 Markdown: {len(files)} 篇",
        ]
        if category == CATEGORY_WECHAT:
            lines.append("  将先读取这些 Markdown 中引用到的本地图片，再同步删除附件目录中的对应图片。")
            lines.append("  不做全库引用检查；若图片文件不存在则跳过。")
        if not root.exists():
            lines.append("  目录不存在，将跳过。")
        for path in files[:30]:
            lines.append(f"  - {path}")
        if len(files) > 30:
            lines.append(f"  ... 还有 {len(files) - 30} 篇未展开显示")

    total_to_delete = sum(len(files) for files in delete_plan.values())
    if total_to_delete == 0:
        ui_panel("主菜单 > 维护与清理 > 删除本地 Markdown 文档 > 确认", [*lines, "", "没有找到可删除的 Markdown 文档，本次不会删除任何文件。"])
        return

    if not confirm_operation("确认删除本地 Markdown 文档", lines, danger=True):
        return

    execute_delete_local_markdowns_by_type(delete_plan)


def execute_delete_local_markdowns_by_type(delete_plan: dict[str, list[Path]]) -> dict[str, int]:
    ui_panel(
        "主菜单 > 维护与清理 > 删除本地 Markdown 文档 > 正在执行",
        [
            "按选择的类别处理 Markdown；微信文章会同步处理引用图片。",
            f"Markdown 和图片会先移入本地回收站: {recycle_bin_root()}",
        ],
    )

    recycled_md = 0
    recycled_images = 0
    skipped_missing = 0
    failed = 0
    seen_image_paths = set()

    for category, paths in delete_plan.items():
        print(f"\n  📁 {CATEGORY_LABELS[category]}")
        root = _markdown_root_for_category(category)
        for raw_path in paths:
            path = _safe_markdown_path_under_root(Path(raw_path), root)
            if not path or not path.is_file():
                skipped_missing += 1
                print(f"     - 跳过，文件已不存在或不在目标目录内: {raw_path}")
                continue

            image_names = extract_images_from_md(str(path)) if category == CATEGORY_WECHAT else []
            try:
                moved = move_to_recycle_bin(path, category=category, item_type="markdown")
                recycled_md += 1
                print(f"     ✓ 已移入回收站: {moved.source.name}")
            except Exception as exc:
                failed += 1
                print(f"     ❌ 移入回收站失败: {path}: {exc}")
                continue

            if category == CATEGORY_WECHAT:
                for image_name in image_names:
                    image_path = _safe_attachment_path(image_name)
                    if not image_path or not image_path.is_file():
                        continue
                    image_key = str(image_path.resolve()).lower()
                    if image_key in seen_image_paths:
                        continue
                    seen_image_paths.add(image_key)
                    try:
                        move_to_recycle_bin(image_path, category=category, item_type="image")
                        recycled_images += 1
                        print(f"       ✓ 图片已移入回收站: {image_path.name}")
                    except Exception as exc:
                        print(f"       ⚠️ 图片移入回收站失败: {image_path}: {exc}")

    lines = [
        f"已移入回收站 Markdown: {recycled_md} 篇",
        f"已移入回收站微信图片: {recycled_images} 张",
        f"文件已不存在/已移动而跳过: {skipped_missing} 篇",
        f"回收站路径: {recycle_bin_root()}",
    ]
    if failed:
        lines.append(f"移入回收站失败: {failed} 篇")
    ui_panel("主菜单 > 维护与清理 > 删除本地 Markdown 文档 > 完成", lines)

    return {
        "deleted_md": recycled_md,
        "deleted_images": recycled_images,
        "recycled_md": recycled_md,
        "recycled_images": recycled_images,
        "skipped_missing": skipped_missing,
        "failed": failed,
    }


def delete_last_downloaded_content_menu():
    """删除上一次工作流生成的本地 Markdown 内容。"""
    if not cfg("safety.allow_local_delete", True):
        ui_warn("安全开关 safety.allow_local_delete=false，禁止删除本地文件。")
        return

    manifest = load_last_download_manifest()
    categories = manifest.get("categories", {})
    total_items = sum(len(items) for items in categories.values() if isinstance(items, list))

    if not total_items:
        ui_panel("主菜单 > 维护与清理 > 删除上次下载的内容", ["当前没有记录到上次下载生成的 Markdown 文档。"])
        return

    ui_menu(
        "主菜单 > 维护与清理 > 删除上次下载的内容",
        [
            ("1", f"AlphaPai 纪要 Markdown（{len(_items_for_category(manifest, CATEGORY_ALPHA_MEMO))} 篇）", None),
            ("2", f"微信公众号文章 Markdown 及图片（{len(_items_for_category(manifest, CATEGORY_WECHAT))} 篇）", None),
            ("3", f"小宇宙播客 Markdown（{len(_items_for_category(manifest, CATEGORY_PODCAST))} 篇）", None),
            ("4", "返回上一层", None),
        ],
        subtitle=f"上次记录时间: {manifest.get('updated_at', '未知')}。可多选，用逗号分隔。",
        notes=['示例：输入 "1" 只删除 AlphaPai，输入 "1,2,3" 删除三类。', "选项 4 必须单选。"],
    )

    while True:
        try:
            raw = ui_prompt("请输入选项: ")
            choices = _parse_multi_choice(raw, {"1", "2", "3", "4"})
            if not choices:
                ui_error("无效选项，请输入 1、2、3 或 4，或用逗号分隔多个选项")
                continue
            if "4" in choices:
                if len(choices) > 1:
                    ui_error("无效选择：选项 4（返回上一层）必须单选")
                    continue
                return
            confirm_delete_last_downloaded_content(choices, manifest)
            return
        except KeyboardInterrupt:
            print()
            ui_success("返回上一层")
            return


def confirm_delete_last_downloaded_content(choices: list[str], manifest: dict):
    selected_categories = []
    if "1" in choices:
        selected_categories.append(CATEGORY_ALPHA_MEMO)
    if "2" in choices:
        selected_categories.append(CATEGORY_WECHAT)
    if "3" in choices:
        selected_categories.append(CATEGORY_PODCAST)

    lines = ["即将执行以下操作："]

    delete_plan = {}
    missing_plan = {}
    for category in selected_categories:
        existing = []
        missing = []
        for item in _items_for_category(manifest, category):
            path = _safe_markdown_path(item.get("path", ""))
            if path and path.is_file():
                existing.append((item, path))
            else:
                missing.append(item)
        delete_plan[category] = existing
        missing_plan[category] = missing

        lines += ["", CATEGORY_LABELS[category], f"  将删除 Markdown: {len(existing)} 篇"]
        if category == CATEGORY_WECHAT:
            lines.append("  将同时删除这些 Markdown 中引用到的本地附件图片。")
            lines.append("  不做全库引用检查；若图片文件不存在则跳过。")
        if missing:
            lines.append(f"  已不存在/疑似被移动，将跳过: {len(missing)} 篇")
        for _, path in existing[:30]:
            lines.append(f"  - {path}")
        if len(existing) > 30:
            lines.append(f"  ... 还有 {len(existing) - 30} 篇未展开显示")

    total_to_delete = sum(len(items) for items in delete_plan.values())
    if total_to_delete == 0:
        ui_panel("主菜单 > 维护与清理 > 删除上次下载的内容 > 确认", [*lines, "", "没有找到仍在原路径的 Markdown 文档，本次不会删除任何文件。"])
        return

    if not confirm_operation("确认删除上次下载的内容", lines, danger=True):
        return

    execute_delete_last_downloaded_content(selected_categories, manifest)


def execute_delete_last_downloaded_content(selected_categories: list[str], manifest: dict):
    ui_panel(
        "主菜单 > 维护与清理 > 删除上次下载的内容 > 正在执行",
        [
            "只处理上次 manifest 记录到的文件路径。",
            f"Markdown 和图片会先移入本地回收站: {recycle_bin_root()}",
        ],
    )

    recycled_md = 0
    recycled_images = 0
    skipped_missing = 0
    failed = 0
    categories = manifest.setdefault("categories", {})

    for category in selected_categories:
        print(f"\n  📁 {CATEGORY_LABELS[category]}")
        remaining_items = []
        for item in _items_for_category(manifest, category):
            path = _safe_markdown_path(item.get("path", ""))
            if not path or not path.is_file():
                skipped_missing += 1
                print(f"     - 跳过，文件已不存在: {item.get('path', '')}")
                continue

            image_names = extract_images_from_md(str(path)) if category == CATEGORY_WECHAT else []
            try:
                moved = move_to_recycle_bin(path, category=category, item_type="markdown")
                recycled_md += 1
                print(f"     ✓ 已移入回收站: {moved.source.name}")
            except Exception as exc:
                failed += 1
                remaining_items.append(item)
                print(f"     ❌ 移入回收站失败: {path}: {exc}")
                continue

            if category == CATEGORY_WECHAT:
                for image_name in image_names:
                    image_path = _safe_attachment_path(image_name)
                    if not image_path or not image_path.is_file():
                        continue
                    try:
                        move_to_recycle_bin(image_path, category=category, item_type="image")
                        recycled_images += 1
                        print(f"       ✓ 图片已移入回收站: {image_path.name}")
                    except Exception as exc:
                        print(f"       ⚠️ 图片移入回收站失败: {image_path}: {exc}")

        categories[category] = remaining_items

    save_last_download_manifest(manifest)

    lines = [
        f"已移入回收站 Markdown: {recycled_md} 篇",
        f"已移入回收站微信图片: {recycled_images} 张",
        f"文件已不存在/已移动而跳过: {skipped_missing} 篇",
        f"回收站路径: {recycle_bin_root()}",
    ]
    if failed:
        lines.append(f"移入回收站失败并保留在记录中: {failed} 篇")
    ui_panel("主菜单 > 维护与清理 > 删除上次下载的内容 > 完成", lines)


def schedule_power_action(action: str, delay_seconds: int):
    """
    定时执行电源操作
    
    Args:
        action: 'sleep', 'hibernate', 'shutdown'
        delay_seconds: 延迟秒数
    """
    import subprocess
    
    action_names = {
        'sleep': '睡眠',
        'hibernate': '休眠',
        'shutdown': '关机'
    }
    
    action_name = action_names.get(action, '未知操作')
    
    if delay_seconds > 0:
        ui_panel("电源动作倒计时", [f"电脑将在 {delay_seconds//60} 分钟后{action_name}", "按 Ctrl+C 可取消。"])
        
        try:
            # 倒计时显示
            remaining = delay_seconds
            while remaining > 0:
                mins, secs = divmod(remaining, 60)
                print(f"\r   倒计时: {mins:02d}:{secs:02d} ", end="", flush=True)
                time.sleep(1)
                remaining -= 1
            
            print()  # 换行
        except KeyboardInterrupt:
            print()
            ui_success(f"已取消定时{action_name}")
            return
    
    # 执行操作
    ui_panel("电源动作", [f"正在{action_name}..."])
    
    try:
        if action == "sleep":
            # Windows睡眠命令（使用PowerShell，更可靠）
            subprocess.run([
                "powershell", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; " +
                "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"
            ], check=True)
        elif action == "hibernate":
            # Windows休眠命令
            subprocess.run(["shutdown", "/h"], check=True)
        elif action == "shutdown":
            # Windows关机命令
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
    except Exception as e:
        ui_error(f"{action_name}失败: {e}")


def confirm_and_start(choice: dict, step_indices: list, steps_to_run: list):
    """
    显示确认信息并等待用户确认
    
    Args:
        choice: 用户选择信息
        step_indices: 步骤索引列表
        steps_to_run: 要运行的步骤列表
    """
    lines = [f"已选择: {choice['description']}", "", "将执行以下步骤:"]
    for i, step_info in zip(step_indices, steps_to_run):
        lines.append(f"  {i}. {step_info['name']}")
    if choice['action'] != 'none':
        lines += ["", f"执行完成后将: {choice['description']}", "期间可按 Ctrl+C 取消倒计时。"]
    if choice['mode'] == 4:
        lines += [
            "",
            "关机提示:",
            "  请确认已保存工作文件、关闭正在运行的程序，并备份重要数据。",
            "  工作流完成后会先进入 5 分钟倒计时。",
        ]
    return confirm_operation("确认运行计划", lines, danger=choice['mode'] == 4)


def choose_run_plan(args):
    """选择并确认本次运行计划；交互取消时返回主菜单。"""
    while True:
        if args.yes or args.dry_run:
            print_startup_dashboard()
            power_choice = get_power_choice_from_args(args.power)
            mode_label = "dry-run 预演模式" if args.dry_run and not args.yes else "非交互式模式"
            print(f"\n🤖 {mode_label}: {power_choice['description']}")
        else:
            while True:
                mode_choice = get_main_mode_choice()
                if mode_choice == "1":
                    power_choice = get_workflow_action_choice()
                    if power_choice is None:
                        continue
                    break
                if mode_choice == "2":
                    show_download_convert_menu()
                    continue
                if mode_choice == "3":
                    show_maintenance_menu()
                    continue
                if mode_choice == "4":
                    show_config_status_menu()
                    continue
                ui_error("无效模式，请输入 1-4")
                continue

        if args.resume:
            last_failed = ProgressManager.get_last_failed_step()
            if last_failed:
                print(f"\n🔄 恢复模式: 从步骤 {last_failed} 继续执行")
                start_step = last_failed
            else:
                print(f"\n🔄 恢复模式: 没有失败的步骤，将执行完整工作流")
                start_step = 1
            step_indices = list(range(start_step, len(WORKFLOW_STEPS) + 1))
            steps_to_run = [WORKFLOW_STEPS[i-1] for i in step_indices]
        elif args.step:
            print(f"\n🎯 单步模式: 只执行步骤 {args.step}")
            steps_to_run = [WORKFLOW_STEPS[args.step - 1]]
            step_indices = [args.step]
        else:
            print(f"\n🔄 完整模式: 执行所有 {len(WORKFLOW_STEPS)} 个步骤")
            steps_to_run = WORKFLOW_STEPS
            step_indices = list(range(1, len(WORKFLOW_STEPS) + 1))

        if args.yes or args.dry_run:
            print_execution_plan(power_choice, step_indices, steps_to_run)
            return power_choice, step_indices, steps_to_run

        if confirm_and_start(power_choice, step_indices, steps_to_run):
            return power_choice, step_indices, steps_to_run

        ui_success("已返回主菜单。")


def print_execution_plan(choice: dict, step_indices: list, steps_to_run: list):
    """非交互模式下展示即将执行的计划"""
    lines = [f"运行模式: {choice['description']}", "", "将执行以下步骤:"]
    for i, step_info in zip(step_indices, steps_to_run):
        lines.append(f"  {i}. {step_info['name']}")
    if choice['action'] != 'none':
        lines += ["", f"执行完成后将: {choice['description']}"]
    ui_panel("运行计划", lines)


def print_safety_notice(dry_run: bool):
    allow_local_delete = cfg("safety.allow_local_delete", True)
    allow_cloud_delete = cfg("safety.allow_cloud_delete", True)
    delete_tingwu = cfg("podcast.delete_tingwu_record_after_process", True)
    ui_panel(
        "安全开关",
        [
            f"dry-run: {'开启' if dry_run else '关闭'}",
            f"本地删除: {'允许' if allow_local_delete else '禁止'}",
            f"云端删除: {'允许' if allow_cloud_delete else '禁止'}",
            f"听悟处理后删除云端记录: {'开启' if delete_tingwu else '关闭'}",
        ],
    )


def run_script(script_path, args, step_name, step_idx, timeout_seconds: int = 7200):
    """
    执行单个脚本 (实时输出模式)
    
    Args:
        script_path: 脚本路径
        args: 脚本参数列表
        step_name: 步骤名称
        step_idx: 步骤索引
    
    Returns:
        TaskResult: normalized task execution result
    """
    start_time = time.time()
    started_at = datetime.now().isoformat(timespec="seconds")
    ProgressManager.save_step_started(step_idx, step_name)
    
    if not os.path.exists(script_path):
        error_msg = f"脚本不存在: {script_path}"
        print(f"❌ {error_msg}")
        ProgressManager.save_progress(step_idx, step_name, False, error_msg, 0.0)
        return TaskResult.from_execution(
            step=step_idx,
            name=step_name,
            success=False,
            elapsed=0.0,
            error=error_msg,
            started_at=started_at,
        )
    
    cmd = [sys.executable, script_path] + args
    
    print(f"\n🚀 开始执行: {step_name}")
    print(f"   命令: {' '.join(cmd)}")
    print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   超时上限: {timeout_seconds} 秒")
    print("   提示: 正在执行中,请耐心等待...")
    print("-" * 70)
    sys.stdout.flush()  # 强制刷新输出缓冲区
    
    try:
        # 设置环境变量，禁用Python输出缓冲 + UTF-8编码
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # 禁用子进程的输出缓冲
        env['PYTHONIOENCODING'] = 'utf-8'  # 子进程使用UTF-8编码
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并stderr到stdout，统一处理
            text=True,
            encoding='utf-8',
            errors='replace',  # 替换无法解码的字符
            bufsize=1,  # 行缓冲
            env=env,  # 传递环境变量
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0  # Windows: 不创建新窗口
        )
        
        stdout_lines = []
        
        import threading
        
        def read_stream(stream, lines_list):
            """实时读取输出流（健壮版本）"""
            try:
                while True:
                    try:
                        line = stream.readline()
                        if not line:  # EOF
                            break
                        
                        line = line.rstrip()
                        if not line:  # 跳过空行
                            continue
                        
                        # 安全打印（处理所有编码错误）
                        try:
                            print(f"   {line}", flush=True)
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            safe_line = line.encode('utf-8', errors='replace').decode('utf-8')
                            print(f"   {safe_line}", flush=True)
                        
                        lines_list.append(line)
                    
                    except UnicodeError:
                        continue
                    
                    except Exception as e:
                        break
                
                stream.close()
                
            except Exception as e:
                pass
        
        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines))
        stdout_thread.start()
        
        try:
            process.wait(timeout=timeout_seconds)
            
            stdout_thread.join(timeout=5)  # 等待输出线程结束
            
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            elapsed = time.time() - start_time
            error_msg = f"脚本执行超时（超过{timeout_seconds}秒）"
            print("-" * 70)
            print(f"❌ {error_msg}")
            ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
            return TaskResult.from_execution(
                step=step_idx,
                name=step_name,
                success=False,
                elapsed=elapsed,
                error=error_msg,
                outputs=stdout_lines,
                returncode=None,
                started_at=started_at,
            )
        
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except:
                    process.kill()
        
        elapsed = time.time() - start_time
        output = '\n'.join(stdout_lines) if stdout_lines else ''
        
        if process.returncode == 0:
            print("-" * 70)
            print(f"✅ {step_name} 执行成功")
            print(f"   用时: {elapsed:.1f} 秒")
            ProgressManager.save_progress(step_idx, step_name, True, None, elapsed)
            return TaskResult.from_execution(
                step=step_idx,
                name=step_name,
                success=True,
                elapsed=elapsed,
                error=None,
                outputs=stdout_lines,
                returncode=process.returncode,
                started_at=started_at,
            )
        else:
            error_msg = output or "未知错误"
            
            print("-" * 70)
            print(f"❌ {step_name} 执行失败 (退出码: {process.returncode})")
            print(f"   用时: {elapsed:.1f} 秒")
            
            if error_msg and len(error_msg) > 0:
                print(f"\n⚠️  错误输出:")
                print("-" * 70)
                print(error_msg[:1500])
                print("-" * 70)
            
            ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
            
            return TaskResult.from_execution(
                step=step_idx,
                name=step_name,
                success=False,
                elapsed=elapsed,
                error=error_msg,
                outputs=stdout_lines,
                returncode=process.returncode,
                started_at=started_at,
            )
            
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"{type(e).__name__}: {str(e)}"
        print("-" * 70)
        print(f"❌ {step_name} 执行异常: {error_msg}")
        print(f"   用时: {elapsed:.1f} 秒")
        ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
        return TaskResult.from_execution(
            step=step_idx,
            name=step_name,
            success=False,
            elapsed=elapsed,
            error=error_msg,
            started_at=started_at,
        )


def show_status():
    """显示上次执行状态"""
    progress = ProgressManager.load_progress()

    if not progress.get("steps"):
        ui_panel("主菜单 > 配置与状态 > 查看执行状态", ["没有执行记录"])
        return
    lines = [
        f"最后更新: {progress.get('last_update', '未知')}",
        f"已完成步骤: {len(progress.get('completed_steps', []))}",
        f"失败步骤: {len(progress.get('failed_steps', []))}",
        "",
        "详细状态:",
    ]
    for step_idx, step_data in sorted(progress["steps"].items(), key=lambda x: int(x[0])):
        step_status = step_data.get("status")
        if step_status == "running":
            status = "⏸️"
        elif step_status == "success" or step_data.get("success"):
            status = "✅"
        else:
            status = "❌"
        lines.append(f"  {status} 步骤{step_idx}: {step_data['name']}")
        if step_status:
            lines.append(f"     状态: {step_status}")
        lines.append(f"     时间: {step_data.get('timestamp', '未知')}")
        lines.append(f"     用时: {step_data.get('elapsed', 0):.1f}秒")
        if step_data.get("error"):
            lines.append(f"     错误: {step_data['error'][:100]}...")
    if progress.get("failed_steps") or progress.get("running_steps"):
        lines += ["", "提示: 使用 --resume 从失败处继续执行"]
        next_step = ProgressManager.get_last_failed_step()
        if next_step:
            lines.append(f"也可以使用 --step {next_step} 重试特定步骤")
    ui_panel("主菜单 > 配置与状态 > 查看执行状态", lines)


def check_dependencies():
    """检查依赖包"""
    global DEPENDENCY_STATUS_TEXT
    required_packages = {
        "requests": "requests",
        "beautifulsoup4": "bs4",
        "playwright": "playwright",
        "Pillow": "PIL",
        "openpyxl": "openpyxl",
        "notion-client": "notion_client"
    }
    
    missing = []
    for package_name, import_name in required_packages.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(package_name)
    
    if missing:
        print(f"\n🔍 依赖检查: 缺少 {len(missing)} 个包")
        for package_name in missing:
            print(f"   ❌ {package_name}")
        print(f"   请运行: pip install {' '.join(missing)}")
        return False
    
    DEPENDENCY_STATUS_TEXT = f"OK（{len(required_packages)} 个包）"
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Daily Info Workflow System - 统一工作流启动器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_workflow.py              # 执行完整工作流
  python run_workflow.py --step 1     # 只执行第1步 (AlphaPai会议纪要)
  python run_workflow.py --step 2     # 只执行第2步 (Notion微信文章)
  python run_workflow.py --step 3     # 只执行第3步 (Notion链接收藏)
  python run_workflow.py --step 4     # 只执行第4步 (微信公众号文章)
  python run_workflow.py --step 5     # 只执行第5步 (小宇宙播客)
  python run_workflow.py --resume     # 从上次失败处继续执行
  python run_workflow.py --status     # 查看上次执行状态
  python run_workflow.py --clear      # 清空执行记录
  python run_workflow.py --yes --power none  # 非交互式运行，适合计划任务
        """
    )
    
    parser.add_argument(
        '--step',
        type=int,
        choices=[1, 2, 3, 4, 5],
        default=None,
        help='只执行指定步骤 (1-5)'
    )
    
    parser.add_argument(
        '--resume',
        action='store_true',
        help='从上次失败处继续执行'
    )
    
    parser.add_argument(
        '--status',
        action='store_true',
        help='查看上次执行状态'
    )
    
    parser.add_argument(
        '--clear',
        action='store_true',
        help='清空执行记录'
    )
    
    parser.add_argument(
        '--skip-check',
        action='store_true',
        help='跳过依赖检查'
    )

    parser.add_argument(
        '--yes',
        action='store_true',
        help='非交互式确认执行（保留原人工流程；仅指定本参数时跳过菜单和确认）'
    )

    parser.add_argument(
        '--power',
        choices=['none', 'sleep', 'hibernate', 'shutdown'],
        default='none',
        help='非交互式结束动作，配合 --yes 使用（默认 none）'
    )

    parser.add_argument(
        '--continue-on-error',
        action='store_true',
        help='某一步失败后继续执行后续步骤（非交互式推荐）'
    )

    parser.add_argument(
        '--fail-fast',
        action='store_true',
        help='某一步失败后立即停止'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只展示将执行的步骤和命令，不真正运行子工作流'
    )

    parser.add_argument(
        '--skip-ai-health-check',
        action='store_true',
        help='跳过运行前 AI 连通性检查'
    )
    
    args = parser.parse_args()
    
    if args.status:
        show_status()
        return
    
    if args.clear:
        ProgressManager.clear_progress()
        if os.path.exists(ERROR_LOG_FILE):
            os.remove(ERROR_LOG_FILE)
        print("✅ 执行记录已清空")
        return
    
    if not args.skip_check:
        if not check_dependencies():
            print("\n❌ 依赖检查失败，请先安装缺失的依赖包")
            print("   或使用 --skip-check 跳过检查")
            return

    config_ok, config_errors, config_warnings = validate_config()
    if config_warnings:
        print("\n⚠️  配置提醒:")
        for item in config_warnings:
            print(f"   - {item}")
    if not config_ok:
        print("\n❌ 配置校验失败:")
        for item in config_errors:
            print(f"   - {item}")
        show_windows_notification("日常信息工作流未启动", "配置校验失败，请检查 config.yaml 和凭证文件")
        return

    if not args.dry_run:
        run_startup_maintenance()
        archived_digests = archive_read_daily_digests()
        if archived_digests:
            print(f"\n📦 已归档日报/周报: {archived_digests} 篇")

    ai_health = None
    should_generate_daily_digest = False
    should_generate_weekly_digest = False
    
    workflow_start = time.time()

    power_choice, step_indices, steps_to_run = choose_run_plan(args)

    if not args.dry_run and not args.skip_ai_health_check:
        print("\n🤖 运行前 AI 连通性检查...")
        ai_health = check_ai_health(lambda msg, level="INFO": print(f"   [{level}] {msg}"))
        if not ai_health.get("ok"):
            print("\n❌ AI 模型预检失败，后续工作流已终止")
            print(f"   错误: {ai_health.get('error')}")
            if ai_health.get("hint"):
                print(f"   提醒: {ai_health.get('hint')}")
            show_windows_notification(
                "AI 模型预检失败",
                ai_health.get("hint") or "请检查 AI API Key、模型额度或续费状态"
            )
            write_workflow_report(
                results=[],
                started_at=datetime.fromtimestamp(workflow_start),
                elapsed_seconds=time.time() - workflow_start,
                dry_run=False,
                config_warnings=config_warnings,
                ai_health=ai_health,
                notes=["AI 预检失败，工作流未启动。"],
            )
            return

    should_generate_daily_digest = (
        not args.dry_run
        and not args.step
        and not args.resume
        and claim_first_full_run_today()
    )
    should_generate_weekly_digest = (
        not args.dry_run
        and not args.step
        and not args.resume
        and claim_first_full_run_this_week()
    )

    if not args.dry_run and any(step_idx in TRACKED_MARKDOWN_STEP_INDICES for step_idx in step_indices):
        start_new_download_manifest()
        print(f"\n🧾 已初始化本次 Markdown 下载记录")

    print_safety_notice(args.dry_run)
    if args.dry_run:
        print("\n🧪 dry-run 模式：以下命令不会实际执行")
        for step_idx, step_info in zip(step_indices, steps_to_run):
            cmd = [sys.executable, step_info['script']] + step_info['args']
            print(f"   步骤 {step_idx}: {' '.join(cmd)}")
        report_path = write_workflow_report(
            results=[
                {
                    "step": step_idx,
                    "name": step_info["name"],
                    "success": True,
                    "elapsed": 0.0,
                    "error": None,
                }
                for step_idx, step_info in zip(step_indices, steps_to_run)
            ],
            started_at=datetime.now(),
            elapsed_seconds=0.0,
            dry_run=True,
            config_warnings=config_warnings,
            ai_health={"ok": True, "provider": "skipped", "model": "dry-run"},
            notes=["dry-run 仅预演步骤，没有执行任何下载、上传、写入或删除。"],
        )
        print(f"\n📄 dry-run 摘要已生成: {report_path}")
        return
    
    results = []
    total_elapsed = 0.0
    should_stop = False
    
    for idx, (step_idx, step_info) in enumerate(zip(step_indices, steps_to_run)):
        print_step_header(step_idx, len(WORKFLOW_STEPS), step_info)
        
        task_result = run_script(
            step_info['script'],
            step_info['args'],
            step_info['name'],
            step_idx,
            int(step_info.get("timeout_seconds", 7200)),
        )
        success = task_result.success
        elapsed = task_result.elapsed
        error_msg = task_result.error
        
        results.append(task_result.to_report_dict())
        
        total_elapsed += elapsed
        
        if not success:
            diagnosis = ErrorAnalyzer.analyze_error(error_msg, step_info)
            if diagnosis:
                results[-1]["diagnostics"] = diagnosis
            
            if diagnosis:
                print_diagnosis(diagnosis)
                ErrorAnalyzer.save_error_log(step_idx, step_info['name'], error_msg, diagnosis)
            else:
                ErrorAnalyzer.save_error_log(step_idx, step_info['name'], error_msg)
            
            if args.fail_fast:
                print(f"\n⏹️  步骤 {step_idx} 失败，已按 --fail-fast 停止")
                should_stop = True
                break

            if args.yes:
                if args.continue_on_error:
                    print(f"\n⚠️  步骤 {step_idx} 失败，按 --continue-on-error 继续执行下一步")
                else:
                    print(f"\n⏹️  步骤 {step_idx} 失败，非交互模式默认停止")
                    should_stop = True
                    break
            elif not args.step:
                print(f"\n⚠️  步骤 {step_idx} 失败")
                print("   按任意键继续执行下一步，或按 Ctrl+C 退出...")
                try:
                    input()
                except KeyboardInterrupt:
                    print("\n\n⏹️  用户中断执行")
                    should_stop = True
                    break
        
        time.sleep(1)
    
    workflow_elapsed = time.time() - workflow_start
    
    print_header("工作流执行总结")
    
    success_count = sum(1 for r in results if r['success'])
    failed_count = len(results) - success_count
    
    print(f"\n📊 执行统计:")
    print(f"   总步骤数: {len(results)}")
    print(f"   成功: {success_count}")
    print(f"   失败: {failed_count}")
    print(f"   总用时: {workflow_elapsed:.1f} 秒")
    print(f"   脚本执行用时: {total_elapsed:.1f} 秒")
    
    print(f"\n📋 详细结果:")
    for r in results:
        status = "✅" if r['success'] else "❌"
        print(f"   {status} 步骤{r['step']}: {r['name']} ({r['elapsed']:.1f}s)")
    
    print("\n" + "=" * 70)
    
    if success_count == len(results):
        print("🎉 所有步骤执行成功！")
        ProgressManager.clear_progress()
        show_windows_notification(
            "日常信息工作流完成",
            f"成功执行 {success_count} 个步骤，用时 {workflow_elapsed:.0f} 秒"
        )
    elif success_count > 0:
        print(f"⚠️  部分步骤执行成功 ({success_count}/{len(results)})")
        print(f"\n💡 提示:")
        print(f"   - 使用 --resume 从失败处继续执行")
        print(f"   - 使用 --status 查看详细状态")
        print(f"   - 查看错误日志: {ERROR_LOG_FILE}")
        show_windows_notification(
            "日常信息工作流部分完成",
            f"成功 {success_count} 个，失败 {failed_count} 个"
        )
    else:
        print("❌ 所有步骤执行失败")
        print(f"\n💡 提示:")
        print(f"   - 查看错误日志: {ERROR_LOG_FILE}")
        print(f"   - 使用 --status 查看详细状态")
        print(f"   - 检查依赖包: python run_workflow.py --skip-check")
        show_windows_notification(
            "日常信息工作流失败",
            f"所有 {len(results)} 个步骤执行失败"
        )
    
    print("=" * 70 + "\n")

    report_path = write_workflow_report(
        results=results,
        started_at=datetime.fromtimestamp(workflow_start),
        elapsed_seconds=workflow_elapsed,
        dry_run=False,
        config_warnings=config_warnings,
        ai_health=ai_health,
        notes=[
            "如某个来源积累了过多未处理内容，各子工作流会按 config.yaml 中的每源上限处理，并在日志中说明达到上限。"
        ],
    )
    print(f"📄 每日运行摘要已生成: {report_path}")

    if should_generate_daily_digest:
        print("\n🗞️  今天第一次完整运行，开始生成信息汇总日报...")
        digest_path = generate_daily_digest()
        if digest_path:
            print(f"🗞️  信息汇总日报已生成: {digest_path}")
        else:
            print("⚠️  信息汇总日报生成失败，详情请查看运行日志。")

    if should_generate_weekly_digest:
        print("\n🗞️  本周第一次完整运行，开始生成信息汇总周报...")
        weekly_path = generate_weekly_digest()
        if weekly_path:
            print(f"🗞️  信息汇总周报已生成: {weekly_path}")
        else:
            print("⚠️  信息汇总周报生成失败，详情请查看运行日志。")
    
    # 执行电源操作
    if power_choice['action'] != 'none':
        schedule_power_action(power_choice['action'], power_choice['delay'])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  用户中断执行")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 工作流执行异常: {e}")
        print(f"\n堆栈跟踪:")
        traceback.print_exc()
        sys.exit(1)

