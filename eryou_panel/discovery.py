"""本机项目探测：注册表 + 常见目录扫描。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterable

from .models import Project
from .util import normalize_dir

Progress = Callable[[str], None]

SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "winsxs", "system32",
    "syswow64", "node_modules", ".git", ".svn", "__pycache__", "venv", ".venv",
    "site-packages", "appdata", "programdata", "recovery", "perflogs",
    "installer", "assembly", "driverstore", "servicing", "softwaredistribution",
    "fixtures", "_research", "dist", "build",
}

# 每个项目用来识别安装目录的关键词（只匹配工具本身，不匹配游戏目录）
FOLDER_TOKENS: dict[str, tuple[str, ...]] = {
    "ok-nte": ("ok-nte", "oknte", "ok_nte"),
    "ok-ww-enhanced": ("ok-ww", "okww", "ok_ww"),
    "maa": ("maaassistant", "maa-assistant", "maa assistant", "maa"),
    "better-genshin-impact": ("bettergenshin", "better-genshin", "bettergi", "bgi"),
    "march7th-assistant": ("march7th", "march7thassistant", "三月七"),
    "azur-lane-auto-script": ("azurlane", "alas", "碧蓝航线"),
    "blue-archive-auto-script": ("bluearchive", "baas", "蔚蓝档案"),
    "onmyoji-auto-script": ("onmyoji", "oas", "阴阳师"),
    "honkai-helper": ("honkai", "dacapo", "崩坏3"),
    "ok-gf2": ("ok-gf2", "gf2", "少女前线2"),
    "zenless-zone-zero-one-dragon": ("onedragon", "zenlesszonezero", "绝区零", "zzz"),
    "nikke-auto-script": ("nikke", "nkas", "胜利女神"),
}

EXE_HINTS: dict[str, tuple[str, ...]] = {
    "ok-nte": ("ok-nte.exe",),
    "ok-ww-enhanced": ("ok-ww-e.exe", "ok-ww.exe", "okww.exe"),
    "maa": ("MAA.exe", "maa.exe"),
    "better-genshin-impact": ("BetterGI.exe",),
    "march7th-assistant": ("March7th Assistant.exe",),
    "azur-lane-auto-script": ("Alas.exe", "alas.exe"),
    "blue-archive-auto-script": ("BlueArchiveAutoScript.exe", "BAAS.exe"),
    "onmyoji-auto-script": ("OnmyojiAutoScript.exe", "OAS.exe"),
    "honkai-helper": ("DaCapo.exe",),
    "ok-gf2": ("ok-gf2.exe",),
    "zenless-zone-zero-one-dragon": ("OneDragon-Launcher.exe",),
    "nikke-auto-script": ("NikkeAutoScript.exe", "NKAS.exe"),
}

BAD_EXE_PARTS = ("unins", "uninstall", "update", "updater", "setup", "crash", "report")


def _lower(path: str) -> str:
    return path.replace("/", "\\").lower()


def find_exe(directory: str, hints: Iterable[str]) -> str:
    """在目录（含一级子目录）中查找主程序，返回相对路径。"""
    if not directory:
        return ""
    root = Path(directory)
    if not root.is_dir():
        return ""
    hints_lower = [h.lower() for h in hints]
    candidates: list[Path] = []
    folders = [root]
    try:
        folders.extend([p for p in root.iterdir() if p.is_dir()])
    except OSError:
        return ""
    for folder in folders[:40]:
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() == ".exe":
                    candidates.append(item)
        except OSError:
            continue
    for hint in hints_lower:
        for item in candidates:
            if item.name.lower() == hint:
                return str(item.relative_to(root))
    # 退而求其次：只接受名字与目录/项目相关、且不像安装器的 EXE
    folder_token = root.name.lower().replace("-", "").replace("_", "").replace(" ", "")
    for item in candidates:
        name = item.name.lower()
        stem = item.stem.lower().replace("-", "").replace("_", "").replace(" ", "")
        if any(bad in name for bad in BAD_EXE_PARTS):
            continue
        if stem and (stem in folder_token or folder_token in stem or "ok" in stem or "maa" in stem):
            return str(item.relative_to(root))
    return ""


def find_exe_any(directory: str) -> str:
    """通用项目兜底：优先根目录，再找一层目录中的可执行主程序。"""
    if not directory:
        return ""
    root = Path(directory)
    if not root.is_dir():
        return ""
    candidates: list[Path] = []
    for folder in [root] + [p for p in root.iterdir() if p.is_dir()][:40]:
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() == ".exe":
                    candidates.append(item)
        except OSError:
            continue
    for item in candidates:
        name = item.name.lower()
        if any(bad in name for bad in BAD_EXE_PARTS):
            continue
        if any(token in name for token in ("launcher", "start", "main", "game")):
            return str(item.relative_to(root))
    for item in candidates:
        if not any(bad in item.name.lower() for bad in BAD_EXE_PARTS):
            return str(item.relative_to(root))
    return ""


def find_exe_exact(directory: str, hints: Iterable[str]) -> str:
    """只按提示的文件名精确匹配（忽略大小写），不做启发式猜测。"""
    if not directory:
        return ""
    root = Path(directory)
    if not root.is_dir():
        return ""
    hints_lower = {h.lower() for h in hints}
    folders = [root]
    try:
        folders.extend([p for p in root.iterdir() if p.is_dir()])
    except OSError:
        return ""
    for folder in folders[:40]:
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() == ".exe" and item.name.lower() in hints_lower:
                    return str(item.relative_to(root))
        except OSError:
            continue
    return ""


def detect_kind(directory: str) -> str:
    if not directory:
        return "okscript"
    root = Path(directory)
    for name in ("src\\config.py", "config.py", "main.py", "pyproject.toml"):
        if (root / name).is_file():
            return "okscript"
    if (root / "MAA.exe").is_file() or (root / "maa.exe").is_file():
        return "maa"
    return "generic"


def probe(project: Project, directory: str) -> dict:
    """探测目录属于哪种运行形态，返回建议字段。"""
    directory = normalize_dir(directory)
    result = {"directory": directory, "kind": project.kind, "exe": "", "runner": "exe", "source_main": ""}
    if not directory or not Path(directory).is_dir():
        return result

    kind = project.kind
    if kind == "okscript":
        has_config = any((Path(directory) / rel).is_file() for rel in ("src\\config.py", "config.py"))
        if not has_config and (Path(directory) / "MAA.exe").is_file():
            kind = "maa"
    if kind == "maa":
        exe = find_exe(directory, ("MAA.exe", "maa.exe", "maa-cli.exe"))
        result.update(kind="maa", exe=exe, runner="exe")
        if not exe:
            result["runner"] = "cli"
        return result

    if kind == "okscript":
        exe = find_exe(directory, EXE_HINTS.get(project.id, ()))
        main = "main.py" if (Path(directory) / "main.py").is_file() else ""
        if exe:
            result.update(kind="okscript", exe=exe, runner="exe")
        elif main:
            result.update(kind="okscript", exe="", runner="python", source_main=main)
        else:
            result.update(kind="okscript", exe="", runner="exe")
        return result

    exe = find_exe(directory, EXE_HINTS.get(project.id, ()))
    result.update(kind="generic", exe=exe, runner="exe" if exe else "cli")
    return result


def _registry_candidates() -> list[tuple[str, str]]:
    """从卸载信息中提取 (显示名, 目录)。"""
    results: list[tuple[str, str]] = []
    try:
        import winreg
    except ImportError:
        return results
    roots = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, subkey in roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(min(count, 2000)):
                    try:
                        name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, name) as child:
                            display = str(_reg_value(child, "DisplayName", "")).strip()
                            if not display:
                                continue
                            install = str(_reg_value(child, "InstallLocation", "") or "").strip()
                            icon = str(_reg_value(child, "DisplayIcon", "") or "").strip()
                            uninstall = str(_reg_value(child, "UninstallString", "") or "").strip()
                            directory = install
                            for value in (icon, uninstall):
                                if not directory and value:
                                    cleaned = value.split(",")[0].strip().strip('"')
                                    if cleaned.lower().endswith(".exe"):
                                        directory = str(Path(cleaned).parent)
                            if directory:
                                results.append((display, directory))
                    except OSError:
                        continue
        except OSError:
            continue
    return results


def _reg_value(key, name: str, default):
    try:
        import winreg

        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _roots() -> list[str]:
    roots: list[str] = []
    home = Path(os.path.expanduser("~"))
    roots.extend([
        str(home / "Downloads"),
        str(home / "Desktop"),
        str(home / "Documents"),
        str(home / "OneDrive" / "Desktop"),
        str(home / "OneDrive" / "Downloads"),
        os.environ.get("LOCALAPPDATA", "") + r"\Programs",
        r"D:\code_project",
        r"C:\code_project",
        r"D:\Games",
        r"D:\Tools",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    ])
    appdata = os.environ.get("LOCALAPPDATA", "")
    roots.extend([
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\WinGet\Packages",
        str(home / "scoop" / "apps"),
        str(home / "scoop" / "persist"),
        appdata + r"\Programs",
    ])
    for letter in "DEFG":
        drive = f"{letter}:\\"
        if Path(drive).is_dir():
            roots.append(drive)
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        if not root or root in seen or not Path(root).is_dir():
            continue
        seen.add(root)
        unique.append(root)
    return unique


def _matches(project: Project, folder_name: str) -> bool:
    tokens = FOLDER_TOKENS.get(project.id, (project.name.lower(),))
    return any(token in folder_name for token in tokens)


def looks_like_install(project: Project, directory: str) -> bool:
    """必须有工具本体（EXE 或源码入口），避免把游戏目录当成工具目录。"""
    if not directory or not Path(directory).is_dir():
        return False
    if find_exe_exact(directory, EXE_HINTS.get(project.id, ())):
        return True
    if project.kind == "okscript":
        return ((Path(directory) / "main.py").is_file()
                and any((Path(directory) / rel).is_file() for rel in ("src/config.py", "config.py", "pyproject.toml")))
    if project.kind == "maa":
        return ((Path(directory) / "MAA.exe").is_file()
                or (Path(directory) / "maa-cli.exe").is_file())
    return False


def scan(projects: list[Project], progress: Progress | None = None,
         time_budget: float = 25.0) -> dict[str, str]:
    """扫描本机，返回 project_id -> 建议目录。"""

    def report(message: str) -> None:
        if progress:
            progress(message)

    found: dict[str, str] = {}
    pending = {p.id: p for p in projects}

    report("读取注册表安装信息…")
    registry = _registry_candidates()
    for project in list(pending.values()):
        tokens = FOLDER_TOKENS.get(project.id, (project.name.lower(),))
        for display, directory in registry:
            text = (display + " " + directory).lower()
            if any(token in text for token in tokens):
                if looks_like_install(project, directory):
                    found[project.id] = directory
                    pending.pop(project.id, None)
                    break

    deadline = time.time() + time_budget
    roots = _roots()
    for index, root in enumerate(roots, 1):
        if not pending:
            break
        report(f"扫描 {root}（{index}/{len(roots)}）")
        for directory, depth in _walk(root, max_depth=4):
            if time.time() > deadline:
                report("扫描超时，已保留找到的目录")
                return found
            name = Path(directory).name.lower()
            for project in list(pending.values()):
                if not _matches(project, name):
                    continue
                if looks_like_install(project, directory):
                    found[project.id] = directory
                    pending.pop(project.id, None)
                    report(f"找到 {project.game}：{directory}")
                    if not pending:
                        return found
    report(f"扫描完成，找到 {len(found)} 个目录")
    return found


def _walk(root: str, max_depth: int):
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name = entry.name.lower()
                    if name in SKIP_DIRS or name.startswith("."):
                        continue
                    if name.startswith("$"):
                        continue
                    stack.append((entry.path, depth + 1))
        except (OSError, PermissionError):
            continue
