"""检查原项目 GitHub Release 版本，并判断是否需要更新。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .installer import InstallError, latest_release
from .models import Project


@dataclass
class UpdateInfo:
    project_id: str
    project_name: str
    latest_tag: str
    current: str
    available: bool
    error: str = ""


def installed_tag(project: Project) -> str:
    directory = Path(project.directory) if project.directory else None
    if directory is None:
        return ""
    marker = directory.parent / ".eryou-install" / f"{project.id}.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("tag", "") or "")


def normalize_tag(tag: str) -> str:
    value = (tag or "").strip().lower()
    if value.startswith("v"):
        value = value[1:]
    value = re.sub(r"\.0+$", "", value)
    return value


def _version_key(value: str) -> tuple:
    value = normalize_tag(value)
    if not value:
        return ()
    main, _, pre = value.partition("-")
    parts = []
    for item in main.split("."):
        match = re.match(r"\d+", item)
        parts.append(int(match.group()) if match else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts) + ((0, pre) if pre else (1, ""))


def is_newer(latest: str, current: str) -> bool:
    if not latest:
        return False
    if not current:
        return True
    left = _version_key(latest)
    right = _version_key(current)
    if left and right:
        return left > right
    return normalize_tag(latest) != normalize_tag(current)


def check_project_update(project: Project, current_version: str = "",
                         token: str = "") -> UpdateInfo:
    current = installed_tag(project) or current_version
    try:
        release = latest_release(project.repo, token)
        latest = str(release.get("tag_name", "") or "")
        return UpdateInfo(
            project_id=project.id, project_name=project.name,
            latest_tag=latest, current=current,
            available=is_newer(latest, current),
        )
    except (InstallError, OSError, ValueError) as exc:
        return UpdateInfo(
            project_id=project.id, project_name=project.name,
            latest_tag="", current=current, available=False, error=str(exc),
        )
