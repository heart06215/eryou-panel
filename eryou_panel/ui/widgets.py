"""侧边栏项目卡片。"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import ListItem, Static

from ..models import Project, Runtime
from ..util import human_duration


class ProjectItem(ListItem):
    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self._label: Static | None = None

    def compose(self):
        yield Static(id="card")

    def on_mount(self) -> None:
        self._label = self.query_one(Static)
        self.refresh_from(None)

    def refresh_from(self, runtime: Runtime | None) -> None:
        if self._label is None:
            return
        project = self.project
        installed = bool(project.directory)
        status = runtime.status if runtime else "stopped"
        accent = project.accent or "#b08d57"

        head = Text()
        head.append(project.game, style="bold " + accent)
        head.append("  " + project.name, style="#8b8477")

        line2 = Text()
        if not installed:
            line2.append("○ 未指定目录", style="#b07d2b")
            self.add_class("missing")
        elif status == "running":
            line2.append("● 运行中", style="bold #3f7d58")
            if runtime and runtime.pid:
                line2.append(f"  PID {runtime.pid}", style="#8b8477")
            self.remove_class("missing")
        elif runtime and runtime.last_error and runtime.exit_code not in (None, 0):
            line2.append("● 已退出", style="#b4493f")
            line2.append(f"  码 {runtime.exit_code}", style="#8b8477")
            self.remove_class("missing")
        else:
            line2.append("● 已停止", style="#8b8477")
            self.remove_class("missing")

        line3 = Text()
        if runtime and status == "running":
            line3.append("CPU ", style="#a29a8c")
            line3.append(f"{runtime.cpu:4.1f}%", style="#6f6a5f")
            line3.append("   MEM ", style="#a29a8c")
            line3.append(f"{runtime.mem_mb:.0f} MB", style="#6f6a5f")
            line3.append("   " + human_duration(runtime.uptime()), style="#a29a8c")
        elif not installed:
            line3.append("按 p 设置安装目录", style="#a29a8c")
        else:
            line3.append(_short_dir(project.directory), style="#a29a8c")

        self._label.update(Text("\n").join([head, line2, line3]))


def _short_dir(path: str, limit: int = 31) -> str:
    """只显示末尾两级目录，避免侧边栏换行。"""
    from pathlib import Path

    try:
        parts = Path(path).parts
    except (OSError, ValueError):
        parts = (path,)
    if len(parts) >= 3:
        compact = str(Path(parts[-2]) / parts[-1])
    elif parts:
        compact = parts[-1]
    else:
        compact = path
    if len(compact) > limit:
        compact = "…" + compact[-(limit - 1):]
    return compact
