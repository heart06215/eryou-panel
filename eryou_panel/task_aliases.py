"""从本地源码快照提取原 GUI 的实际任务名。

ok-nte / ok-ww 的 TaskCard 标题使用源码里的 self.name，可能与面板显示名不同
（例如“日常流程”对应“日常任务”，ok-ww 还带 emoji）。这里只读 sources/。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Project
from .util import app_dir, projects_dir

_CLASS_RE = re.compile(r"^class\s+(\w+)", re.M)
_NAME_RE = re.compile(r"^\s+self\.name\s*=\s*(['\"])(.*?)\1", re.M)

_FALLBACK = {
    "ok-nte": {
        "LauncherTask": "Start Game",
        "DailyRoutineTask": "日常任务",
        "OwnerSelectionTask": "店长特供",
        "WhirlwindTask": "自动小旋风",
        "VolleyballTask": "排球之星",
        "FishingTask": "自动钓鱼",
    },
    "ok-ww-enhanced": {
        "DailyTask": "📅 Daily Task",
        "DailyTask2": "⭐ Daily Task",
        "FarmEchoTask": "🌀 Farm 4C Echo in Dungeon/World",
        "FarmEchoTask2": "⭐ 4C Echo and Skill Advanced Material",
        "TacetTask": "Tacet Field",
        "TacetTask2": "Tacet Field",
        "ForgeryTask": "⚒️ Forgery Challenge",
        "ForgeryTask2": "⭐ Forgery Challenge",
        "SimulationTask": "Simulation Challenge",
        "SimulationTask2": "Simulation Challenge",
        "AutoCombatTask": "⚔️ Auto Combat",
        "AutoPickTask": "🌸 Auto Pick",
        "AutoLoginTask": "🔑 Auto Login",
        "FastTravelTask": "✈️ Fast Travel",
        "GardenTask": "🎡 自动周常乐园",
    },
}

_cache: dict[str, dict[str, str]] = {}


def _task_folders(project_id: str) -> list[Path]:
    root = projects_dir() / "sources"
    if project_id == "ok-nte":
        return [root / "ok-nte" / "src" / "tasks"]
    if project_id == "ok-ww-enhanced":
        return [root / "ok-ww-enhanced" / "src"]
    return []


def source_task_names(project_id: str) -> dict[str, str]:
    if project_id in _cache:
        return _cache[project_id]
    result: dict[str, str] = {}
    for folder in _task_folders(project_id):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matches = list(_CLASS_RE.finditer(text))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                block = text[match.end():end]
                name = _NAME_RE.search(block)
                if name:
                    result[match.group(1)] = name.group(2).strip()
    result.update(_FALLBACK.get(project_id, {}))
    _cache[project_id] = result
    return result


def task_candidates(project: Project, key: str, display_name: str) -> list[str]:
    class_name = key.rsplit(".", 1)[-1]
    names = [display_name, class_name]
    actual = source_task_names(project.id).get(class_name)
    if actual:
        names.insert(0, actual)
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique
