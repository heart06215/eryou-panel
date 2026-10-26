"""设置、参数、命令、帮助等弹窗。"""
from __future__ import annotations

import os
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ProgressBar, RichLog, Select, Static

from ..adapters import make_adapter
from ..constants import APP_VERSION
from ..discovery import detect_kind, probe
from ..installer import InstallOutcome, InstallResult, install_name, install_project
from ..models import Project
from ..util import browse_folder, human_time, normalize_dir


class ProjectSettingsScreen(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"项目设置 · {self.project.game} / {self.project.name}", id="dialog_title")
            yield Label("安装目录（解压后的根目录，包含 ok-nte.exe 或 MAA.exe）", classes="dialog_label")
            with Horizontal():
                yield Input(value=self.project.directory, placeholder=r"例如 D:\Games\ok-nte", id="dir_input")
                yield Button("浏览…", id="browse")
            yield Label("可执行文件（可留空自动识别）", classes="dialog_label")
            yield Input(value=self.project.exe, placeholder="例如 ok-nte.exe", id="exe_input")
            yield Label("项目类型", classes="dialog_label")
            yield Select(
                [("ok-script 系（ok-nte / ok-ww）", "okscript"), ("MAA（明日方舟）", "maa"), ("通用 EXE", "generic")],
                value=self.project.kind,
                allow_blank=False,
                id="kind_input",
            )
            yield Label("GitHub 仓库（owner/repo）", classes="dialog_label")
            yield Input(value=self.project.repo, placeholder="owner/repo", id="repo_input")
            yield Static(
                "提示：也可以按 n 让面板自动扫描本机。目录只用于启动与读取日志，面板不会修改项目文件。",
                id="dialog_hint",
            )
            with Horizontal(id="dialog_buttons"):
                yield Button("自动探测", id="detect")
                yield Button("取消", id="cancel")
                yield Button("保存", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#dir_input", Input).focus()

    def _current_dir(self) -> str:
        return normalize_dir(self.query_one("#dir_input", Input).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse":
            folder = browse_folder("选择项目安装目录")
            if folder:
                self.query_one("#dir_input", Input).value = folder
            return
        if event.button.id == "detect":
            directory = self._current_dir()
            if not directory or not Path(directory).is_dir():
                self.app.notify("目录不存在", severity="error")
                return
            info = probe(self.project, directory)
            kind = info.get("kind") or detect_kind(directory)
            self.query_one("#kind_input", Select).value = kind
            self.query_one("#exe_input", Input).value = info.get("exe") or ""
            self.app.notify(f"探测结果：{kind} · {info.get('exe') or '未找到 EXE'}", timeout=4)
            return
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "save":
            directory = self._current_dir()
            if directory and not Path(directory).is_dir():
                self.app.notify("目录不存在：" + directory, severity="error")
                return
            if directory:
                info = probe(self.project, directory)
                exe = self.query_one("#exe_input", Input).value.strip() or info.get("exe") or ""
                kind = str(self.query_one("#kind_input", Select).value or "okscript")
                runner = "python" if (kind == "okscript" and not exe and
                                      (Path(directory) / "main.py").is_file()) else "exe"
                self.dismiss({
                    "directory": directory,
                    "exe": exe,
                    "runner": runner,
                    "kind": kind,
                    "repo": self.query_one("#repo_input", Input).value.strip(),
                })
            else:
                self.dismiss({"directory": "", "exe": "", "runner": "auto", "kind": self.project.kind,
                              "repo": self.query_one("#repo_input", Input).value.strip()})

    def action_cancel(self) -> None:
        self.dismiss(None)


class TaskArgScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, title: str, prompt: str, default: str = "") -> None:
        super().__init__()
        self._title = title
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title, id="dialog_title")
            yield Label(self._prompt or "参数（可留空）", classes="dialog_label")
            yield Input(value=self._default, id="arg_input")
            yield Static("留空则使用工具自身默认值。参数会按空格拆分后传给命令行。", id="dialog_hint")
            with Horizontal(id="dialog_buttons"):
                yield Button("取消", id="cancel")
                yield Button("运行", id="run", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(self.query_one("#arg_input", Input).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else self.query_one("#arg_input", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, project: Project, example: str = "") -> None:
        super().__init__()
        self.project = project
        self.example = example

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"命令行 · {self.project.game} / {self.project.name}", id="dialog_title")
            yield Label("在项目目录中通过系统 shell 执行，输出实时写入面板日志", classes="dialog_label")
            yield Input(
                placeholder=self.example or r"例如：ok-nte.exe -t src.tasks.FishingTask -h",
                id="command_input",
            )
            yield Static(
                "这是操控一切功能的入口：任何 CLI 参数、脚本、工具都可以在这里执行。\n"
                "危险命令会直接执行，请确认命令内容。",
                id="dialog_hint",
            )
            with Horizontal(id="dialog_buttons"):
                yield Button("取消", id="cancel")
                yield Button("执行", id="run", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = self.query_one("#command_input", Input).value.strip()
        if value:
            self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        value = self.query_one("#command_input", Input).value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭"), Binding("q", "close", "关闭")]

    def compose(self) -> ComposeResult:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold #b08d57", no_wrap=True)
        table.add_column(style="#3f3c36")
        rows = [
            ("s", "启动选中项目的图形界面"),
            ("x", "停止面板启动的进程，或结束该项目目录下的外部实例"),
            ("r", "重启（停止 → 等待退出 → 再次启动）"),
            ("c", "打开命令行，在项目目录执行任意命令"),
            ("i", "安装选中项目（固定安装到面板目录，自动下载最新 Release）"),
            ("I", "安装所有缺失项目"),
            ("p", "项目设置：安装目录、EXE、类型、仓库"),
            ("n", "自动扫描本机安装目录"),
            ("u", "强制同步 GitHub 信息（版本 / Star / Issues）"),
            ("o / g", "打开项目目录 / 打开 GitHub 仓库"),
            ("f", "日志自动跟随开关"),
            ("1 / 2 / 3", "快速切换项目"),
            ("t / l / e", "跳到任务 / 日志 / 配置标签页"),
            ("Enter", "任务页运行任务；配置页打开文件"),
            ("F5", "重新读取任务与日志"),
            ("? / q", "帮助 / 退出"),
        ]
        for key, desc in rows:
            table.add_row(key, desc)

        notes = Text()
        notes.append("\n能力说明\n", style="bold #2b2a26")
        notes.append(
            "· 启动与停止：直接托管项目 EXE（ok-nte / ok-ww / MAA），并监视其 CPU、内存与运行时长。\n"
            "· 无头任务：ok-script 项目使用 -t <任务> -h；MAA 使用 maa-cli 子命令。任务输出进入面板日志。\n"
            "· 任务清单：ok-script 从 src/config.py（或 config.py）的 onetime_tasks/trigger_tasks 读取；\n"
            "  MAA 使用内置预设 + 配置目录 tasks/ 下的自定义任务。\n"
            "· 全部功能：图形界面仍是最完整的入口；面板负责调度、监视与常用任务的一键执行。\n"
            "· 配置/日志都在项目自己的目录中，面板只读日志、不修改项目源码。\n\n",
            style="#6f6a5f",
        )
        notes.append("首次使用\n", style="bold #8a6d3b")
        notes.append(
            "· 按 i 自动下载并安装到面板目录下的同名文件夹（ok-nte / ok-ww-enhanced / MAA），\n"
            "  所有临时文件也都在该目录内，不写入其它位置。Shift+I 安装全部缺失项目。\n"
            "· 也可以手动从 Releases 下载解压，按 p 指定目录；按 n 自动扫描。\n"
            "· ok-script 项目通常需要管理员权限，并保持游戏 1920×1080（16:9）。\n"
            "· MAA 需要模拟器/ADB；无头任务需要额外安装 maa-cli 并放入 MAA 目录或 PATH。\n"
            "· 同一项目只允许一个实例：启动 GUI 后请先停止，再运行无头任务。\n\n",
            style="#6f6a5f",
        )
        notes.append("界面与玻璃效果\n", style="bold #8a6d3b")
        notes.append(
            "暖白 + 香槟金 + 圆角卡片；终端本身无法做真实背景模糊，面板用半透明色阶模拟毛玻璃。\n"
            "若使用 Windows Terminal，可在 设置 → 外观 中开启“亚克力材质”并把不透明度调到 85% 左右，\n"
            "再配合浅色主题，即可获得真实的透视模糊效果。\n\n",
            style="#6f6a5f",
        )
        notes.append("安全提示\n", style="bold #b07d2b")
        notes.append(
            "这些都是第三方游戏自动化工具，可能违反游戏服务条款并导致账号处罚。请自行评估风险；\n"
            "面板本身不包含、不修改任何游戏客户端。使用前请阅读各项目的免责声明。",
            style="#b07d2b",
        )

        with Vertical(id="dialog"):
            yield Static(f"二游面板 {APP_VERSION} · 帮助", id="dialog_title")
            with VerticalScroll(id="help_body"):
                yield Static(table)
                yield Static(notes)
            with Horizontal(id="dialog_buttons"):
                yield Button("关闭", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class MessageScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, title: str, body: str, confirm: str = "确定") -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._confirm = confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title, id="dialog_title")
            yield Static(self._body)
            with Horizontal(id="dialog_buttons"):
                yield Button("取消", id="cancel")
                yield Button(self._confirm, id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class InstallScreen(ModalScreen[InstallOutcome | None]):
    """下载并安装一个或多个项目；所有文件都落在固定安装根目录。"""

    BINDINGS = [Binding("escape", "close", "关闭")]

    def __init__(self, projects: list[Project], root, token: str = "", force: bool = False,
                 on_installed=None) -> None:
        super().__init__()
        self.projects = projects
        self.root = root
        self.token = token
        self.force = force
        self.on_installed = on_installed
        self._busy = False
        self._outcome = InstallOutcome()

    def compose(self) -> ComposeResult:
        title = "安装 · " + "、".join(p.game for p in self.projects)
        with Vertical(id="dialog"):
            yield Static(title, id="dialog_title")
            yield Static(id="install_path", markup=False)
            yield ProgressBar(total=100, show_eta=False, id="install_progress")
            yield Static("准备就绪", id="install_status", markup=False)
            yield RichLog(id="install_log", max_lines=400, wrap=True, markup=False)
            with Horizontal(id="dialog_buttons"):
                yield Button("开始安装", id="install_start", variant="primary")
                yield Button("关闭", id="install_close")

    def on_mount(self) -> None:
        lines = [f"安装根目录：{self.root}", "所有下载与解压都在该目录内完成，不写入其它位置。"]
        for project in self.projects:
            lines.append(f"{project.game} / {project.name} → {self.root / install_name(project)}")
        if self.force:
            lines.append("覆盖模式：旧目录会先备份，成功后删除备份。")
        self.query_one("#install_path", Static).update("\n".join(lines))
        self._log("等待开始。")
        self.query_one("#install_start", Button).focus()
        # 点击“安装”后直接开始，不需要再点一次
        self.set_timer(0.4, self._start)

    # ---- 按钮 -----------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install_start":
            self._start()
        elif event.button.id == "install_close":
            self.action_close()

    def action_close(self) -> None:
        if self._busy:
            self.app.notify("安装正在进行，请等待完成", severity="warning")
            return
        self.dismiss(self._outcome if (self._outcome.results or self._outcome.errors) else None)

    # ---- 安装流程 -------------------------------------------------
    def _start(self) -> None:
        if self._busy:
            return
        self._busy = True
        start = self.query_one("#install_start", Button)
        start.disabled = True
        self.query_one("#install_close", Button).disabled = True
        self.query_one("#install_log", RichLog).clear()
        self._log("开始安装…")
        self._run(self.projects, self.root, self.force)

    @work(thread=True, exclusive=True, group="install")
    def _run(self, projects: list[Project], root, force: bool) -> None:
        for project in projects:
            self._call(self._status, 0.0, f"正在安装 {project.game} / {project.name}…")
            try:
                result = install_project(
                    project, root, force=force, token=self.token,
                    progress=lambda pct, msg: self._call(self._progress, pct, msg),
                    log=lambda msg: self._call(self._log, msg),
                )
            except Exception as exc:
                self._call(self._failed, project, str(exc))
                break
            self._call(self._one_done, result)
        self._call(self._finished)

    def _call(self, callback, *args) -> None:
        try:
            self.app.call_from_thread(callback, *args)
        except Exception:
            pass

    def _progress(self, percent: float, message: str) -> None:
        try:
            self.query_one("#install_progress", ProgressBar).update(progress=percent)
            self.query_one("#install_status", Static).update(f"[{percent:5.1f}%] {message}")
        except Exception:
            pass

    def _status(self, percent: float, message: str) -> None:
        self._progress(percent, message)

    def _log(self, message: str) -> None:
        try:
            self.query_one("#install_log", RichLog).write(message)
        except Exception:
            pass

    def _one_done(self, result: InstallResult) -> None:
        self._outcome.results.append(result)
        self._log(f"完成：{result.project_name} {result.tag} → {result.path}")
        if self.on_installed is not None:
            try:
                self.on_installed(result)
            except Exception as exc:
                self._log(f"应用安装结果失败：{exc}")

    def _failed(self, project: Project, error: str) -> None:
        message = f"{project.game} / {project.name}：{error}"
        self._outcome.errors.append(message)
        self._log("错误：" + message)
        self._status(0.0, "安装失败，请查看日志")

    def _finished(self) -> None:
        self._busy = False
        try:
            self.query_one("#install_close", Button).disabled = False
            self.query_one("#install_close", Button).label = "关闭"
            if self._outcome.errors:
                self.query_one("#install_status", Static).update("安装结束，存在失败项；关闭后可重新安装")
                self.query_one("#install_start", Button).disabled = True
            else:
                self.query_one("#install_status", Static).update("全部安装完成")
                self.query_one("#install_start", Button).disabled = True
            self.query_one("#install_close", Button).focus()
        except Exception:
            pass
