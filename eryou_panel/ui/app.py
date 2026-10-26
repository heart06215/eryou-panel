"""二游面板主界面。"""
from __future__ import annotations

import datetime as _dt
import os
import time
from pathlib import Path

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    ListView,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from ..adapters import Adapter, AdapterError, make_adapter
from ..config import Config
from ..constants import APP_NAME, APP_TAGLINE, APP_VERSION
from ..discovery import probe, scan
from ..installer import install_name
from ..models import GithubInfo, HistoryEntry, Project, Runtime, TaskItem
from ..services import GithubService, HistoryStore, LogTailer, ProcessService, safe_name
from ..tools import apply_scan_results
from ..util import (
    human_bytes,
    human_duration,
    open_path,
    open_url,
    short_time,
    strip_ansi,
    tail_lines,
)
from .modals import CommandScreen, HelpScreen, InstallScreen, MessageScreen, ProjectSettingsScreen, TaskArgScreen
from .style import STYLE
from .widgets import ProjectItem


def _iso_local(value: str) -> str:
    if not value:
        return "—"
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return value[:16]


def _clean_version(value: str) -> str:
    return value.strip().lstrip("vV").strip()


def _friendly_error(exc: Exception) -> str:
    if getattr(exc, "winerror", None) == 740 or "WinError 740" in str(exc):
        return "该项目需要管理员权限。请退出面板，右键 EryouPanel.exe → 以管理员身份运行，再试一次。"
    if isinstance(exc, FileNotFoundError):
        return "找不到可执行文件或解释器：" + str(exc)
    return f"{type(exc).__name__}: {exc}"


class EryouPanelApp(App[None]):
    CSS = STYLE
    TITLE = APP_NAME
    SUB_TITLE = APP_TAGLINE
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("s", "start", "启动"),
        Binding("x", "stop", "停止"),
        Binding("r", "restart", "重启"),
        Binding("c", "command", "命令"),
        Binding("i", "install", "安装"),
        Binding("I", "install_all", "全部安装", show=False),
        Binding("p", "settings", "设置"),
        Binding("n", "scan", "扫描"),
        Binding("u", "github", "同步"),
        Binding("o", "open_dir", "目录"),
        Binding("g", "open_repo", "仓库"),
        Binding("f", "follow", "跟随"),
        Binding("t", "tab_tasks", "任务", show=False),
        Binding("l", "tab_logs", "日志", show=False),
        Binding("e", "tab_config", "配置", show=False),
        Binding("1", "pick(0)", "异环", show=False),
        Binding("2", "pick(1)", "鸣潮", show=False),
        Binding("3", "pick(2)", "方舟", show=False),
        Binding("f5", "reload", "刷新", show=False),
        Binding("question_mark", "help", "帮助"),
        Binding("q", "quit_app", "退出"),
    ]

    def __init__(self, config: Config, auto_scan: bool = True) -> None:
        super().__init__()
        self.config = config
        self.auto_scan = auto_scan
        self.history = HistoryStore()
        self.service = ProcessService(self.history)
        self.github = GithubService(config.github_token, config.github_ttl)
        self.adapters: dict[str, Adapter] = {}
        self.runtimes: dict[str, Runtime] = {}
        self._items: dict[str, ProjectItem] = {}
        self._tasks: dict[str, TaskItem] = {}
        self._task_rows: dict[str, TaskItem] = {}
        self.selected_id = config.projects[0].id if config.projects else ""
        self.current_log = ""
        self.tailer = LogTailer()
        self._overview_sig: tuple = ()
        self._scanning = False
        self._scan_text = ""
        self._ui_ready = False
        self._install_dirty = False

        for project in config.projects:
            runtime = Runtime(project=project)
            self.runtimes[project.id] = runtime
            self.adapters[project.id] = make_adapter(project)

        self.service.on_exit = self._process_exited
        self.service.on_output = None

    # ---- 组合 -----------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="◆")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static(id="side_title")
                yield ListView(*[ProjectItem(p) for p in self.config.projects], id="projects")
                yield Static("↑↓ 选择 · Enter 任务 · ? 帮助", id="side_hint")
            with Vertical(id="content"):
                yield Static(id="proj_header")
                with TabbedContent(initial="tab-overview", id="tabs"):
                    with TabPane("概览", id="tab-overview"):
                        with VerticalScroll(id="overview_scroll"):
                            yield Static(id="overview")
                    with TabPane("任务", id="tab-tasks"):
                        yield Static("", id="task_hint")
                        yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True)
                    with TabPane("日志", id="tab-logs"):
                        with Horizontal(id="log_bar"):
                            yield Select(
                                [("（暂无日志）", "__none__")],
                                value="__none__",
                                allow_blank=True,
                                id="log_select",
                            )
                            yield Input(placeholder="过滤日志（子串）", id="log_filter")
                            yield Switch(True, id="log_follow")
                        yield RichLog(id="log_view", max_lines=4000, wrap=False, markup=False)
                    with TabPane("配置", id="tab-config"):
                        with Horizontal(id="cfg_bar"):
                            yield Button("打开配置目录", id="open_config")
                            yield Button("打开日志目录", id="open_logs")
                        yield DataTable(id="config_table", cursor_type="row", zebra_stripes=True)
                        yield Static(id="config_preview")
        yield Footer()

    def on_mount(self) -> None:
        for item in self.query(ProjectItem):
            self._items[item.project.id] = item
        self._load_all_tasks()
        self.query_one("#projects", ListView).index = 0
        self._ui_ready = True
        if self.selected_id:
            self.select_project(self.selected_id, force=True)
        self.query_one("#projects", ListView).focus()
        self.set_interval(self.config.poll_interval, self.tick)
        self._tick_once()
        if self.size.width < 96 or self.size.height < 28:
            self.notify("终端窗口偏小，建议放大到至少 110×32（可直接最大化）", timeout=8)
        if self.auto_scan and self.config.first_run and not any(p.directory for p in self.config.projects):
            self.set_timer(0.8, self.action_scan)

    # ---- 数据加载 -------------------------------------------------
    def _load_all_tasks(self) -> None:
        for project in self.config.projects:
            self._load_tasks(project.id)

    def _load_tasks(self, project_id: str) -> None:
        runtime = self.runtimes[project_id]
        adapter = self.adapters[project_id]
        runtime.version = adapter.version()
        try:
            runtime.tasks = adapter.tasks()
            runtime.tasks_error = ""
        except Exception as exc:
            runtime.tasks = []
            runtime.tasks_error = f"读取任务失败：{type(exc).__name__}: {exc}"
        runtime.tasks_loaded_at = time.time()

    def _rebind(self, project_id: str) -> None:
        project = self.config.get(project_id)
        if project is None:
            return
        runtime = self.runtimes[project_id]
        runtime.project = project
        runtime.reset_tasks()
        runtime.github = None
        runtime.github_error = ""
        self.adapters[project_id] = make_adapter(project)
        self._load_tasks(project_id)

    # ---- 选择项目 -------------------------------------------------
    def select_project(self, project_id: str, force: bool = False) -> None:
        if not project_id:
            return
        if project_id == self.selected_id and not force:
            return
        self.selected_id = project_id
        runtime = self.runtimes[project_id]
        runtime.history = self.history.recent(project_id, 6)
        self._render_header()
        self._render_overview(force=True)
        self._render_tasks()
        self._render_config()
        self._refresh_log_selector()
        self._request_github(project_id, force=False)

    # ---- 主循环 ---------------------------------------------------
    def tick(self) -> None:
        self._tick_once()

    def _tick_once(self) -> None:
        if not self._ui_ready:
            return
        try:
            side_title = self.query_one("#side_title", Static)
        except Exception:
            # 应用退出/测试拆除时控件可能已被卸载
            return
        snapshot = self.service.processes()
        running = 0
        for project in self.config.projects:
            runtime = self.runtimes[project.id]
            self.service.refresh(runtime, snapshot)
            item = self._items.get(project.id)
            if item is not None:
                item.refresh_from(runtime)
            if runtime.status == "running":
                running += 1
        total = len(self.config.projects)
        side = self._scan_text or f"{total} 个项目 · {running} 个运行中 · ↑↓ 切换"
        side_title.update(
            Text.assemble(("项目  ", "bold #6f6a5f"), (f"{total}", "bold #b08d57"),
                          ("    运行 ", "bold #8b8477"), (f"{running}", "bold #3f7d58"))
        )
        self.query_one("#side_hint", Static).update(Text(side, style="#a29a8c"))
        self.sub_title = f"{APP_TAGLINE} · {running}/{total} 运行中"

        if self.selected_id:
            self._render_header()
            self._render_overview()
            self._poll_logs()

    # ---- 渲染：标题 -------------------------------------------------
    def _selected(self) -> tuple[Project, Runtime, Adapter] | None:
        if not self.selected_id:
            return None
        return (
            self.config.get(self.selected_id) or self.config.projects[0],
            self.runtimes[self.selected_id],
            self.adapters[self.selected_id],
        )

    def _render_header(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        text = Text()
        text.append(project.game, style="bold " + (project.accent or "#b08d57"))
        text.append("  " + project.name, style="bold #2b2a26")
        text.append("    ")
        if runtime.status == "running":
            text.append("● 运行中", style="bold #3f7d58")
            text.append(f"  PID {runtime.pid}", style="#8b8477")
        elif not project.directory:
            text.append("○ 未指定目录", style="bold #b07d2b")
        else:
            text.append("● 已停止", style="#8b8477")
        text.append("    " + adapter.mode_text(), style="#a29a8c")
        if runtime.version:
            text.append("  v" + runtime.version, style="#a29a8c")
        self.query_one("#proj_header", Static).update(text)

    # ---- 渲染：概览 -------------------------------------------------
    def _render_overview(self, force: bool = False) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        github = runtime.github
        sig = (
            project.id, project.directory, runtime.status, runtime.pid,
            int(runtime.uptime() // 5), round(runtime.cpu), round(runtime.mem_mb),
            runtime.version, adapter.mode_text(), runtime.tasks_error,
            github.latest_tag if github else "", github.stars if github else 0,
            github.issues if github else 0, runtime.github_loading, runtime.github_error,
            tuple((round(h.ts), h.code) for h in runtime.history[-3:]),
            len(runtime.tasks),
        )
        if not force and sig == self._overview_sig:
            return
        self._overview_sig = sig

        accent = project.accent or "#b08d57"
        head = Text()
        head.append(project.game, style="bold " + accent)
        head.append(" · " + project.name, style="bold #2b2a26")
        head.append("\n")
        head.append(project.notes or "（暂无简介）", style="#6f6a5f")

        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(style="#8b8477", no_wrap=True, width=8)
        grid.add_column(overflow="fold")

        def row(label: str, value: Text | str) -> None:
            grid.add_row(label, value if isinstance(value, Text) else Text(str(value), style="#3f3c36"))

        if runtime.status == "running":
            status = Text("运行中", style="bold #3f7d58")
            status.append(f"   PID {runtime.pid}   已运行 {human_duration(runtime.uptime())}", style="#6f6a5f")
        elif not project.directory:
            status = Text("未指定安装目录", style="bold #b07d2b")
        else:
            status = Text("已停止", style="#8b8477")
            if runtime.last_action:
                status.append(f"   上次：{runtime.last_action}", style="#a29a8c")
        row("状态", status)
        row("资源", Text.assemble(
            ("CPU ", "#a29a8c"), (f"{runtime.cpu:.1f}%", "#3f3c36"), ("    ", ""),
            ("内存 ", "#a29a8c"), (f"{runtime.mem_mb:.0f} MB", "#3f3c36"), ("    ", ""),
            ("任务 ", "#a29a8c"), (runtime.active_task or "—", "#6f6a5f"),
        ))
        row("目录", Text(project.directory or "未设置（按 p 指定，或按 n 自动扫描）",
                        style="#3f3c36" if project.directory else "#b07d2b"))
        row("形态", Text.assemble(
            (adapter.mode_text(), "#3f3c36"), ("    ", ""),
            ("版本 ", "#a29a8c"), (runtime.version or "—", "#6f6a5f"), ("    ", ""),
            ("任务数 ", "#a29a8c"), (str(len(runtime.tasks)), "#6f6a5f"),
        ))

        repo_text = Text(project.repo or "—", style="#8a6d3b")
        if github is not None:
            repo_text.append(f"    ★ {github.stars}    Issues {github.issues}", style="#6f6a5f")
        row("仓库", repo_text)
        if runtime.github_loading and github is None:
            row("最新", Text("正在同步 GitHub…", style="#8b8477"))
        elif github is not None and github.latest_tag:
            latest = Text(github.latest_tag, style="bold #8a6d3b")
            latest.append("  " + _iso_local(github.latest_published_at), style="#8b8477")
            installed = _clean_version(runtime.version)
            if installed and _clean_version(github.latest_tag) != installed:
                latest.append("   可更新", style="bold #b07d2b")
            row("最新", latest)
        elif runtime.github_error:
            row("最新", Text("GitHub 同步失败（离线或限流）", style="#b07d2b"))
        elif project.repo:
            row("最新", Text("尚未同步（按 u 刷新）", style="#a29a8c"))

        action_grid = Table.grid(padding=(0, 2))
        action_grid.add_column(style="#8b8477", no_wrap=True, width=12)
        action_grid.add_column(style="#6f6a5f")
        action_grid.add_row(Text("[s] 启动 GUI"), Text("[x] 停止"))
        action_grid.add_row(Text("[c] 命令行"), Text("[r] 重启"))
        action_grid.add_row(Text("[p] 路径设置"), Text("[o] 打开目录"))

        history_grid = Table.grid(padding=(0, 2))
        history_grid.add_column(style="#a29a8c", no_wrap=True, width=8)
        history_grid.add_column(style="#6f6a5f")
        history_grid.add_column(style="#8b8477", no_wrap=True, width=18)
        if runtime.history:
            for entry in runtime.history[-5:]:
                result = ""
                if entry.code is not None:
                    result = f"退出码 {entry.code}"
                    if entry.duration:
                        result += " · " + human_duration(entry.duration)
                history_grid.add_row(short_time(entry.ts), entry.action, result)
        else:
            history_grid.add_row("—", "暂无运行记录", "")

        sections: list = [head, Text(""), grid]
        if not project.directory or not Path(project.directory).is_dir():
            target = self.config.install_root_path() / install_name(project)
            hint = Text()
            hint.append("\n未找到项目目录\n", style="bold #b07d2b")
            hint.append("1. 按 i 由面板自动下载并安装到：\n   " + str(target) + "\n", style="#6f6a5f")
            hint.append("2. 或手动从 GitHub Releases 下载解压，按 p 指定根目录；n 自动扫描\n", style="#6f6a5f")
            hint.append("3. 按 s 启动图形界面；Shift+I 可安装所有缺失项目\n", style="#6f6a5f")
            hint.append("安装根目录固定为 " + str(self.config.install_root_path()) + "，不写入其它位置。\n",
                        style="#a29a8c")
            if project.release_url:
                hint.append("下载页：" + project.release_url + "\n", style="#8a6d3b")
            hint.append("提示：ok-script 项目要求游戏 1920x1080（16:9）并通常需要管理员权限；"
                        "MAA 需要模拟器 / ADB 连接。", style="#a29a8c")
            sections.append(hint)
        elif runtime.tasks_error:
            sections.append(Text("\n" + runtime.tasks_error, style="#b4493f"))
        elif not runtime.tasks:
            sections.append(Text("\n未读取到任务清单。可在项目目录检查 src/config.py（ok-script）"
                                 "或配置目录 tasks/（MAA），也可以按 r 重新加载。", style="#b07d2b"))
        sections.extend([Text(""), Text("快捷操作", style="bold #2b2a26"), action_grid,
                         Text(""), Text("最近动作", style="bold #2b2a26"), history_grid])
        note = Text()
        note.append("\n同一项目只允许一个实例运行：启动 GUI 后再执行无头任务会被拒绝，请先停止。\n",
                    style="#a29a8c")
        note.append("面板只负责调度和监视，运行结果与账号风险由各项目及其使用者承担。",
                    style="#a29a8c")
        sections.append(note)
        self.query_one("#overview", Static).update(Group(*sections))

    # ---- 渲染：任务 -------------------------------------------------
    def _render_tasks(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        table = self.query_one("#tasks", DataTable)
        hint = self.query_one("#task_hint", Static)
        table.clear(columns=True)
        table.add_columns("任务", "类型", "说明")
        self._task_rows = {}
        if not adapter.available:
            hint.update(Text("目录未设置，无法读取任务。按 p 设置，或按 n 自动扫描。", style="#b07d2b"))
            return
        if runtime.tasks_error:
            hint.update(Text(runtime.tasks_error, style="#b4493f"))
            return
        if not runtime.tasks:
            hint.update(Text("未读取到任务。ok-script 项目检查 src/config.py；MAA 检查安装目录 / 配置目录。",
                             style="#b07d2b"))
            return
        for task in runtime.tasks:
            key = task.key
            self._task_rows[key] = task
            kind_text = {"onetime": "一次性", "trigger": "常驻", "preset": "MAA", "custom": "自定义"}.get(
                task.kind, task.kind)
            kind_style = {"trigger": "#b07d2b", "preset": "#3f7d58", "custom": "#7a5aa8"}.get(
                task.kind, "#6f6a5f")
            if task.kind == "trigger":
                detail = "常驻触发（GUI 运行时生效）"
            elif task.kind == "preset":
                detail = task.hint
            elif task.kind == "custom":
                detail = task.hint or "自定义任务"
            else:
                detail = "无头运行：" + task.key.rsplit(".", 1)[-1]
            table.add_row(
                Text(task.name, style="bold #2b2a26"),
                Text(kind_text, style=kind_style),
                Text(detail, style="#8b8477"),
                key=key,
            )
        runner = getattr(adapter, "runner", None)
        run_hint = ""
        if adapter.__class__.__name__ == "OkScriptAdapter":
            state = adapter.runner()  # type: ignore[attr-defined]
            if state == "missing":
                run_hint = "   ⚠ 未找到 EXE / Python，任务无法启动"
            elif state == "python":
                run_hint = "   以源码方式运行（Python）"
        if adapter.__class__.__name__ == "MaaAdapter" and not adapter.cli():  # type: ignore[attr-defined]
            run_hint = "   ⚠ 未找到 maa-cli，任务无法启动（GUI 仍可用）"
        hint.update(Text.assemble(
            ("Enter 运行选中任务", "bold #8a6d3b"),
            ("   ·   需要参数的任务会先弹窗询问   ·   输出进入日志标签页" + run_hint, "#8b8477"),
        ))

    # ---- 渲染：配置 -------------------------------------------------
    def _render_config(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        table = self.query_one("#config_table", DataTable)
        preview = self.query_one("#config_preview", Static)
        table.clear(columns=True)
        table.add_columns("文件", "大小", "修改时间")
        config_dir = adapter.config_dir()
        files: list[Path] = []
        if config_dir is not None:
            try:
                for path in sorted(config_dir.rglob("*")):
                    if path.is_file() and path.suffix.lower() in (
                            ".json", ".jsonc", ".py", ".toml", ".yaml", ".yml", ".ini", ".txt", ".log"):
                        files.append(path)
                    if len(files) >= 300:
                        break
            except OSError:
                pass
        if not files:
            preview.update(Text("未找到配置目录。项目首次运行后通常会生成 configs/ 或 config/；"
                                "也可以用 c 直接执行命令。", style="#8b8477"))
            return
        for path in files[:200]:
            try:
                stat = path.stat()
            except OSError:
                continue
            table.add_row(
                Text(str(path.relative_to(config_dir)), style="#3f3c36"),
                Text(human_bytes(stat.st_size), style="#8b8477"),
                Text(_dt.datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M"), style="#8b8477"),
                key=str(path),
            )
        self._preview_config(str(files[0]))

    def _preview_config(self, path: str) -> None:
        preview = self.query_one("#config_preview", Static)
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            preview.update(Text(f"无法读取：{exc}", style="#b4493f"))
            return
        lines = text.splitlines()[:18]
        body = Text()
        body.append(str(path) + "\n\n", style="bold #8a6d3b")
        body.append("\n".join(lines) or "（空文件）", style="#6f6a5f")
        if len(text.splitlines()) > 18:
            body.append("\n…", style="#a29a8c")
        preview.update(body)

    # ---- 日志 -----------------------------------------------------
    def _project_log_files(self) -> list[Path]:
        selected = self._selected()
        if selected is None:
            return []
        project, runtime, adapter = selected
        files: list[Path] = []
        try:
            files.extend(adapter.log_files()[:30])
        except OSError:
            pass
        prefix = safe_name(project.id) + "-"
        try:
            panel = [p for p in self.service.log_dir.glob(prefix + "*.log") if p.is_file()]
            panel.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            files.extend(panel[:30])
        except OSError:
            pass
        unique: dict[str, Path] = {}
        for path in files:
            unique[str(path)] = path
        ordered = sorted(unique.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return ordered

    def _refresh_log_selector(self) -> None:
        files = self._project_log_files()
        select = self.query_one("#log_select", Select)
        if files:
            options = []
            for path in files[:40]:
                try:
                    size = human_bytes(path.stat().st_size)
                    stamp = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M")
                except OSError:
                    size, stamp = "?", "?"
                options.append((f"{path.name}  ·  {size}  ·  {stamp}", str(path)))
        else:
            options = [("（暂无日志）", "__none__")]
        select.set_options(options)
        target = self.current_log if any(str(p) == self.current_log for p in files) else (
            str(files[0]) if files else "__none__")
        try:
            select.value = target
        except Exception:
            select.value = options[0][1]
        self._switch_log(target if target != "__none__" else "")

    def _switch_log(self, path: str) -> None:
        self.current_log = path
        view = self.query_one("#log_view", RichLog)
        view.clear()
        if not path:
            view.write(Text("暂无日志。启动项目或运行任务后，这里会实时显示输出。", style="#a29a8c"))
            self.tailer = LogTailer()
            return
        lines = self.tailer.reset(path, 300)
        self._write_log_lines(lines, clear=False)
        view.write(Text("── 跟随中 ──", style="#b8a06a"))

    def _write_log_lines(self, lines: list[str], clear: bool = False) -> None:
        view = self.query_one("#log_view", RichLog)
        if clear:
            view.clear()
        needle = self.query_one("#log_filter", Input).value.strip().lower()
        count = 0
        for line in lines:
            if needle and needle not in line.lower():
                continue
            view.write(strip_ansi(line).replace("\x00", ""))
            count += 1
            if count >= 400:
                break

    def _poll_logs(self) -> None:
        switch = self.query_one("#log_follow", Switch)
        if not switch.value or not self.current_log:
            return
        lines, reset = self.tailer.poll()
        if reset:
            self.query_one("#log_view", RichLog).clear()
        if lines:
            self._write_log_lines(lines)

    # ---- 启动 / 停止 ----------------------------------------------
    def _guard(self) -> bool:
        if isinstance(self.screen, ModalScreen):
            return False
        if isinstance(self.focused, Input):
            return False
        return True

    def action_start(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if runtime.status == "running":
            self.notify("项目已在运行，先按 x 停止", severity="warning")
            return
        try:
            self.service.start_gui(runtime, adapter)
        except AdapterError as exc:
            self.notify(str(exc), title="无法启动", severity="error")
            return
        except Exception as exc:
            self.notify(_friendly_error(exc), title="启动失败", severity="error")
            return
        self._after_start(runtime, "已启动 GUI")

    def _after_start(self, runtime: Runtime, message: str) -> None:
        self.select_project(runtime.project.id, force=True)
        self._render_overview(force=True)
        self.query_one("#tabs", TabbedContent).active = "tab-logs"
        self._refresh_log_selector()
        self.tick()
        self.notify(f"{runtime.project.game} · {message}", timeout=3)

    def action_stop(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        message = self.service.stop(runtime)
        self.tick()
        self.notify(f"{project.game}：{message}", timeout=3)

    def action_restart(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if runtime.status != "running":
            self.action_start()
            return
        self._restart_worker(project.id)

    @work(thread=True, exclusive=True, group="restart")
    def _restart_worker(self, project_id: str) -> None:
        runtime = self.runtimes[project_id]
        project = self.config.get(project_id) or runtime.project
        self.service.stop(runtime)
        deadline = time.time() + 12
        while time.time() < deadline:
            if not self.service.is_managed(project_id) and self.service.find_external(project) is None:
                break
            time.sleep(0.3)
        self.call_from_thread(self._restart_finish, project_id)

    def _restart_finish(self, project_id: str) -> None:
        selected = self._selected()
        if selected is None or selected[0].id != project_id:
            return
        project, runtime, adapter = selected
        try:
            self.service.start_gui(runtime, adapter)
        except Exception as exc:
            self.notify("重启失败：" + _friendly_error(exc), severity="error")
            return
        self._after_start(runtime, "已重新启动")

    # ---- 任务 -----------------------------------------------------
    def _run_task(self, key: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        task = self._task_rows.get(key) or self._tasks.get(key)
        if task is None:
            return
        if runtime.status == "running" and self.service.is_managed(project.id):
            self.notify("请先停止当前进程，再运行无头任务（同一项目只允许一个实例）", severity="warning")
            return
        if runtime.status == "running" and self.service.find_external(project) is not None:
            self.notify("检测到外部实例正在运行，请先关闭它", severity="warning")
            return
        if task.arg_prompt:
            self.push_screen(
                TaskArgScreen(f"运行任务 · {task.name}", task.arg_prompt, task.arg_default),
                lambda value: self._task_arg_done(task, value),
            )
            return
        self._start_task(task, "")

    def _task_arg_done(self, task: TaskItem, value: str | None) -> None:
        if value is None:
            return
        self._start_task(task, value)

    def _start_task(self, task: TaskItem, arg: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        try:
            self.service.start_task(runtime, adapter, task, arg)
        except AdapterError as exc:
            self.notify(str(exc), title="任务未启动", severity="error")
            return
        except Exception as exc:
            self.notify(_friendly_error(exc), title="任务未启动", severity="error")
            return
        self._after_start(runtime, f"任务已启动：{task.name}")

    # ---- 命令 / 设置 / 扫描 ----------------------------------------
    def action_command(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        example = ""
        if project.kind == "okscript":
            example = r"ok-nte.exe -t src.tasks.FishingTask.FishingTask -h"
            if adapter.exe_path():
                example = Path(adapter.exe_path()).name + " -t src.tasks.FishingTask.FishingTask -h"
        elif project.kind == "maa":
            example = "maa fight 1-7"
        self.push_screen(CommandScreen(project, example), self._command_done)

    def _command_done(self, command: str | None) -> None:
        if not command:
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        try:
            self.service.start_shell(runtime, adapter, command)
        except AdapterError as exc:
            self.notify(str(exc), severity="error")
            return
        except Exception as exc:
            self.notify(_friendly_error(exc), severity="error")
            return
        self._after_start(runtime, "命令已启动")

    def action_settings(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        self.push_screen(ProjectSettingsScreen(project), self._settings_done)

    def _settings_done(self, value: dict | None) -> None:
        if not value:
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        project.directory = value.get("directory", "")
        project.exe = value.get("exe", "")
        project.runner = value.get("runner", "auto")
        project.kind = value.get("kind", project.kind)
        project.repo = value.get("repo", project.repo)
        self.config.save()
        self._rebind(project.id)
        self.select_project(project.id, force=True)
        self._render_tasks()
        self._render_config()
        self._refresh_log_selector()
        self.tick()
        self.notify("设置已保存", timeout=3)

    # ---- 安装 -----------------------------------------------------
    def action_install(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if runtime.status == "running":
            self.notify("请先停止正在运行的项目，再安装/覆盖", severity="warning")
            return
        if project.directory and Path(project.directory).is_dir():
            self.push_screen(
                MessageScreen(
                    "覆盖安装",
                    f"{project.game} 已安装：\n{project.directory}\n\n"
                    "覆盖安装会先备份旧目录，成功后删除备份；配置会保留。",
                    "覆盖安装",
                ),
                lambda ok: self._open_install([project], force=True) if ok else None,
            )
            return
        self._open_install([project])

    def action_install_all(self) -> None:
        if not self._guard():
            return
        if any(runtime.status == "running" for runtime in self.runtimes.values()):
            self.notify("请先停止所有运行中的项目", severity="warning")
            return
        missing = [p for p in self.config.projects
                   if not (p.directory and Path(p.directory).is_dir())]
        if not missing:
            self.notify("三个项目都已安装", timeout=3)
            return
        self._open_install(missing)

    def _open_install(self, projects: list[Project], force: bool = False) -> None:
        root = self.config.install_root_path()
        self.push_screen(
            InstallScreen(projects, root, self.config.github_token, force,
                          on_installed=self._installed_one),
            self._install_closed,
        )

    def _installed_one(self, result) -> None:
        project = self.config.get(result.project_id)
        if project is None:
            return
        project.directory = result.path
        info = probe(project, result.path)
        project.kind = info.get("kind", project.kind)
        project.exe = info.get("exe", "")
        project.runner = info.get("runner", "exe")
        self._rebind(project.id)
        self.config.save()
        # 安装弹窗仍然覆盖在界面上，此时不要查询主界面控件；关闭弹窗后统一刷新
        self._install_dirty = True

    def _install_closed(self, outcome) -> None:
        if self._install_dirty:
            self._install_dirty = False
            if self.selected_id:
                self.select_project(self.selected_id, force=True)
            self._render_tasks()
            self._render_config()
            self._refresh_log_selector()
            self.tick()
        if outcome is not None:
            if outcome.results:
                self.notify("安装完成：" + "、".join(r.project_name for r in outcome.results), timeout=6)
            for error in outcome.errors:
                self.notify("安装失败：" + error, severity="error", timeout=12)
        self.tick()

    def action_scan(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self._scanning:
            self.notify("正在扫描中…", timeout=2)
            return
        self._scanning = True
        self._scan_text = "正在扫描本机安装目录…"
        self._scan_worker()

    @work(thread=True, exclusive=True, group="scan")
    def _scan_worker(self) -> None:
        try:
            found = scan(self.config.projects,
                         progress=lambda message: self.call_from_thread(self._scan_progress, message))
        except Exception as exc:
            self.call_from_thread(self._scan_failed, str(exc))
            return
        self.call_from_thread(self._scan_done, found)

    def _scan_progress(self, message: str) -> None:
        self._scan_text = message
        side = self.query_one("#side_hint", Static)
        side.update(Text(message, style="#b08d57"))

    def _scan_failed(self, error: str) -> None:
        self._scanning = False
        self._scan_text = ""
        self.notify("扫描失败：" + error, severity="error")

    def _scan_done(self, found: dict[str, str]) -> None:
        self._scanning = False
        self._scan_text = ""
        applied = apply_scan_results(self.config, found)
        for project_id in applied:
            self._rebind(project_id)
        if applied:
            self.select_project(self.selected_id, force=True)
            self._render_tasks()
            self._render_config()
            self._refresh_log_selector()
        self.tick()
        if applied:
            self.notify(f"已找到并应用 {len(applied)} 个项目目录", timeout=4)
        else:
            self.notify("未自动找到安装目录。请按 p 手动指定解压后的根目录。", timeout=6)

    # ---- GitHub ---------------------------------------------------
    def action_github(self) -> None:
        if not self._guard():
            return
        if self.selected_id:
            self._request_github(self.selected_id, force=True)
            self.notify("正在同步 GitHub…", timeout=2)

    def _request_github(self, project_id: str, force: bool = False) -> None:
        project = self.config.get(project_id)
        if project is None or not project.repo:
            return
        runtime = self.runtimes[project_id]
        cached = self.github.cached(project.repo)
        if cached:
            self._apply_github(project_id, cached)
        stale = (not cached) or (time.time() - float(cached.get("fetched_at", 0)) > self.config.github_ttl)
        if force or stale:
            if runtime.github is None:
                runtime.github_loading = True
                self._render_overview(force=True)
            self._github_worker(project_id, project.repo)

    @work(thread=True, exclusive=False, group="github")
    def _github_worker(self, project_id: str, repo: str) -> None:
        data = self.github.fetch(repo)
        self.call_from_thread(self._apply_github, project_id, data)

    def _apply_github(self, project_id: str, data: dict) -> None:
        runtime = self.runtimes.get(project_id)
        if runtime is None:
            return
        runtime.github_loading = False
        runtime.github_error = str(data.get("error", "") or "")
        if data.get("stars") is not None or data.get("latest_tag"):
            runtime.github = GithubInfo(
                description=str(data.get("description", "") or ""),
                stars=int(data.get("stars", 0) or 0),
                issues=int(data.get("issues", 0) or 0),
                pushed_at=str(data.get("pushed_at", "") or ""),
                latest_tag=str(data.get("latest_tag", "") or ""),
                latest_url=str(data.get("latest_url", "") or ""),
                latest_published_at=str(data.get("latest_published_at", "") or ""),
                homepage=str(data.get("homepage", "") or ""),
                fetched_at=float(data.get("fetched_at", time.time())),
            )
        if self.selected_id == project_id:
            self._render_overview(force=True)

    # ---- 打开 / 刷新 ----------------------------------------------
    def action_open_dir(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project = selected[0]
        if project.directory and Path(project.directory).is_dir():
            open_path(project.directory)
        else:
            self.notify("目录未设置", severity="warning")

    def action_open_repo(self) -> None:
        if not self._guard():
            return
        selected = self._selected()
        if selected is None:
            return
        project = selected[0]
        open_url(project.repo_url or project.docs_url)
        if not project.repo_url:
            self.notify("未配置仓库地址", severity="warning")

    def action_open_config(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        adapter = selected[2]
        config_dir = adapter.config_dir()
        if config_dir is None:
            self.notify("未找到配置目录（项目尚未运行过）", severity="warning")
            return
        open_path(str(config_dir))

    def action_open_logs(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        adapter = selected[2]
        files = adapter.log_files()
        if files:
            open_path(str(files[0].parent))
        elif selected[0].directory:
            open_path(selected[0].directory)
        else:
            self.notify("还没有日志", severity="warning")

    def action_follow(self) -> None:
        if not self._guard():
            return
        switch = self.query_one("#log_follow", Switch)
        switch.value = not switch.value

    def action_tab_tasks(self) -> None:
        if not self._guard():
            return
        self.query_one("#tabs", TabbedContent).active = "tab-tasks"
        self.query_one("#tasks", DataTable).focus()

    def action_tab_logs(self) -> None:
        if not self._guard():
            return
        self.query_one("#tabs", TabbedContent).active = "tab-logs"

    def action_tab_config(self) -> None:
        if not self._guard():
            return
        self.query_one("#tabs", TabbedContent).active = "tab-config"

    def action_pick(self, index: int) -> None:
        if not self._guard():
            return
        if 0 <= index < len(self.config.projects):
            list_view = self.query_one("#projects", ListView)
            list_view.index = index
            self.select_project(self.config.projects[index].id)

    def action_reload(self) -> None:
        if not self._guard():
            return
        if not self.selected_id:
            return
        self._load_tasks(self.selected_id)
        self._render_tasks()
        self._render_config()
        self._refresh_log_selector()
        self.notify("已重新加载任务与日志", timeout=2)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        if not self._guard():
            return
        self.exit()

    # ---- 事件 -----------------------------------------------------
    @on(ListView.Highlighted, "#projects")
    def _on_project_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, ProjectItem):
            self.select_project(item.project.id)

    @on(ListView.Selected, "#projects")
    def _on_project_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ProjectItem):
            self.select_project(item.project.id, force=True)
            self.query_one("#tabs", TabbedContent).active = "tab-tasks"
            self.query_one("#tasks", DataTable).focus()

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        table = event.data_table
        key = str(event.row_key.value)
        if table.id == "tasks":
            self._run_task(key)
        elif table.id == "config_table":
            open_path(key)

    @on(DataTable.RowHighlighted)
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "config_table":
            try:
                self._preview_config(str(event.row_key.value))
            except Exception:
                pass
            return
        if event.data_table.id == "tasks":
            self._show_task_command(str(event.row_key.value))

    def _show_task_command(self, key: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        task = self._task_rows.get(key)
        if task is None:
            return
        preview = self.service.command_preview(adapter, task)
        line = Text()
        line.append("Enter 运行", style="bold #8a6d3b")
        line.append("   ·   ", style="#a29a8c")
        line.append(preview, style="#6f6a5f")
        if task.kind == "trigger":
            line.append("   ·   常驻任务建议直接启动 GUI", style="#b07d2b")
        self.query_one("#task_hint", Static).update(line)

    @on(Select.Changed, "#log_select")
    def _on_log_selected(self, event: Select.Changed) -> None:
        value = str(event.value or "")
        if value and value != "__none__":
            self._switch_log(value)

    @on(Input.Changed, "#log_filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        if not self.current_log:
            return
        lines = tail_lines(self.current_log, 300)
        self._write_log_lines(lines, clear=True)

    @on(Input.Submitted, "#log_filter")
    def _on_filter_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#log_view", RichLog).focus()

    @on(Switch.Changed, "#log_follow")
    def _on_follow_changed(self, event: Switch.Changed) -> None:
        if event.value:
            self._poll_logs()

    @on(Button.Pressed, "#open_config")
    def _on_open_config(self, event: Button.Pressed) -> None:
        self.action_open_config()

    @on(Button.Pressed, "#open_logs")
    def _on_open_logs(self, event: Button.Pressed) -> None:
        self.action_open_logs()

    # ---- 进程退出回调（工作线程） ----------------------------------
    def _process_exited(self, runtime: Runtime, code: int, log_path: str) -> None:
        try:
            self.call_from_thread(self._exit_on_ui, runtime, code, log_path)
        except Exception:
            pass

    def _exit_on_ui(self, runtime: Runtime, code: int, log_path: str) -> None:
        name = f"{runtime.project.game} · {runtime.project.name}"
        if code == 0:
            self.notify(f"{name} 运行结束（退出码 0）", timeout=5)
        else:
            self.notify(f"{name} 异常退出，退出码 {code}，详见日志", severity="error", timeout=8)
        runtime.history = self.history.recent(runtime.project.id, 6)
        if self.selected_id == runtime.project.id:
            self._render_overview(force=True)
            self._render_tasks()
            self.tick()
