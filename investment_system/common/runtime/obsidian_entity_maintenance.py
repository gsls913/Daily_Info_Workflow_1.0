from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from investment_system.common.config.config_loader import get as cfg


ENTITY_COMPANY = "公司"
ENTITY_INDUSTRY = "行业"
ENTITY_STRATEGY = "策略"
CREATABLE_ENTITY_KINDS = {ENTITY_COMPANY, ENTITY_INDUSTRY}
DEFAULT_OBSIDIAN_EXE = r"D:\softwares\Obsidian\Obsidian.exe"
DEFAULT_SHORTCUT_STAGING_DIR = r"D:\桌面文件\系列功能动作\Obsidian笔记\新增待添加到utools"

INFO_COLLECTOR_COMPANY_SUBDIRS = ("公告", "纪要", "模型", "其他", "研报")
INFO_COLLECTOR_INDUSTRY_SUBDIRS = ("纪要", "模型", "其他", "研报")

INVESTMENT_LOGIC_TEMPLATE = """# 逻辑1：xxx



# 逻辑2：xxx



# 分红



# 资金面与诉求



# 业绩预测与估值测算
"""

INVESTMENT_INDUSTRY_INFO_TEMPLATE = """# 行业宏观分析



# 竞争格局分析



# 产业价值链分析



# 边际信息跟踪
"""

INVESTMENT_COMPANY_INFO_TEMPLATE = """# 公司基本信息



## 基础信息



## 历史沿革



## 股权结构



## 管理层



# 公司经营信息&数据



# 业务情况



## 业务结构与模式



## 业务1：xxx



## 业务2：xxx



# 业绩与财务情况



# 股价复盘



# 边际信息跟踪
"""

_INVESTMENT_DIR_RE = re.compile(r"^\{(公司|行业|策略)-(.+)\}$")
_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class EntityLocation:
    vault: str
    kind: str
    name: str
    path: Path
    parent: Path | None = None


@dataclass(frozen=True)
class EntityScanResult:
    name: str
    info_collector_root: Path
    investment_notes_root: Path
    info_collector_matches: tuple[EntityLocation, ...] = ()
    investment_notes_matches: tuple[EntityLocation, ...] = ()

    def info_collector_has_kind(self, kind: str) -> bool:
        return any(item.kind == kind for item in self.info_collector_matches)

    def investment_notes_has_kind(self, kind: str) -> bool:
        return any(item.kind == kind for item in self.investment_notes_matches)


@dataclass(frozen=True)
class CreateResult:
    root: Path
    created_dirs: tuple[Path, ...] = ()
    created_files: tuple[Path, ...] = ()
    skipped_existing: tuple[Path, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.created_dirs or self.created_files)


@dataclass(frozen=True)
class ShortcutSpec:
    path: Path
    target_path: str
    arguments: str
    working_directory: str = ""
    icon_location: str = ",0"
    description: str = ""
    window_style: int = 1


@dataclass(frozen=True)
class ShortcutCreateResult:
    created: tuple[Path, ...] = ()
    skipped_existing: tuple[Path, ...] = ()
    failed: tuple[tuple[Path, str], ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.created)


def normalize_entity_name(name: str) -> str:
    return (name or "").strip()


def validate_entity_name(name: str) -> str:
    normalized = normalize_entity_name(name)
    if not normalized:
        raise ValueError("名称不能为空")
    if _INVALID_NAME_RE.search(normalized):
        raise ValueError('名称不能包含 Windows 文件名非法字符: < > : " / \\ | ? *')
    if normalized in {".", ".."} or normalized.endswith("."):
        raise ValueError("名称不能为 .、..，也不能以英文句点结尾")
    return normalized


def info_collector_root() -> Path:
    return Path(str(cfg("paths.obsidian_base_dir", r"D:\path\to\Obsidian\Vault")))


def investment_notes_root(info_root: Path | None = None) -> Path:
    configured = cfg("paths.investment_notes_base_dir")
    if configured:
        return Path(str(configured))
    base = Path(info_root) if info_root else info_collector_root()
    return base.parent / "投资笔记"


def obsidian_exe_path() -> str:
    return str(cfg("paths.obsidian_exe_path", DEFAULT_OBSIDIAN_EXE))


def shortcut_staging_dir() -> Path:
    return Path(str(cfg("paths.obsidian_shortcut_staging_dir", DEFAULT_SHORTCUT_STAGING_DIR)))


def _info_kind_root(root: Path, kind: str) -> Path:
    if kind == ENTITY_COMPANY:
        return root / "B公司"
    if kind == ENTITY_INDUSTRY:
        return root / "B行业"
    raise ValueError(f"信息收集器不支持的类型: {kind}")


def list_investment_category_dirs(investment_root: Path | None = None) -> list[Path]:
    root = Path(investment_root) if investment_root else investment_notes_root()
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and path.name[:1] in {"A", "B", "C"}],
        key=lambda path: path.name.lower(),
    )


def _scan_info_collector(name: str, root: Path) -> tuple[EntityLocation, ...]:
    matches: list[EntityLocation] = []
    for kind in (ENTITY_COMPANY, ENTITY_INDUSTRY):
        path = _info_kind_root(root, kind) / name
        if path.is_dir():
            matches.append(EntityLocation("信息收集器", kind, name, path, path.parent))
    return tuple(matches)


def _scan_investment_notes(name: str, root: Path) -> tuple[EntityLocation, ...]:
    matches: list[EntityLocation] = []
    for category_dir in list_investment_category_dirs(root):
        try:
            children = [path for path in category_dir.iterdir() if path.is_dir()]
        except OSError:
            continue
        for child in children:
            match = _INVESTMENT_DIR_RE.match(child.name)
            if not match:
                continue
            kind, entity_name = match.groups()
            if entity_name == name:
                matches.append(EntityLocation("投资笔记", kind, entity_name, child, category_dir))
    return tuple(matches)


def scan_entity(
    name: str,
    info_root: Path | str | None = None,
    investment_root: Path | str | None = None,
) -> EntityScanResult:
    normalized = validate_entity_name(name)
    info_base = Path(info_root) if info_root else info_collector_root()
    investment_base = Path(investment_root) if investment_root else investment_notes_root(info_base)
    return EntityScanResult(
        name=normalized,
        info_collector_root=info_base,
        investment_notes_root=investment_base,
        info_collector_matches=_scan_info_collector(normalized, info_base),
        investment_notes_matches=_scan_investment_notes(normalized, investment_base),
    )


def _ensure_dir(path: Path, created: list[Path], skipped: list[Path]) -> None:
    if path.exists():
        skipped.append(path)
        return
    path.mkdir(parents=True, exist_ok=True)
    created.append(path)


def _ensure_file(path: Path, content: str, created: list[Path], skipped: list[Path]) -> None:
    if path.exists():
        skipped.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(path)


def create_info_collector_entity(
    name: str,
    kind: str,
    info_root: Path | str | None = None,
) -> CreateResult:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("信息收集器只支持创建公司或行业")

    root = Path(info_root) if info_root else info_collector_root()
    entity_dir = _info_kind_root(root, kind) / normalized
    subdirs = INFO_COLLECTOR_COMPANY_SUBDIRS if kind == ENTITY_COMPANY else INFO_COLLECTOR_INDUSTRY_SUBDIRS

    created_dirs: list[Path] = []
    created_files: list[Path] = []
    skipped: list[Path] = []

    _ensure_dir(entity_dir, created_dirs, skipped)
    for subdir in subdirs:
        _ensure_dir(entity_dir / subdir, created_dirs, skipped)
    _ensure_file(entity_dir / f"【{normalized}】.md", "", created_files, skipped)

    return CreateResult(entity_dir, tuple(created_dirs), tuple(created_files), tuple(skipped))


def investment_entity_dir_name(name: str, kind: str) -> str:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("投资笔记只支持创建公司或行业")
    return f"{{{kind}-{normalized}}}"


def investment_template_files(name: str, kind: str) -> dict[str, str]:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("投资笔记只支持创建公司或行业")
    info_template = INVESTMENT_COMPANY_INFO_TEMPLATE if kind == ENTITY_COMPANY else INVESTMENT_INDUSTRY_INFO_TEMPLATE
    return {
        f"{normalized}{{手写报告页}}.md": "",
        f"{normalized}{{投资逻辑页}}.md": INVESTMENT_LOGIC_TEMPLATE,
        f"{normalized}{{信息整理页}}.md": info_template,
    }


def create_investment_notes_entity(
    name: str,
    kind: str,
    category_dir: Path | str,
) -> CreateResult:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("投资笔记只支持创建公司或行业")

    category_path = Path(category_dir)
    entity_dir = category_path / investment_entity_dir_name(normalized, kind)

    created_dirs: list[Path] = []
    created_files: list[Path] = []
    skipped: list[Path] = []

    _ensure_dir(entity_dir, created_dirs, skipped)
    for filename, content in investment_template_files(normalized, kind).items():
        _ensure_file(entity_dir / filename, content, created_files, skipped)

    return CreateResult(entity_dir, tuple(created_dirs), tuple(created_files), tuple(skipped))


def info_collector_shortcut_specs(
    name: str,
    kind: str,
    shortcut_dir: Path | str | None = None,
    obsidian_exe: str | None = None,
) -> list[ShortcutSpec]:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("信息收集器快捷方式只支持公司或行业")
    root_name = "B公司" if kind == ENTITY_COMPANY else "B行业"
    target_dir = Path(shortcut_dir) if shortcut_dir else shortcut_staging_dir()
    return [
        ShortcutSpec(
            path=target_dir / f"{normalized}【信息搜集】.lnk",
            target_path=obsidian_exe or obsidian_exe_path(),
            arguments=f"obsidian://open?vault=信息收集器&file={root_name}\\{normalized}\\【{normalized}】.md",
        )
    ]


def investment_notes_shortcut_specs(
    name: str,
    kind: str,
    category_dir: Path | str,
    shortcut_dir: Path | str | None = None,
    obsidian_exe: str | None = None,
) -> list[ShortcutSpec]:
    normalized = validate_entity_name(name)
    if kind not in CREATABLE_ENTITY_KINDS:
        raise ValueError("投资笔记快捷方式只支持公司或行业")
    category_name = Path(category_dir).name
    entity_dir_name = investment_entity_dir_name(normalized, kind)
    target_dir = Path(shortcut_dir) if shortcut_dir else shortcut_staging_dir()
    exe = obsidian_exe or obsidian_exe_path()
    page_names = ("信息整理页", "手写报告页", "投资逻辑页")
    return [
        ShortcutSpec(
            path=target_dir / f"{normalized}{{{page_name}}}.lnk",
            target_path=exe,
            arguments=(
                "obsidian://open?vault=投资笔记&file="
                f"{category_name}/{entity_dir_name}/{normalized}{{{page_name}}}.md"
            ),
        )
        for page_name in page_names
    ]


def _ps_single_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_windows_shortcuts(specs: list[ShortcutSpec]) -> ShortcutCreateResult:
    created: list[Path] = []
    skipped: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for spec in specs:
        if spec.path.exists():
            skipped.append(spec.path)
            continue
        spec.path.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "$shell = New-Object -ComObject WScript.Shell\n"
            f"$shortcut = $shell.CreateShortcut({_ps_single_quote(spec.path)})\n"
            f"$shortcut.TargetPath = {_ps_single_quote(spec.target_path)}\n"
            f"$shortcut.Arguments = {_ps_single_quote(spec.arguments)}\n"
            f"$shortcut.WorkingDirectory = {_ps_single_quote(spec.working_directory)}\n"
            f"$shortcut.IconLocation = {_ps_single_quote(spec.icon_location)}\n"
            f"$shortcut.Description = {_ps_single_quote(spec.description)}\n"
            f"$shortcut.WindowStyle = {spec.window_style}\n"
            "$shortcut.Save()\n"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except Exception as exc:
            failed.append((spec.path, str(exc)))
            continue
        if completed.returncode == 0 and spec.path.exists():
            created.append(spec.path)
        else:
            message = (completed.stderr or completed.stdout or f"退出码 {completed.returncode}").strip()
            failed.append((spec.path, message))

    return ShortcutCreateResult(tuple(created), tuple(skipped), tuple(failed))

