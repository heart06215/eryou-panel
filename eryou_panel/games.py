"""自动探测本机游戏与模拟器。

只读注册表/磁盘/进程，不写任何游戏文件。
- 异环：HTGame.exe，启动器 NTEGlobalGame.exe / NTEGame.exe / NTETWGame.exe
- 鸣潮：Wuthering Waves.exe / Client-Win64-Shipping.exe
- 明日方舟：检测 MuMu / 雷电 / BlueStacks / 夜神 / 逍遥 / Androws 模拟器
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import _registry_candidates, _walk
from .models import Project
from .winproc import list_processes

PC_SPECS: dict[str, dict] = {
    "ok-nte": {
        "tokens": ("neverness", "nte", "异环", "hotta"),
        "game": ("HTGame.exe",),
        "launchers": ("NTELauncher.exe", "NTEGlobalGame.exe", "NTEGame.exe",
                      "NTETWGame.exe", "NTEGlobalLauncher.exe"),
        "processes": ("HTGame.exe", "NTELauncher.exe", "NTEGlobalGame.exe",
                      "NTEGame.exe", "NTETWGame.exe"),
    },
    "ok-ww-enhanced": {
        "tokens": ("wuthering", "鸣潮", "kuro"),
        "game": ("Wuthering Waves.exe", "Client-Win64-Shipping.exe"),
        "launchers": ("Wuthering Waves.exe", "launcher.exe"),
        "processes": ("Wuthering Waves.exe", "Client-Win64-Shipping.exe", "launcher.exe"),
    },
    "better-genshin-impact": {
        "tokens": ("genshin", "原神", "yuanshen", "mihoyo", "hoyoverse"),
        "game": ("YuanShen.exe", "GenshinImpact.exe"),
        "launchers": ("launcher.exe", "YuanShen.exe", "GenshinImpact.exe"),
        "processes": ("YuanShen.exe", "GenshinImpact.exe", "launcher.exe"),
    },
    "march7th-assistant": {
        "tokens": ("star rail", "starrail", "崩坏：星穹铁道", "崩坏星穹铁道", "hoyoverse"),
        "game": ("StarRail.exe",),
        "launchers": ("launcher.exe", "StarRail.exe"),
        "processes": ("StarRail.exe", "launcher.exe"),
    },
    "honkai-helper": {
        "tokens": ("honkai impact", "崩坏3", "bh3"),
        "game": ("BH3.exe", "HonkaiImpact3.exe"),
        "launchers": ("BH3.exe", "HonkaiImpact3.exe", "launcher.exe"),
        "processes": ("BH3.exe", "HonkaiImpact3.exe", "launcher.exe"),
    },
    "ok-gf2": {
        "tokens": ("girls frontline", "girlsfrontline", "gfl2", "gf2", "少女前线2"),
        "game": ("GF2.exe", "GirlsFrontline2.exe"),
        "launchers": ("GF2.exe", "GirlsFrontline2.exe", "launcher.exe"),
        "processes": ("GF2.exe", "GirlsFrontline2.exe", "launcher.exe"),
    },
    "zenless-zone-zero-one-dragon": {
        "tokens": ("zenless", "绝区零", "zzz", "hoyoverse"),
        "game": ("ZenlessZoneZero.exe",),
        "launchers": ("launcher.exe", "ZenlessZoneZero.exe"),
        "processes": ("ZenlessZoneZero.exe", "launcher.exe"),
    },
}

# 启动器 ≠ 游戏本体：优先直接拉起真正的游戏进程，启动器作为回退。
# 常见安装路径：先查这些路径，避免每次都全盘扫描。
COMMON_PC_PATHS: dict[str, tuple[str, ...]] = {
    "better-genshin-impact": (
        r"Genshin Impact\Genshin Impact Game",
        r"Genshin Impact Game",
        r"Program Files\Genshin Impact\Genshin Impact Game",
    ),
    "march7th-assistant": (
        r"Star Rail\Game",
        r"Star Rail\Games",
        r"Star Rail",
        r"Program Files\Star Rail\Game",
    ),
    "honkai-helper": (
        r"Honkai Impact 3rd\Games",
        r"Honkai Impact 3rd",
        r"Program Files\Honkai Impact 3rd\Games",
    ),
    "ok-gf2": (
        r"Girls' Frontline 2",
        r"GirlsFrontline2",
        r"GFL2",
        r"Program Files\Girls' Frontline 2",
    ),
    "zenless-zone-zero-one-dragon": (
        r"ZenlessZoneZero Game",
        r"Zenless Zone Zero Game",
        r"Program Files\ZenlessZoneZero Game",
    ),
}

ACTUAL_GAME_PATHS: dict[str, tuple[str, ...]] = {
    "ok-nte": (
        r"Client\WindowsNoEditor\HT\Binaries\Win64\HTGame.exe",
    ),
    "ok-ww-enhanced": (
        r"Wuthering Waves Game\Client\Binaries\Win64\Client-Win64-Shipping.exe",
        r"Wuthering Waves Game\Wuthering Waves.exe",
    ),
}

def actual_game_exe(project: Project) -> str:
    """定位游戏本体 EXE；找不到时由调用方回退到启动器。"""
    paths = ACTUAL_GAME_PATHS.get(project.id, ())
    if not paths:
        return ""
    roots: list[Path] = []
    if project.game_dir:
        roots.append(Path(project.game_dir))
    if project.game_exe:
        roots.append(Path(project.game_exe).resolve().parent)
    for root in roots:
        bases = (root, root.parent, root.parent.parent)
        for base in bases:
            for relative in paths:
                candidate = base / relative
                if candidate.is_file():
                    return str(candidate)
    return ""


def game_workdir(project: Project, exe: str) -> str:
    """给游戏本体选择正确工作目录（鸣潮需要 Wuthering Waves Game）。"""
    if project.id == "ok-ww-enhanced":
        parts = Path(exe).parts
        for index, part in enumerate(parts):
            if part.lower() == "wuthering waves game":
                return str(Path(*parts[:index + 1]))
    if project.game_dir and Path(project.game_dir).is_dir():
        return project.game_dir
    return str(Path(exe).parent)


EMULATORS: list[tuple[str, str]] = [
    ("MuMuPlayer.exe", "MuMu 12"),
    ("MuMuPlayerGlobal.exe", "MuMu 12"),
    ("MuMuNxMain.exe", "MuMu 12"),
    ("MuMuManager.exe", "MuMu 12"),
    ("mumu-cli.exe", "MuMu 12"),
    ("NemuPlayer.exe", "MuMu 12"),
    ("dnplayer.exe", "雷电模拟器"),
    ("HD-Player.exe", "BlueStacks"),
    ("Nox.exe", "夜神模拟器"),
    ("MEmu.exe", "逍遥模拟器"),
    ("Androws.exe", "腾讯 Androws"),
]

EMULATOR_HINTS = (
    r"Netease\MuMu Player 12\nx_main\MuMuNxMain.exe",
    r"Netease\MuMu Player 12\nx_main\MuMuManager.exe",
    r"Netease\MuMu Player 12\shell\MuMuPlayer.exe",
    r"Netease\MuMuPlayer-12.0\shell\MuMuPlayer.exe",
    r"Netease\MuMuPlayer-12.0\shell\MuMuPlayerGlobal.exe",
    r"Netease\MuMuPlayer\shell\MuMuPlayer.exe",
    r"LDPlayer\LDPlayer9\dnplayer.exe",
    r"LDPlayer9\dnplayer.exe",
    r"BlueStacks_nxt\HD-Player.exe",
    r"Nox\bin\Nox.exe",
    r"Microvirt\MEmu\MEmu.exe",
    r"Androws\Androws.exe",
)



@dataclass
class GameHit:
    project_id: str
    kind: str                     # pc | emulator | maa
    launch_exe: str = ""
    game_dir: str = ""
    processes: list[str] = field(default_factory=list)
    running: bool = False
    detail: str = ""
    adb_path: str = ""

    @property
    def process_text(self) -> str:
        return ";".join(p for p in self.processes if p)


def detect_games(projects: list[Project], force: bool = False,
                 time_budget: float = 9.0) -> dict[str, GameHit | None]:
    processes = list_processes()
    running_names = {p.name.lower() for p in processes}
    deadline = time.time() + time_budget
    result: dict[str, GameHit | None] = {}
    for project in projects:
        try:
            if project.id in PC_SPECS:
                result[project.id] = _detect_pc(project, PC_SPECS[project.id], processes,
                                                running_names, force, deadline)
            elif project.kind == "maa":
                result[project.id] = _detect_maa(project, processes, running_names,
                                                 force, deadline)
            else:
                result[project.id] = None
        except Exception:
            result[project.id] = None
    return result


def _running_hit(project: Project, spec: dict, processes) -> GameHit | None:
    names = {n.lower() for n in spec.get("processes", ())}
    for proc in processes:
        if proc.name.lower() not in names:
            continue
        path = proc.path or ""
        directory = str(Path(path).parent) if path else ""
        game_dir, launch, processes_list = _resolve_paths(directory, spec) if directory else ("", path, [])
        return GameHit(
            project_id=project.id,
            kind="pc",
            launch_exe=launch or path,
            game_dir=game_dir or directory,
            processes=processes_list or list(spec.get("processes", ())),
            running=True,
            detail="运行中 · " + proc.name,
        )
    return None


def _cached_hit(project: Project, spec: dict, running_names: set[str]) -> GameHit | None:
    exe = project.game_exe
    if not exe or not Path(exe).is_file():
        return None
    names = [n for n in (project.game_process or "").split(";") if n]
    if not names:
        names = list(spec.get("processes", ()))
    running = any(n.lower() in running_names for n in names)
    return GameHit(
        project_id=project.id,
        kind=project.game_kind or "pc",
        launch_exe=exe,
        game_dir=project.game_dir or str(Path(exe).parent),
        processes=names,
        running=running,
        detail=project.game_note or ("运行中" if running else "已找到"),
        adb_path=project.game_adb,
    )


def _detect_pc(project: Project, spec: dict, processes, running_names: set[str],
               force: bool, deadline: float) -> GameHit | None:
    if not force:
        cached = _cached_hit(project, spec, running_names)
        if cached is not None:
            return cached

    running = _running_hit(project, spec, processes)
    if running is not None:
        return running

    # 先扫各磁盘的第一层目录，命中率最高、速度最快
    # 先查常见安装路径，再全盘扫描。
    for drive in "CDEFG":
        for relative in COMMON_PC_PATHS.get(project.id, ()):
            candidate = Path(f"{drive}:\\") / relative
            if candidate.is_dir():
                found = _find_in_dir(str(candidate), spec)
                if found:
                    return _hit(project, found, "常见路径")

    tokens = spec["tokens"]
    for drive in "CDEFG":
        root = Path(f"{drive}:\\")
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            if not any(token in child.name.lower() for token in tokens):
                continue
            found = _find_in_dir(str(child), spec)
            if found:
                return _hit(project, found, "磁盘目录")

    # 注册表卸载信息
    for display, directory in _registry_candidates():
        text = (display + " " + directory).lower()
        if not any(token in text for token in tokens):
            continue
        found = _find_in_dir(directory, spec)
        if found:
            return _hit(project, found, "注册表")

    # Steam 库
    for library in _steam_libraries():
        for candidate in _walk(str(library), max_depth=2):
            folder = Path(candidate).name.lower()
            if not any(token in folder for token in tokens):
                continue
            found = _find_in_dir(candidate, spec)
            if found:
                return _hit(project, found, "Steam")

    # 常见目录 / 磁盘
    for root in _game_roots():
        if time.time() > deadline:
            return None
        for directory, _depth in _walk(root, max_depth=4):
            if time.time() > deadline:
                return None
            folder = Path(directory).name.lower()
            if not any(token in folder for token in tokens):
                continue
            found = _find_in_dir(directory, spec)
            if found:
                return _hit(project, found, "磁盘扫描")
    return None


def _hit(project: Project, found: tuple[str, str, list[str]], source: str) -> GameHit:
    game_dir, launch, processes = found
    return GameHit(
        project_id=project.id,
        kind="pc",
        launch_exe=launch,
        game_dir=game_dir,
        processes=processes,
        running=False,
        detail=f"已找到 · {source}",
    )


def _resolve_paths(directory: str, spec: dict) -> tuple[str, str, list[str]]:
    """从任意层级的游戏目录向上寻找启动器，返回 (根目录, 启动 EXE, 进程名)。"""
    if not directory:
        return "", "", []
    current = Path(directory)
    for _ in range(4):
        for launcher in spec["launchers"]:
            candidate = current / launcher
            if candidate.is_file():
                return str(current), str(candidate), list(spec["processes"])
        current = current.parent
    return directory, "", list(spec["processes"])


def _find_in_dir(directory: str, spec: dict) -> tuple[str, str, list[str]] | None:
    """在目录及浅层子目录中找启动器/游戏 EXE。"""
    root = Path(directory)
    if not root.is_dir():
        return None
    names = {n.lower() for n in spec["game"]}
    launchers = {n.lower() for n in spec["launchers"]}
    found_game = ""
    found_dir = ""
    for current, _depth in _walk(str(root), max_depth=3):
        try:
            files = [entry.name for entry in os.scandir(current) if entry.is_file()]
        except OSError:
            continue
        lower = {f.lower(): f for f in files}
        for name in launchers:
            if name in lower:
                base = Path(current)
                return str(base), str(base / lower[name]), list(spec["processes"])
        if not found_game:
            for name in names:
                if name in lower:
                    found_game = str(Path(current) / lower[name])
                    found_dir = current
                    break
        if found_game:
            break
    if not found_game:
        return None
    game_dir, launch, processes = _resolve_paths(found_dir, spec)
    return game_dir or found_dir, launch or found_game, processes


def _game_roots() -> list[str]:
    home = Path.home()
    roots = [
        str(home / "Downloads"), str(home / "Desktop"), str(home / "Documents"),
        r"C:\Program Files", r"C:\Program Files (x86)",
        r"D:\Games", r"D:\Program Files", r"E:\Games", r"F:\Games",
    ]
    for letter in "CDEFG":
        drive = f"{letter}:\\"
        if Path(drive).is_dir():
            roots.append(drive)
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        if root and root not in seen and Path(root).is_dir():
            seen.add(root)
            unique.append(root)
    return unique


def _steam_libraries() -> list[Path]:
    paths: list[Path] = []
    try:
        import winreg

        for root, key_name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(root, key_name) as key:
                    steam = Path(str(winreg.QueryValueEx(key, "SteamPath")[0]))
            except OSError:
                continue
            if steam.is_dir():
                paths.append(steam)
                vdf = steam / "steamapps" / "libraryfolders.vdf"
                if vdf.is_file():
                    try:
                        text = vdf.read_text(encoding="utf-8", errors="replace")
                        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                            library = Path(match.group(1).replace("\\\\", "\\"))
                            if library.is_dir():
                                paths.append(library / "steamapps" / "common")
                    except OSError:
                        pass
                paths.append(steam / "steamapps" / "common")
    except Exception:
        pass
    return [p for p in dict.fromkeys(paths) if p.is_dir()]


def _adb_near(exe: str) -> str:
    if not exe:
        return ""
    path = Path(exe)
    for candidate in (path.parent / "adb.exe",
                      path.parent.parent / "shell" / "adb.exe",
                      path.parent.parent / "nx_main" / "adb.exe"):
        if candidate.is_file():
            return str(candidate)
    return ""


def _detect_maa(project: Project, processes, running_names: set[str],
                force: bool, deadline: float) -> GameHit | None:
    emulator_names = {name.lower() for name, _ in EMULATORS}
    for proc in processes:
        if proc.name.lower() not in emulator_names:
            continue
        label = next((label for name, label in EMULATORS if name.lower() == proc.name.lower()),
                     "模拟器")
        adb = _adb_near(proc.path)
        detail = f"运行中 · {label}" + (f" · ADB {adb}" if adb else "")
        return GameHit(
            project_id=project.id,
            kind="emulator",
            launch_exe=proc.path,
            game_dir=str(Path(proc.path).parent) if proc.path else "",
            processes=[proc.name],
            running=True,
            detail=detail,
            adb_path=adb,
        )

    if not force:
        cached = _cached_hit(project, {"processes": [n for n, _ in EMULATORS]}, running_names)
        if cached is not None:
            return cached

    candidates: list[Path] = []
    bases = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
             os.environ.get("LOCALAPPDATA", "")]
    for drive in "CDEFG":
        bases.append(f"{drive}:\\Program Files")
        bases.append(f"{drive}:\\Program Files (x86)")
    for base in dict.fromkeys(b for b in bases if b):
        for hint in EMULATOR_HINTS:
            candidates.append(Path(base) / hint)
    for path in candidates:
        if path.is_file():
            label = next((label for name, label in EMULATORS if name.lower() == path.name.lower()),
                         "模拟器")
            adb = _adb_near(str(path))
            detail = f"已找到 · {label}" + (f" · ADB {adb}" if adb else "")
            return GameHit(
                project_id=project.id,
                kind="emulator",
                launch_exe=str(path),
                game_dir=str(path.parent),
                processes=[path.name],
                running=False,
                detail=detail,
                adb_path=adb,
            )

    for root in _game_roots():
        if time.time() > deadline:
            break
        for directory, _depth in _walk(root, max_depth=4):
            if time.time() > deadline:
                break
            name = Path(directory).name.lower()
            if not any(key in name for key in ("mumu", "ldplayer", "bluestacks", "nox", "memu", "androws")):
                continue
            for current, _d in _walk(directory, max_depth=2):
                try:
                    items = list(Path(current).iterdir())
                except OSError:
                    continue
                for item in items:
                    if item.is_file() and item.name.lower() in emulator_names:
                        label = next((label for n, label in EMULATORS if n.lower() == item.name.lower()),
                                     "模拟器")
                        adb = _adb_near(str(item))
                        detail = f"已找到 · {label}" + (f" · ADB {adb}" if adb else "")
                        return GameHit(
                            project_id=project.id,
                            kind="emulator",
                            launch_exe=str(item),
                            game_dir=str(item.parent),
                            processes=[item.name],
                            running=False,
                            detail=detail,
                            adb_path=adb,
                        )

    # 没有模拟器时不回退到 MAA GUI
    return None


def apply_hit(project: Project, hit: GameHit | None) -> bool:
    """写入 Project；返回是否有变化。"""
    if hit is None:
        return False
    values = {
        "game_exe": hit.launch_exe,
        "game_dir": hit.game_dir,
        "game_kind": hit.kind,
        "game_process": hit.process_text,
        "game_adb": hit.adb_path,
        "game_note": hit.detail,
    }
    changed = False
    for key, value in values.items():
        if getattr(project, key) != value:
            setattr(project, key, value)
            changed = True
    return changed
