from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from common_libs.ai.ai_client import create_ai_client
from common_libs.ai.quality import basic_ai_response_ok, clean_thinking_tags
from common_libs.config.config_loader import get as cfg
from common_libs.runtime.task_state import load_task_state
from common_libs.storage.download_history import save_json_atomic
from common_libs.utils.paths import OBSIDIAN_BASE_DIR, PROJECT_ROOT


STATE_FILE = Path(PROJECT_ROOT) / "data" / "logs" / "daily_digest_state.json"
SOURCE_FILE_TEMPLATE = "daily_digest_sources_{date}.md"
WEEKLY_SOURCE_FILE_TEMPLATE = "weekly_digest_sources_{date}.md"

DEFAULT_MAX_INPUT_CHARS = 120_000
DEFAULT_CHUNK_CHARS = 80_000


DAILY_DIGEST_PROMPT = """你是一位为投资研究者服务的信息总编。下面是今天自动收集到的会议纪要、微信文章和播客要点正文。

请写一份“信息汇总日报”，目标是帮助我把握今天内容中真正有价值、值得注意、可能影响投资研究和长期认知积累的信息。

写作要求：
1. 先给出 5-10 条最值得注意的核心观察，不要平均用力。
2. 按主题组织内容，而不是按来源流水账罗列。
3. 明确指出重要公司、行业、宏观变量、商业模式、政策变化、风险信号、反常识观点和可继续跟踪的问题。
4. 区分事实、观点和你的综合判断；不要编造原文没有的信息。
5. 对明显重复的信息做合并，对信息密度低的内容可以简略处理。
6. 输出 Markdown，结构清晰，适合保存到 Obsidian。

【今日材料】
{content}
"""


CHUNK_PROMPT = """你是一位投资研究信息筛选助手。下面是今日材料的一部分。

请提炼这一部分中值得进入日报汇总的内容：
- 重要事实、数据、公司、行业、政策、风险信号
- 有启发或反常识的观点
- 值得继续跟踪的问题
- 可以忽略的信息请压缩或省略

请输出结构化 Markdown，不要编造原文没有的信息。

【材料分段】
{content}
"""


SYNTHESIS_PROMPT = """你是一位为投资研究者服务的信息总编。下面是今日材料分段提炼后的结果。

请综合成一份完整的“信息汇总日报”。要求：
1. 先给出 5-10 条最值得注意的核心观察。
2. 按主题组织，不按分段流水账。
3. 合并重复内容，保留重要公司、行业、宏观变量、政策、风险和待跟踪问题。
4. 明确区分事实、观点和综合判断。
5. 输出 Markdown，适合保存到 Obsidian。

【分段提炼】
{content}
"""


WEEKLY_DIGEST_PROMPT = """你是一位为投资研究者服务的周度信息总编。下面是本周每天的信息汇总日报。

请写一份“信息汇总周报”，目标是帮助我从一周的信息流里提炼真正值得注意、值得跟踪、可能影响投资研究和长期认知积累的内容。

写作要求：
1. 先给出本周 8-12 条最重要的核心观察，强调变化、共识、分歧和反复出现的线索。
2. 按主题组织，不要按日期流水账复述。
3. 提炼本周重要公司、行业、宏观变量、政策变化、商业模式、风险信号和反常识观点。
4. 明确区分事实、观点和综合判断；不要编造日报里没有的信息。
5. 给出“下周值得继续跟踪的问题/线索”。
6. 输出 Markdown，结构清晰，适合保存到 Obsidian。

【本周日报材料】
{content}
"""


WEEKLY_CHUNK_PROMPT = """你是一位投资研究信息筛选助手。下面是本周日报材料的一部分。

请提炼这一部分中值得进入周报的内容：
- 本周反复出现或互相印证的重要线索
- 重要事实、公司、行业、政策、风险信号
- 值得继续跟踪的问题
- 可以忽略的日常噪音请压缩或省略

请输出结构化 Markdown，不要编造原文没有的信息。

【本周材料分段】
{content}
"""


WEEKLY_SYNTHESIS_PROMPT = """你是一位为投资研究者服务的周度信息总编。下面是本周日报材料分段提炼后的结果。

请综合成一份完整的“信息汇总周报”。要求：
1. 先给出本周 8-12 条最重要的核心观察。
2. 按主题组织，突出变化、趋势、共识、分歧和待验证线索。
3. 合并重复内容，保留重要公司、行业、宏观变量、政策、风险和下周待跟踪问题。
4. 明确区分事实、观点和综合判断。
5. 输出 Markdown，适合保存到 Obsidian。

【分段提炼】
{content}
"""


LogFunc = Callable[[str], None]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _today_compact() -> str:
    return datetime.now().strftime("%Y%m%d")


def _week_start(day: date | None = None) -> date:
    day = day or date.today()
    return day - timedelta(days=day.weekday())


def _date_compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    save_json_atomic(state, STATE_FILE)


def claim_first_full_run_today() -> bool:
    """在启动器开始时判定并记录今天的第一次正式全量运行。"""
    today = _today()
    state = _load_state()
    if state.get("first_full_run_date") == today:
        return False
    state["first_full_run_date"] = today
    state["first_full_run_at"] = datetime.now().isoformat(timespec="seconds")
    state["digest_status"] = "pending"
    _save_state(state)
    return True


def claim_first_full_run_this_week() -> bool:
    """在启动器开始时判定并记录本周第一次正式全量运行。周一作为一周第一天。"""
    week_key = _week_start().isoformat()
    state = _load_state()
    if state.get("first_full_run_week") == week_key:
        return False
    state["first_full_run_week"] = week_key
    state["first_full_run_week_at"] = datetime.now().isoformat(timespec="seconds")
    state["weekly_digest_status"] = "pending"
    _save_state(state)
    return True


def mark_digest_result(status: str, path: str = "", error: str = "") -> None:
    state = _load_state()
    state["digest_status"] = status
    state["digest_finished_at"] = datetime.now().isoformat(timespec="seconds")
    if path:
        state["digest_path"] = path
    if error:
        state["digest_error"] = error
    _save_state(state)


def mark_weekly_digest_result(status: str, path: str = "", error: str = "") -> None:
    state = _load_state()
    state["weekly_digest_status"] = status
    state["weekly_digest_finished_at"] = datetime.now().isoformat(timespec="seconds")
    if path:
        state["weekly_digest_path"] = path
    if error:
        state["weekly_digest_error"] = error
    _save_state(state)


def _daily_dirs() -> tuple[Path, Path]:
    base = Path(OBSIDIAN_BASE_DIR) / cfg("daily_digest.base_dir", "Bz日报周报")
    unread = base / cfg("daily_digest.unread_folder_name", "未读")
    read = base / cfg("daily_digest.read_folder_name", "已读")
    unread.mkdir(parents=True, exist_ok=True)
    read.mkdir(parents=True, exist_ok=True)
    return unread, read


def archive_read_daily_digests(log_func: LogFunc = print) -> int:
    unread, read = _daily_dirs()
    moved = 0
    for md_file in unread.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as exc:
            log_func(f"读取日报失败 {md_file}: {exc}")
            continue
        if "- [x] **是否已读**" not in content:
            continue

        year, month = _extract_year_month(md_file.name, content)
        dest_dir = read / f"{year}年" / f"{year}年{month}月"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / md_file.name
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{md_file.stem}_{counter}{md_file.suffix}"
            counter += 1
        shutil.move(str(md_file), str(dest))
        moved += 1
        log_func(f"已归档日报/周报: {md_file.name} -> {dest_dir.parent.name}/{dest_dir.name}/")
    return moved


def _extract_year_month(filename: str, content: str) -> tuple[int, int]:
    match = re.search(r"(\d{4})(\d{2})\d{2}", filename)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"\*\*日期\*\*:\s*(\d{4})-(\d{2})-\d{2}", content)
    if match:
        return int(match.group(1)), int(match.group(2))
    today = date.today()
    return today.year, today.month


def _extract_report_date(path: Path, content: str = "") -> date | None:
    match = re.search(r"(\d{4})(\d{2})(\d{2})", path.name)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    if not content:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None
    match = re.search(r"\*\*日期\*\*:\s*(\d{4})-(\d{2})-(\d{2})", content)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def collect_today_documents() -> list[dict[str, Any]]:
    state = load_task_state()
    tasks = state.get("tasks", {})
    today = _today()
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    workflow_names = {
        "memo": "会议纪要",
        "wechat": "AlphaPai 公众号文章",
        "notion_wechat": "Notion 微信收藏",
        "podcast_transcript": "小宇宙播客",
    }

    for task in tasks.values():
        if not isinstance(task, dict):
            continue
        if task.get("status") != "markdown_saved":
            continue
        if not str(task.get("updated_at", "")).startswith(today):
            continue
        workflow = task.get("workflow", "")
        if workflow not in workflow_names:
            continue
        meta = task.get("meta") or {}
        path = meta.get("path") or meta.get("md_path")
        if not path:
            continue
        md_path = Path(path)
        if not md_path.exists() or md_path.suffix.lower() != ".md":
            continue
        key = str(md_path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        docs.append({
            "workflow": workflow,
            "type": workflow_names[workflow],
            "title": task.get("title") or md_path.stem,
            "path": md_path,
            "updated_at": task.get("updated_at", ""),
        })

    docs.sort(key=lambda item: (item["type"], str(item["path"])))
    return docs


def extract_digest_body(path: Path, workflow: str) -> str:
    text = path.read_text(encoding="utf-8")
    if workflow == "podcast_transcript":
        return _section_after_heading(text, "# AI 要点")
    if workflow in {"wechat", "notion_wechat"}:
        return _section_after_heading(text, "# 正文")
    if workflow == "memo":
        if "# 会议全文" in text:
            return _section_after_heading(text, "# 会议全文")
        return _between_first_content_separators(text)
    return _between_first_content_separators(text)


def _section_after_heading(text: str, heading: str) -> str:
    idx = text.find(heading)
    if idx >= 0:
        body = text[idx + len(heading):]
    else:
        body = _between_first_content_separators(text)
    return _strip_trailing_meta(body)


def _between_first_content_separators(text: str) -> str:
    parts = re.split(r"\n---\n", text, maxsplit=2)
    if len(parts) >= 2:
        body = parts[1]
        if len(parts) == 3:
            body = body
        return _strip_trailing_meta(body)
    return _strip_trailing_meta(text)


def _strip_trailing_meta(text: str) -> str:
    text = re.split(r"\n---\s*\n\s*# AI 评价\b", text, maxsplit=1)[0]
    text = re.split(r"\n# AI 评价\b", text, maxsplit=1)[0]
    text = re.split(r"\n---\s*\n\s*# 会议全文\b", text, maxsplit=1)[0]
    text = re.split(r"\n# 会议全文\b", text, maxsplit=1)[0]
    text = re.sub(r"\n---\n\s*\*下载时间:.*$", "", text, flags=re.DOTALL)
    text = re.sub(r"\n---\n\s*\*整理时间:.*$", "", text, flags=re.DOTALL)
    return text.strip()


def _build_source_text(docs: list[dict[str, Any]]) -> str:
    sections = []
    for idx, doc in enumerate(docs, 1):
        try:
            body = extract_digest_body(doc["path"], doc["workflow"])
        except Exception as exc:
            body = f"读取失败: {exc}"
        if not body:
            continue
        sections.append(
            "\n".join([
                f"## 材料 {idx}: {doc['title']}",
                f"- 类型: {doc['type']}",
                f"- 文件: {doc['path']}",
                "",
                body,
            ])
        )
    return "\n\n---\n\n".join(sections)


def _split_text(text: str, chunk_chars: int) -> list[str]:
    if len(text) <= chunk_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in re.split(r"\n(?=## 材料 \d+:)", text):
        if current and current_len + len(block) > chunk_chars:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        if len(block) > chunk_chars:
            for i in range(0, len(block), chunk_chars):
                chunks.append(block[i:i + chunk_chars])
            continue
        current.append(block)
        current_len += len(block)
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk.strip()]


def _ai_call(prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
    client = create_ai_client()
    response, _ = client.call_for_long_thinking(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        max_attempts_per_model=3,
        max_models=3,
    )
    cleaned = clean_thinking_tags(response)
    ok, reason = basic_ai_response_ok(cleaned, min_chars=100)
    if not ok:
        raise RuntimeError(f"日报 AI 结果质量检查未通过: {reason}")
    return cleaned


def _generate_from_source(
    source_text: str,
    direct_prompt: str,
    chunk_prompt: str,
    synthesis_prompt: str,
    config_prefix: str,
) -> str:
    max_input_chars = int(cfg("daily_digest.max_input_chars", DEFAULT_MAX_INPUT_CHARS))
    chunk_chars = int(cfg("daily_digest.chunk_chars", DEFAULT_CHUNK_CHARS))
    max_tokens = int(cfg(f"{config_prefix}.max_tokens", 12000))
    chunk_max_tokens = int(cfg(f"{config_prefix}.chunk_max_tokens", 7000))
    synthesis_max_tokens = int(cfg(f"{config_prefix}.synthesis_max_tokens", 12000))

    if len(source_text) <= max_input_chars:
        return _ai_call(direct_prompt.format(content=source_text), max_tokens=max_tokens)

    chunk_summaries = []
    for idx, chunk in enumerate(_split_text(source_text, chunk_chars), 1):
        summary = _ai_call(chunk_prompt.format(content=chunk), max_tokens=chunk_max_tokens)
        chunk_summaries.append(f"## 分段 {idx}\n\n{summary}")

    joined = "\n\n---\n\n".join(chunk_summaries)
    return _ai_call(synthesis_prompt.format(content=joined), max_tokens=synthesis_max_tokens)


def generate_digest_from_source(source_text: str) -> str:
    return _generate_from_source(
        source_text,
        DAILY_DIGEST_PROMPT,
        CHUNK_PROMPT,
        SYNTHESIS_PROMPT,
        "daily_digest",
    )


def generate_weekly_digest_from_source(source_text: str) -> str:
    return _generate_from_source(
        source_text,
        WEEKLY_DIGEST_PROMPT,
        WEEKLY_CHUNK_PROMPT,
        WEEKLY_SYNTHESIS_PROMPT,
        "weekly_digest",
    )


def _unique_digest_path(unread_dir: Path) -> Path:
    return _unique_report_path(unread_dir, _today_compact(), "日报")


def _unique_report_path(unread_dir: Path, date_prefix: str, kind: str) -> Path:
    base = f"{date_prefix}_信息汇总{kind}"
    path = unread_dir / f"{base}.md"
    counter = 1
    while path.exists():
        path = unread_dir / f"{base}_{counter}.md"
        counter += 1
    return path


def write_daily_digest(body: str, docs: list[dict[str, Any]]) -> Path:
    unread, _ = _daily_dirs()
    path = _unique_digest_path(unread)
    lines = [
        "# 信息汇总日报",
        "",
        "## 基本信息",
        "",
        f"- **日期**: {_today()}",
        f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **来源文档数**: {len(docs)}",
        "- [ ] **是否已读**",
        "- **人工标签**: ",
        "- **我的评价**: ",
        "",
        "---",
        "",
        body.strip(),
        "",
        "---",
        "",
        "## 来源文档",
        "",
    ]
    if docs:
        lines.extend(f"- {doc['type']} | {doc['title']} | `{doc['path']}`" for doc in docs)
    else:
        lines.append("- 今日没有新增可汇总文档。")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _extract_report_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"\n---\n", text, maxsplit=2)
    if len(parts) >= 2:
        return parts[1].strip()
    return text.strip()


def collect_weekly_daily_digests(week_start: date | None = None) -> list[dict[str, Any]]:
    week_start = week_start or _week_start()
    week_end = week_start + timedelta(days=6)
    unread, read = _daily_dirs()
    candidates = list(unread.glob("*_信息汇总日报*.md")) + list(read.rglob("*_信息汇总日报*.md"))
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        report_date = _extract_report_date(path)
        if not report_date or not (week_start <= report_date <= week_end):
            continue
        docs.append({
            "date": report_date,
            "title": path.stem,
            "path": path,
        })
    docs.sort(key=lambda item: (item["date"], str(item["path"])))
    return docs


def _build_weekly_source_text(docs: list[dict[str, Any]]) -> str:
    sections = []
    for idx, doc in enumerate(docs, 1):
        try:
            body = _extract_report_body(doc["path"])
        except Exception as exc:
            body = f"读取失败: {exc}"
        if not body:
            continue
        sections.append(
            "\n".join([
                f"## 日报 {idx}: {doc['date'].isoformat()} {doc['title']}",
                f"- 文件: {doc['path']}",
                "",
                body,
            ])
        )
    return "\n\n---\n\n".join(sections)


def write_weekly_digest(body: str, docs: list[dict[str, Any]], week_start: date | None = None) -> Path:
    week_start = week_start or _week_start()
    unread, _ = _daily_dirs()
    path = _unique_report_path(unread, _date_compact(week_start), "周报")
    week_end = week_start + timedelta(days=6)
    lines = [
        "# 信息汇总周报",
        "",
        "## 基本信息",
        "",
        f"- **日期**: {week_start.isoformat()}",
        f"- **周期**: {week_start.isoformat()} 至 {week_end.isoformat()}",
        f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **来源日报数**: {len(docs)}",
        "- [ ] **是否已读**",
        "- **人工标签**: ",
        "- **我的评价**: ",
        "",
        "---",
        "",
        body.strip(),
        "",
        "---",
        "",
        "## 来源日报",
        "",
    ]
    if docs:
        lines.extend(f"- {doc['date'].isoformat()} | `{doc['path']}`" for doc in docs)
    else:
        lines.append("- 本周没有找到可汇总的日报。")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_daily_digest(log_func: LogFunc = print) -> Path | None:
    archive_read_daily_digests(log_func=log_func)
    docs = collect_today_documents()
    source_path = Path(PROJECT_ROOT) / "data" / "logs" / SOURCE_FILE_TEMPLATE.format(date=_today_compact())
    source_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        source_text = _build_source_text(docs)
        source_path.write_text(source_text, encoding="utf-8")
        if not source_text.strip():
            body = "## 今日概览\n\n今天没有新增可用于汇总的会议纪要、微信文章或播客笔记。"
        else:
            body = generate_digest_from_source(source_text)
        digest_path = write_daily_digest(body, docs)
        mark_digest_result("generated", path=str(digest_path))
        log_func(f"日报已生成: {digest_path}")
        return digest_path
    except Exception as exc:
        mark_digest_result("failed", error=str(exc))
        log_func(f"日报生成失败: {exc}")
        return None
    finally:
        try:
            if source_path.exists():
                source_path.unlink()
        except Exception:
            pass


def generate_weekly_digest(log_func: LogFunc = print) -> Path | None:
    archive_read_daily_digests(log_func=log_func)
    week_start = _week_start()
    docs = collect_weekly_daily_digests(week_start)
    source_path = Path(PROJECT_ROOT) / "data" / "logs" / WEEKLY_SOURCE_FILE_TEMPLATE.format(date=_date_compact(week_start))
    source_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        source_text = _build_weekly_source_text(docs)
        source_path.write_text(source_text, encoding="utf-8")
        if not source_text.strip():
            body = "## 本周概览\n\n本周没有找到可用于汇总的信息汇总日报。"
        else:
            body = generate_weekly_digest_from_source(source_text)
        digest_path = write_weekly_digest(body, docs, week_start)
        mark_weekly_digest_result("generated", path=str(digest_path))
        log_func(f"周报已生成: {digest_path}")
        return digest_path
    except Exception as exc:
        mark_weekly_digest_result("failed", error=str(exc))
        log_func(f"周报生成失败: {exc}")
        return None
    finally:
        try:
            if source_path.exists():
                source_path.unlink()
        except Exception:
            pass
