"""面板基础数据模型。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskItem:
    """一个可执行任务。"""

    key: str                 # 唯一标识：module.Class / MAA 子命令 / 自定义任务名
    name: str                # 中文显示名
    kind: str                # "onetime" | "trigger" | "preset" | "custom"
    command: list[str] = field(default_factory=list)
    hint: str = ""           # 说明或示例
    arg_prompt: str = ""     # 非空时运行前弹出参数输入框
    arg_default: str = ""


@dataclass
class GithubInfo:
    description: str = ""
    stars: int = 0
    issues: int = 0
    pushed_at: str = ""
    latest_tag: str = ""
    latest_url: str = ""
    latest_published_at: str = ""
    homepage: str = ""
    fetched_at: float = 0.0

    @property
    def age_text(self) -> str:
        if not self.fetched_at:
            return "未同步"
        seconds = max(0, int(time.time() - self.fetched_at))
        if seconds < 60:
            return "刚刚同步"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前同步"
        return f"{seconds // 3600} 小时前同步"


@dataclass
class HistoryEntry:
    ts: float
    project_id: str
    project_name: str
    action: str
    detail: str = ""
    code: int | None = None
    duration: float | None = None


@dataclass
class Project:
    id: str
    game: str
    name: str
    repo: str
    kind: str = "okscript"             # okscript | maa | generic
    directory: str = ""
    exe: str = ""                      # 相对目录的可执行文件名；留空自动识别
    runner: str = "auto"               # auto | exe | python
    notes: str = ""
    accent: str = "#b08d57"
    game_exe: str = ""
    game_dir: str = ""
    game_kind: str = ""
    game_process: str = ""
    game_adb: str = ""
    game_note: str = ""
    platforms: list[str] = field(default_factory=list)  # pc | android | macos | ios
    log_dirs: list[str] = field(default_factory=list)
    git_url: str = ""
    docs_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def platform_text(self) -> str:
        values = set(self.platforms or ["pc"])
        pc = "pc" in values
        android = "android" in values
        apple = "ios" in values or "macos" in values
        if not android and not apple:
            return "电脑端 · 仅电脑"
        if not pc:
            if android and not apple:
                return "手机端 · 仅安卓"
            if apple and not android:
                return "手机端 · 仅苹果"
            return "手机端 · 支持安卓/苹果"
        supported = []
        if android:
            supported.append("安卓")
        if apple:
            supported.append("苹果")
        return "电脑端 · 支持" + "/".join(supported)

    @property
    def mobile_only(self) -> bool:
        values = set(self.platforms or ["pc"])
        return "pc" not in values and ("android" in values or "ios" in values)

    @property
    def supports_bureau(self) -> tuple[bool, bool]:
        values = set(self.platforms or ["pc"])
        # 纯电脑端项目作为通用工具在两局显示；安卓专用/苹果专用按平台归属。
        pc_only = values == {"pc"}
        return (
            "android" in values or pc_only,
            "ios" in values or "macos" in values or pc_only,
        )

    @property
    def release_url(self) -> str:
        return f"https://github.com/{self.repo}/releases" if self.repo else ""

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}" if self.repo else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game": self.game,
            "name": self.name,
            "repo": self.repo,
            "kind": self.kind,
            "directory": self.directory,
            "exe": self.exe,
            "runner": self.runner,
            "notes": self.notes,
            "accent": self.accent,
            "game_exe": self.game_exe,
            "game_dir": self.game_dir,
            "game_kind": self.game_kind,
            "game_process": self.game_process,
            "game_adb": self.game_adb,
            "game_note": self.game_note,
            "platforms": list(self.platforms),
            "log_dirs": list(self.log_dirs),
            "git_url": self.git_url,
            "docs_url": self.docs_url,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Project":
        return cls(
            id=str(raw.get("id", "")).strip() or "project",
            game=str(raw.get("game", "")),
            name=str(raw.get("name", "")),
            repo=str(raw.get("repo", "")),
            kind=str(raw.get("kind", "okscript")),
            directory=str(raw.get("directory", "")),
            exe=str(raw.get("exe", "")),
            runner=str(raw.get("runner", "auto")),
            notes=str(raw.get("notes", "")),
            accent=str(raw.get("accent", "#b08d57")),
            game_exe=str(raw.get("game_exe", "")),
            game_dir=str(raw.get("game_dir", "")),
            game_kind=str(raw.get("game_kind", "")),
            game_process=str(raw.get("game_process", "")),
            game_adb=str(raw.get("game_adb", "")),
            game_note=str(raw.get("game_note", "")),
            platforms=[str(x) for x in raw.get("platforms", []) if isinstance(x, str)],
            log_dirs=[str(x) for x in raw.get("log_dirs", []) if isinstance(x, str)],
            git_url=str(raw.get("git_url", "")),
            docs_url=str(raw.get("docs_url", "")),
            extra=dict(raw.get("extra", {}) or {}),
        )


@dataclass
class Runtime:
    """运行期状态，不落盘。"""

    project: Project
    status: str = "stopped"          # stopped | running | starting
    pid: int | None = None
    started_at: float = 0.0
    cpu: float = 0.0
    mem_mb: float = 0.0
    exit_code: int | None = None
    last_error: str = ""
    last_action: str = ""
    last_action_at: float = 0.0
    tasks: list[TaskItem] = field(default_factory=list)
    tasks_error: str = ""
    tasks_loaded_at: float = 0.0
    github: GithubInfo | None = None
    github_error: str = ""
    github_loading: bool = False
    log_path: str = ""
    log_offset: int = 0
    log_lines: list[str] = field(default_factory=list)
    active_task: str = ""
    version: str = ""
    game_running: bool = False
    history: list[HistoryEntry] = field(default_factory=list)

    def reset_tasks(self) -> None:
        self.tasks = []
        self.tasks_error = ""
        self.tasks_loaded_at = 0.0

    def uptime(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, time.time() - self.started_at)
