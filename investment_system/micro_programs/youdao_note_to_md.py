#!/usr/bin/env python3
"""Download public Youdao Cloud Note share links as local Markdown files."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import html2text
import requests
from bs4 import BeautifulSoup


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from investment_system.common.utils.paths import ATTACHMENT_DIR, MEMO_BASE_DIR
    from investment_system.common.markdown_utils import bump_headings_when_h1_exists, normalize_markdown_output

    DEFAULT_OUT_DIR = Path(MEMO_BASE_DIR) / "0-Inbox"
    DEFAULT_ATTACHMENT_DIR = Path(ATTACHMENT_DIR)
except Exception:
    from investment_system.common.markdown_utils import bump_headings_when_h1_exists, normalize_markdown_output

    DEFAULT_OUT_DIR = Path(r"D:\softwares\Obsidian\MyNotes\信息收集器\C会议纪要\0-Inbox")
    DEFAULT_ATTACHMENT_DIR = Path(r"D:\softwares\Obsidian\MyNotes\信息收集器\_overall\_attachment")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class DownloadError(RuntimeError):
    """Raised when a share note cannot be downloaded or parsed."""


@dataclass
class NoteDocument:
    title: str
    markdown: str
    final_url: str


def is_youdao_note_url(url: str) -> bool:
    """Return True when the URL looks like a Youdao Cloud Note share link."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if not (host == "note.youdao.com" or host.endswith(".note.youdao.com")):
        return False

    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    if path.startswith("/s/") and len(path.strip("/").split("/")) >= 2:
        return True
    if any(token in path for token in ("/ynoteshare/", "/share/", "/ynoteshare")):
        return "id" in query
    return False


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


def resolve_share_url(session: requests.Session, url: str, timeout: int = 30) -> tuple[str, str]:
    """Return (share_key, final_url), resolving short /s/ links when needed."""
    if not is_youdao_note_url(url):
        raise DownloadError("不是有道云链接：请提供 note.youdao.com 的有道云笔记分享链接。")

    response = session.get(url, allow_redirects=True, timeout=timeout)
    response.raise_for_status()
    final_url = response.url

    parsed = urlparse(final_url)
    share_key = parse_qs(parsed.query).get("id", [""])[0]
    if share_key:
        return share_key, final_url

    match = re.search(r"[?&]id=([0-9a-fA-F]{16,64})", response.text)
    if match:
        return match.group(1), final_url

    raise DownloadError("这是有道云链接，但没有解析到分享 id。")


def safe_filename(name: str, default: str = "有道云笔记") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ")
    return (cleaned or default)[:120]


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        numbered = directory / f"{stem} ({index}){suffix}"
        if not numbered.exists():
            return numbered
    raise DownloadError(f"无法生成不重复文件名：{candidate}")


def extension_from_response(url: str, response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    by_type = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
    }
    if content_type in by_type:
        return by_type[content_type]

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def download_markdown_images(markdown: str, title: str, attachment_dir: Path = DEFAULT_ATTACHMENT_DIR) -> str:
    image_re = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
    matches = list(image_re.finditer(markdown))
    if not matches:
        return markdown

    attachment_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    replacements: dict[str, str] = {}
    image_index = 1

    def replace(match: re.Match[str]) -> str:
        nonlocal image_index
        alt_text, image_url = match.group(1), match.group(2)
        if image_url not in replacements:
            response = session.get(image_url, timeout=60)
            response.raise_for_status()
            ext = extension_from_response(image_url, response)
            stem = safe_filename(title, default="youdao_note")
            output_path = unique_path(attachment_dir, f"{stem}_image_{image_index:02d}{ext}")
            image_index += 1
            output_path.write_bytes(response.content)
            replacements[image_url] = output_path.name
        return f"![{alt_text}]({replacements[image_url]})"

    return image_re.sub(replace, markdown)


def make_converter(base_url: str) -> html2text.HTML2Text:
    converter = html2text.HTML2Text(baseurl=base_url)
    converter.body_width = 0
    converter.ignore_links = False
    converter.ignore_images = False
    converter.ignore_emphasis = False
    converter.protect_links = True
    converter.unicode_snob = True
    return converter


def normalize_markdown(markdown: str) -> str:
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").split("\n")]
    compacted: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            compacted.append(line)
            blank_count = 0
            continue
        blank_count += 1
        if blank_count <= 2:
            compacted.append("")
    return "\n".join(compacted).strip()


def html_to_markdown(title: str, html: str, source_url: str) -> NoteDocument:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    for tag in soup.find_all(src=True):
        tag["src"] = urljoin(source_url, tag["src"])
    for tag in soup.find_all(href=True):
        tag["href"] = urljoin(source_url, tag["href"])

    markdown = make_converter(source_url).handle(str(soup))
    markdown = normalize_markdown(markdown)
    if not markdown:
        raise DownloadError("页面加载成功，但没有解析出笔记正文。")

    return NoteDocument(title=title.strip() or "有道云笔记", markdown=markdown, final_url=source_url)


def visible_text_len(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    return len(soup.get_text("", strip=True))


def title_from_soup(soup: BeautifulSoup) -> str:
    for selector in ("h1", ".title", ".note-title", "[class*=title]"):
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node.get_text(" ", strip=True)
    if soup.title and soup.title.get_text(strip=True):
        return re.sub(r"\s*[-_丨|].*$", "", soup.title.get_text(" ", strip=True)).strip()
    return "有道云笔记"


def candidate_nodes(soup: BeautifulSoup) -> Iterable:
    selectors = (
        "#noteContent",
        "#content",
        ".note-content",
        ".noteContent",
        ".reader-content",
        ".content",
        ".article",
        ".ynote",
        "[class*=note][class*=content]",
        "[class*=editor]",
        "article",
        "main",
    )
    yielded = set()
    for selector in selectors:
        for node in soup.select(selector):
            identity = id(node)
            if identity not in yielded:
                yielded.add(identity)
                yield node
    if soup.body:
        yield soup.body


def parse_static_html(html: str, source_url: str) -> NoteDocument:
    soup = BeautifulSoup(html, "html.parser")
    title = title_from_soup(soup)

    best_html = ""
    best_score = 0
    for node in candidate_nodes(soup):
        node_html = str(node)
        score = visible_text_len(node_html)
        if score > best_score:
            best_score = score
            best_html = node_html

    if best_score < 20:
        raise DownloadError("静态页面中没有足够的笔记正文，可能需要浏览器渲染。")
    return html_to_markdown(title, best_html, source_url)


def parse_embedded_json(html: str, source_url: str) -> NoteDocument | None:
    """Try common server-injected JSON shapes before falling back to DOM parsing."""
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    for script in scripts:
        for match in re.finditer(r"(\{[^{}]*(?:title|content|body|note)[^{}]*\})", script, flags=re.S):
            raw = match.group(1)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            title = str(data.get("title") or data.get("name") or "有道云笔记")
            content = data.get("content") or data.get("body") or data.get("note")
            if isinstance(content, str) and len(BeautifulSoup(content, "html.parser").get_text(strip=True)) > 20:
                return html_to_markdown(title, content, source_url)
    return None


def fetch_with_requests(url: str, timeout: int = 30) -> NoteDocument:
    session = make_session()
    share_key, final_url = resolve_share_url(session, url, timeout=timeout)
    return fetch_with_youdao_api(session, share_key, final_url, timeout=timeout)


def fetch_with_youdao_api(
    session: requests.Session,
    share_key: str,
    final_url: str,
    timeout: int = 30,
) -> NoteDocument:
    unlogin_id = str(uuid.uuid4())
    share_api = "https://share.note.youdao.com/yws/api/personal/share"
    share_response = session.get(
        share_api,
        params={
            "method": "get",
            "shareKey": share_key,
            "unloginId": unlogin_id,
            "sec": "v1",
        },
        headers={"Referer": final_url},
        timeout=timeout,
    )
    share_response.raise_for_status()
    share_data = share_response.json()

    entry = share_data.get("entry") or {}
    file_meta = share_data.get("fileMeta") or {}
    file_id = entry.get("id")
    if not file_id:
        raise DownloadError("分享元数据中没有找到笔记文件 id。")

    editor_version = entry.get("editorVersion") or "j1"
    editor_type = entry.get("orgEditorType") or file_meta.get("noteType") or 1
    note_api = f"https://share.note.youdao.com/yws/api/note/{share_key}/{file_id}"
    note_response = session.get(
        note_api,
        params={
            "sev": editor_version,
            "editorType": editor_type,
            "unloginId": unlogin_id,
            "editorVersion": "new-json-editor",
            "sec": "v1",
        },
        headers={"Referer": final_url},
        timeout=timeout,
    )
    note_response.raise_for_status()
    note_data = note_response.json()

    title = str(note_data.get("tl") or entry.get("name") or file_meta.get("title") or share_data.get("name") or "有道云笔记")
    title = re.sub(r"\.note$", "", title, flags=re.I).strip()
    raw_content = note_data.get("content")
    if not raw_content:
        raise DownloadError("有道接口返回成功，但没有正文 content 字段。")

    markdown = parse_youdao_editor_content(raw_content)
    markdown = normalize_markdown(markdown)
    if not markdown:
        raise DownloadError("有道接口返回了正文 JSON，但没有解析出有效 Markdown。")
    return NoteDocument(title=title, markdown=markdown, final_url=final_url)


def parse_youdao_editor_content(raw_content: str | dict[str, Any]) -> str:
    if isinstance(raw_content, str):
        try:
            content = json.loads(raw_content)
        except json.JSONDecodeError:
            return make_converter("").handle(raw_content)
    else:
        content = raw_content

    blocks = content.get("5")
    if not isinstance(blocks, list):
        raise DownloadError("暂不支持这种有道正文 JSON 结构：缺少块列表。")

    lines: list[str] = []
    list_counters: dict[tuple[str, int], int] = {}
    for block in blocks:
        lines.extend(render_youdao_block(block, list_counters))
    return "\n".join(add_spacing_after_lists(lines))


def add_spacing_after_lists(lines: list[str]) -> list[str]:
    spaced: list[str] = []
    list_item_re = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
    for line in lines:
        if (
            spaced
            and spaced[-1].strip()
            and list_item_re.match(spaced[-1])
            and line.strip()
            and not list_item_re.match(line)
        ):
            spaced.append("")
        spaced.append(line)
    return spaced


def render_youdao_block(block: dict[str, Any], list_counters: dict[tuple[str, int], int]) -> list[str]:
    block_type = block.get("6")
    attrs = block.get("4") if isinstance(block.get("4"), dict) else {}

    if block_type == "im":
        image_url = attrs.get("u") or attrs.get("url") or ""
        if image_url:
            return [f"![]({image_url})", ""]
        return []

    text = collect_youdao_text(block).strip()
    children = block.get("5") if isinstance(block.get("5"), list) else []

    if block_type == "h" and text:
        heading_type = str(attrs.get("l") or "h2")
        match = re.fullmatch(r"h([1-6])", heading_type)
        level = int(match.group(1)) if match else 2
        return [f"{'#' * level} {text}", ""]

    if block_type == "l" or attrs.get("lt") in {"unordered", "ordered"}:
        list_type = attrs.get("lt", "unordered")
        level = int(attrs.get("ll") or 1)
        indent = "\t" * max(level - 1, 0)
        if list_type == "ordered":
            list_id = str(attrs.get("li") or f"default-{level}")
            counter_key = (list_id, level)
            list_counters[counter_key] = list_counters.get(counter_key, 0) + 1
            marker = f"{list_counters[counter_key]}. "
        else:
            marker = "- "
        return [f"{indent}{marker}{text}" if text else ""]

    if block_type in {"h1", "h2", "h3", "h4", "h5", "h6"} and text:
        level = int(block_type[1])
        return [f"{'#' * level} {text}", ""]

    if block_type == "hr":
        return ["---", ""]

    if text:
        return [text, ""]

    lines: list[str] = []
    for child in children:
        if isinstance(child, dict):
            lines.extend(render_youdao_block(child, list_counters))
    return lines


def collect_youdao_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""

    pieces: list[str] = []
    runs = node.get("7")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            text = str(run.get("8") or "")
            if text:
                pieces.append(apply_youdao_marks(text, run.get("9")))

    children = node.get("5")
    if isinstance(children, list):
        for child in children:
            pieces.append(collect_youdao_text(child))

    return "".join(pieces)


def apply_youdao_marks(text: str, marks: Any) -> str:
    if not isinstance(marks, list):
        return text

    mark_names = {str(mark.get("2")) for mark in marks if isinstance(mark, dict)}
    escaped = text.replace("\n", "  \n")
    if "b" in mark_names:
        escaped = f"**{escaped}**"
    if "i" in mark_names:
        escaped = f"*{escaped}*"
    if "s" in mark_names:
        escaped = f"~~{escaped}~~"
    return escaped


def fetch_with_playwright(url: str, timeout_ms: int = 45000) -> NoteDocument:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DownloadError("需要安装 playwright 才能渲染动态页面。") from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, locale="zh-CN")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
                time.sleep(1)

                title = page.title() or "有道云笔记"
                candidates = []
                selectors = [
                    "#noteContent",
                    "#content",
                    ".note-content",
                    ".reader-content",
                    ".content",
                    ".article",
                    "article",
                    "main",
                    "body",
                ]
                for selector in selectors:
                    locator = page.locator(selector).first
                    try:
                        if locator.count():
                            candidates.append(locator.inner_html(timeout=3000))
                    except Exception:
                        continue
            finally:
                browser.close()
    except Exception as exc:
        raise DownloadError(str(exc)) from exc

    best_html = max(candidates, key=visible_text_len, default="")
    if visible_text_len(best_html) < 20:
        raise DownloadError("浏览器渲染后仍未解析出足够的笔记正文。")
    return html_to_markdown(title, best_html, url)


def download_youdao_note(
    url: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    use_browser: bool = True,
    attachment_dir: Path = DEFAULT_ATTACHMENT_DIR,
    overwrite: bool = False,
) -> Path:
    if not is_youdao_note_url(url):
        raise DownloadError("不是有道云链接：请提供 note.youdao.com 的有道云笔记分享链接。")

    try:
        document = fetch_with_requests(url)
    except Exception as first_error:
        if not use_browser:
            raise DownloadError(f"下载失败：{first_error}") from first_error
        try:
            document = fetch_with_playwright(url)
        except Exception as second_error:
            raise DownloadError(f"下载失败：HTTP 抓取失败（{first_error}）；浏览器渲染也失败（{second_error}）。") from second_error

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(document.title) + ".md"
    output_path = out_dir / filename if overwrite else unique_path(out_dir, filename)
    markdown = download_markdown_images(document.markdown, document.title, attachment_dir=attachment_dir)
    content = build_youdao_markdown(document, url, markdown)
    output_path.write_text(normalize_markdown_output(content), encoding="utf-8")
    return output_path


def build_youdao_markdown(document: NoteDocument, source_url: str, body_markdown: str | None = None) -> str:
    body = body_markdown if body_markdown is not None else document.markdown
    body = bump_headings_when_h1_exists(normalize_markdown(body))
    lines = [
        "# 基本信息",
        "",
        f"- **日期**: {datetime.now().strftime('%Y-%m-%d')}",
        f"- **原文链接**: [{document.title}]({source_url})",
        "- **行业**: ",
        "- **公司**: ",
        "- **人工标签**: ",
        "- [ ] **是否已读**",
        "- **我的评价**: ",
        "",
        "---",
        "",
        "# 正文",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def parse_url_inputs(values: list[str]) -> list[str]:
    urls: list[str] = []
    for value in values:
        for url in re.findall(r"https?://[^\s,，]+", value):
            cleaned = url.strip().rstrip("，,。")
            if is_youdao_note_url(cleaned) and cleaned not in urls:
                urls.append(cleaned)
    return urls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把公开的有道云笔记分享链接下载为 Markdown 文件。")
    parser.add_argument("urls", nargs="+", help="一个或多个有道云笔记分享链接")
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"Markdown 保存目录，默认：{DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--attachment-dir",
        default=str(DEFAULT_ATTACHMENT_DIR),
        help=f"图片附件保存目录，默认：{DEFAULT_ATTACHMENT_DIR}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="如果同名 Markdown 已存在，直接覆盖而不是生成 (1)、(2) 文件。",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="只使用普通 HTTP 抓取，不启用 Playwright 浏览器渲染兜底。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    urls = parse_url_inputs(args.urls)
    if not urls:
        print("没有解析到有效的有道云链接。", file=sys.stderr)
        return 2

    saved_paths: list[Path] = []
    failed: list[tuple[str, str]] = []
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] 下载: {url}")
        try:
            output_path = download_youdao_note(
                url,
                out_dir=Path(args.out_dir),
                use_browser=not args.no_browser,
                attachment_dir=Path(args.attachment_dir),
                overwrite=args.overwrite,
            )
            saved_paths.append(output_path)
            print(f"  已保存：{output_path}")
        except DownloadError as exc:
            failed.append((url, str(exc)))
            print(f"  失败：{exc}", file=sys.stderr)
        except requests.RequestException as exc:
            failed.append((url, f"网络请求失败：{exc}"))
            print(f"  网络请求失败：{exc}", file=sys.stderr)

    print()
    print(f"完成：成功 {len(saved_paths)} 篇，失败 {len(failed)} 篇。")
    if failed:
        print("失败链接：", file=sys.stderr)
        for url, reason in failed:
            print(f"- {url} -> {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

