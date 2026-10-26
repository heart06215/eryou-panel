"""panel.json 的读写与默认值合并。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .constants import DEFAULT_PROJECTS
from .models import Project
from .util import app_dir, atomic_write, data_dir, load_json, normalize_dir, projects_dir


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "panel.json")
        self.first_run = not self.path.is_file()
        self.poll_interval = 1.0
        self.github_ttl = 900
        self.github_token = ""
        self.projects: list[Project] = []
        self.load()

    def load(self) -> None:
        raw = load_json(self.path)
        defaults = {p.id: copy.deepcopy(p) for p in DEFAULT_PROJECTS}
        merged: list[Project] = []
        for item in raw.get("projects", []) if isinstance(raw.get("projects"), list) else []:
            if not isinstance(item, dict):
                continue
            project = Project.from_dict(item)
            if not project.id:
                continue
            base = defaults.get(project.id)
            if base is not None:
                # 保留默认的说明/仓库/强调色，除非用户明确覆盖
                if not project.repo:
                    project.repo = base.repo
                if not project.game:
                    project.game = base.game
                if not project.name:
                    project.name = base.name
                if not project.notes:
                    project.notes = base.notes
                if not project.git_url:
                    project.git_url = base.git_url
                if not project.docs_url:
                    project.docs_url = base.docs_url
                if not project.platforms:
                    project.platforms = list(base.platforms)
                merged_extra = dict(base.extra)
                merged_extra.update(project.extra)
                project.extra = merged_extra
                defaults.pop(project.id, None)
            merged.append(project)

        # 文件里缺失的默认项目补齐（保持顺序：默认在前）
        by_id = {p.id: p for p in merged}
        ordered: list[Project] = []
        for default in DEFAULT_PROJECTS:
            if default.id in by_id:
                ordered.append(by_id.pop(default.id))
            else:
                ordered.append(copy.deepcopy(default))
        ordered.extend(by_id.values())
        self.projects = ordered

        try:
            self.poll_interval = float(raw.get("poll_interval", 1.0))
            self.poll_interval = min(5.0, max(0.5, self.poll_interval))
        except (TypeError, ValueError):
            self.poll_interval = 1.0
        try:
            self.github_ttl = int(raw.get("github_ttl", 900))
        except (TypeError, ValueError):
            self.github_ttl = 900
        self.github_token = str(raw.get("github_token", "") or "")
        self.install_root = str(raw.get("install_root", "") or "")
        self.ui = dict(raw.get("ui") or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "poll_interval": self.poll_interval,
            "github_ttl": self.github_ttl,
            "github_token": self.github_token,
            "install_root": self.install_root,
            "ui": dict(self.ui),
            "projects": [p.to_dict() for p in self.projects],
        }

    def install_root_path(self) -> Path:
        """安装根目录。默认 = 面板 EXE 所在目录；相对路径按该目录解析。"""
        raw = (self.install_root or "").strip()
        base = app_dir()
        if not raw:
            return projects_dir()
        path = Path(os.path.expandvars(raw)).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()

    def save(self) -> None:
        atomic_write(self.path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n")

    def get(self, project_id: str) -> Project | None:
        for project in self.projects:
            if project.id == project_id:
                return project
        return None

    def set_directory(self, project_id: str, directory: str) -> Project | None:
        project = self.get(project_id)
        if project is None:
            return None
        project.directory = normalize_dir(directory)
        project.exe = ""
        project.runner = "auto"
        self.save()
        return project

    def add_project(self, project: Project) -> None:
        self.projects.append(project)
        self.save()
