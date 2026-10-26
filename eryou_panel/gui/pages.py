"""展开面板的五个页面：概览 / 任务 / 日志 / 配置 / 安装。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CheckBox,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SwitchButton,
    TransparentToolButton,
)

from ..adapters import Adapter
from ..models import GithubInfo, HistoryEntry, Project, Runtime, TaskItem
from ..updates import is_newer
from ..util import human_duration, human_time, open_path, open_url
from .theme import ERROR, FAINT, GOLD_DEEP, INK, MUTED, OK
from .widgets import Card, GlowProgressBar, Pill, SectionTitle, StatCard, icon_button


def _table(parent: QWidget, columns: list[str]) -> QTableWidget:
    table = QTableWidget(parent)
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.verticalHeader().setDefaultSectionSize(32)
    return table


class OverviewPage(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    command_requested = Signal()
    settings_requested = Signal()
    open_dir_requested = Signal()
    open_repo_requested = Signal()
    sync_requested = Signal()
    install_requested = Signal()
    logs_requested = Signal()
    launch_game_requested = Signal()
    launch_emulator_requested = Signal()
    find_game_requested = Signal()
    update_check_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_maa = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget(scroll)
        content.setObjectName("PageContent")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 10, 12)
        layout.setSpacing(12)

        # 标题与操作
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title = QLabel("—", self)
        self.title.setObjectName("PageTitle")
        self.name = QLabel("", self)
        self.name.setObjectName("ProjectName")
        self.status = Pill("未安装", self)
        head.addWidget(self.title)
        head.addWidget(self.name)
        head.addWidget(self.status)
        head.addStretch(1)
        self.start_btn = PrimaryPushButton(FluentIcon.PLAY, "运行任务", self)
        self.start_btn.setToolTip("打开任务页，选择要执行的任务")
        self.stop_btn = PushButton(FluentIcon.CANCEL, "停止", self)
        self.command_btn = icon_button(FluentIcon.COMMAND_PROMPT, "命令行", self)
        self.settings_btn = icon_button(FluentIcon.SETTING, "项目设置", self)
        self.dir_btn = icon_button(FluentIcon.FOLDER, "打开目录", self)
        self.update_btn = PushButton(FluentIcon.SYNC, "检查更新", self)
        for button in (self.start_btn, self.stop_btn,
                       self.command_btn, self.settings_btn, self.dir_btn,
                       self.update_btn):
            head.addWidget(button)
        self.update_btn.clicked.connect(self.update_check_requested.emit)
        self.command_btn.setVisible(False)
        self.settings_btn.setVisible(False)
        layout.addLayout(head)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.command_btn.clicked.connect(self.command_requested.emit)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.dir_btn.clicked.connect(self.open_dir_requested.emit)

        # 统计
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_cpu = StatCard("CPU", "—", self)
        self.stat_mem = StatCard("内存", "—", self)
        self.stat_uptime = StatCard("运行时长", "—", self)
        self.stat_tasks = StatCard("任务数", "—", self)
        for card in (self.stat_cpu, self.stat_mem, self.stat_uptime, self.stat_tasks):
            stats.addWidget(card, 1)
            card.setVisible(False)

        notes_card = Card(self)
        notes_layout = QVBoxLayout(notes_card)
        notes_layout.setContentsMargins(16, 12, 16, 12)
        notes_layout.setSpacing(6)
        notes_layout.addWidget(SectionTitle("项目说明", notes_card))
        self.notes = QLabel("", notes_card)
        self.notes.setObjectName("PathLabel")
        self.notes.setWordWrap(True)
        notes_layout.addWidget(self.notes)
        notes_card.setVisible(False)

        game_card = Card(self)
        self.game_card = game_card
        game_layout = QHBoxLayout(game_card)
        game_layout.setContentsMargins(16, 10, 16, 10)
        game_left = QVBoxLayout()
        game_left.setSpacing(2)
        game_left.addWidget(SectionTitle("对应游戏", game_card))
        self.game_path = QLabel("尚未查找", game_card)
        self.game_path.setObjectName("PathLabel")
        self.game_path.setWordWrap(True)
        self.game_detail = QLabel("", game_card)
        self.game_detail.setObjectName("TinyLabel")
        self.game_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        game_left.addWidget(self.game_path)
        game_left.addWidget(self.game_detail)
        self.game_pill = Pill("未找到", game_card)
        self.game_pill.set_state("warn", "未找到")
        self.launch_game_btn = PushButton(FluentIcon.PLAY, "启动游戏", game_card)
        self.find_game_btn = icon_button(FluentIcon.SYNC, "重新查找游戏", game_card)
        game_layout.addLayout(game_left, 1)
        game_layout.addWidget(self.game_pill)
        game_layout.addWidget(self.launch_game_btn)
        game_layout.addWidget(self.find_game_btn)
        layout.addWidget(game_card)
        self.launch_game_btn.setVisible(False)
        self.launch_game_btn.clicked.connect(self._launch_clicked)
        self.find_game_btn.clicked.connect(self.find_game_requested.emit)

        # 信息卡
        info_card = Card(self)
        info_layout = QGridLayout(info_card)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setHorizontalSpacing(18)
        info_layout.setVerticalSpacing(8)
        self.info: dict[str, QLabel] = {}
        rows = [("目录", "path"), ("形态", "mode"), ("仓库", "repo"),
                ("最新", "latest"), ("平台", "platform"), ("运行", "run"), ("任务", "task")]
        for index, (label, key) in enumerate(rows):
            caption = QLabel(label, info_card)
            caption.setObjectName("Metric")
            value = QLabel("—", info_card)
            value.setObjectName("PathLabel")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            info_layout.addWidget(caption, index // 2, (index % 2) * 2)
            info_layout.addWidget(value, index // 2, (index % 2) * 2 + 1)
            self.info[key] = value
        self.repo_link = PushButton(FluentIcon.LINK, "打开仓库", info_card)
        self.repo_link.clicked.connect(self.open_repo_requested.emit)
        self.sync_btn = PushButton(FluentIcon.SYNC, "同步 GitHub", info_card)
        self.sync_btn.clicked.connect(self.sync_requested.emit)
        self.sync_btn.setVisible(False)
        self.repo_link.setFixedWidth(132)
        self.repo_link.setFixedHeight(30)
        info_layout.addWidget(self.repo_link, 3, 2, Qt.AlignRight | Qt.AlignVCenter)
        info_layout.addWidget(self.sync_btn, 3, 3)
        layout.addWidget(info_card)

        # 最近动作
        history_card = Card(self)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(16, 12, 16, 12)
        history_layout.setSpacing(8)
        history_layout.addWidget(SectionTitle("最近动作", history_card))
        self.history = _table(history_card, ["时间", "动作", "结果"])
        self.history.setFixedHeight(132)
        self.history.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        history_layout.addWidget(self.history)
        self.logs_btn = PushButton(FluentIcon.DOCUMENT, "查看日志", history_card)
        self.logs_btn.clicked.connect(self.logs_requested.emit)
        history_layout.addWidget(self.logs_btn, 0, Qt.AlignRight)
        history_card.setVisible(False)

        # 未安装提示
        self.install_card = Card(self)
        install_layout = QHBoxLayout(self.install_card)
        install_layout.setContentsMargins(16, 12, 16, 12)
        self.install_hint = QLabel("", self.install_card)
        self.install_hint.setWordWrap(True)
        self.install_hint.setObjectName("Metric")
        self.install_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, "一键安装", self.install_card)
        self.install_btn.clicked.connect(self.install_requested.emit)
        install_layout.addWidget(self.install_hint, 1)
        install_layout.addWidget(self.install_btn)
        layout.addWidget(self.install_card)

        layout.addStretch(1)

    def _launch_clicked(self) -> None:
        self.launch_game_requested.emit()

    def set_project(self, project: Project, runtime: Runtime, adapter: Adapter,
                    github: GithubInfo | None, history: list[HistoryEntry]) -> None:
        self.title.setText(project.game)
        self.name.setText(project.name)
        if project.mobile_only:
            self.start_btn.setText("安装到 MuMu")
            self.start_btn.setToolTip("下载手机端 APK 并安装到 MuMu 模拟器")
        elif project.kind == "okscript":
            self.start_btn.setText("打开原项目")
            self.start_btn.setToolTip("启动原项目官方 GUI；任务在上游界面执行")
        else:
            self.start_btn.setText("打开原项目")
            self.start_btn.setToolTip("打开原项目；任务在原项目内执行")
        self._is_maa = project.kind == "maa"
        self.launch_game_btn.setText("打开 MAA" if self._is_maa else "打开原项目")
        if self._is_maa:
            self.launch_game_btn.setToolTip("打开 MAA 原项目；任务和设置都在 MAA 内操作")
        else:
            self.launch_game_btn.setToolTip("打开原项目；任务、配置和游戏启动都在原项目内操作")
        if project.mobile_only and project.extra.get("mobile_installed") and not project.directory:
            self.status.set_state("running", "已安装到 MuMu")
        elif not project.directory:
            self.status.set_state("stopped", "未安装")
        elif not getattr(runtime, "initialized", True):
            self.status.set_state("warn", "需初始化")
        elif runtime.status == "running":
            self.status.set_state("running", "运行中")
        elif runtime.exit_code not in (None, 0):
            self.status.set_state("error", f"退出码 {runtime.exit_code}")
        else:
            self.status.set_state("stopped", "已停止")

        self.stat_cpu.set_value(f"{runtime.cpu:.1f}%" if runtime.status == "running" else "—")
        self.stat_mem.set_value(f"{runtime.mem_mb:.0f} MB" if runtime.status == "running" else "—")
        self.stat_uptime.set_value(human_duration(runtime.uptime()) if runtime.status == "running" else "—")
        self.stat_tasks.set_value("原项目" if project.kind in ("okscript", "maa") else str(len(runtime.tasks)))

        self.info["path"].setText(project.directory or "未设置")
        self.info["path"].setToolTip(project.directory or "按 i 安装，或按 p 指定目录")
        self.info["mode"].setText(
            "手机端 · MuMu" if project.mobile_only else
            adapter.mode_text() + (f" · v{runtime.version}" if runtime.version else "")
        )
        self.info["platform"].setText(project.platform_text)
        self.game_card.setVisible(not project.mobile_only)
        repo_text = (f"https://github.com/{project.repo}" if project.repo else "—")
        if github is not None:
            repo_text += f"    ★ {github.stars}    Issues {github.issues}"
        self.info["repo"].setText(repo_text)
        self.notes.setText(project.notes or "（暂无说明）")

        target = project.game_exe
        if target and Path(target).is_file():
            kind = {"emulator": "模拟器", "maa": "MAA", "pc": "游戏"}.get(
                project.game_kind, "游戏")
            self.game_path.setText(f"{kind}：{target}")
            self.game_path.setToolTip(target)
            detail = project.game_note or ""
            if project.game_dir:
                detail += ("\n" if detail else "") + project.game_dir
            if project.game_adb:
                detail += f"\nADB：{project.game_adb}"
            self.game_detail.setText(detail)
            self.game_detail.setToolTip(detail)
            if runtime.game_running:
                self.game_pill.set_state("running", "运行中")
            else:
                self.game_pill.set_state("stopped", "已找到")
            self.launch_game_btn.setEnabled(True)
        else:
            self.game_path.setText("未找到本机游戏；点击右侧刷新，或按 Ctrl+G 重新查找")
            self.game_detail.setText(
                "异环：HTGame.exe / NTE 启动器  ·  鸣潮：Wuthering Waves.exe  ·  方舟：MuMu / 雷电 / BlueStacks 等")
            self.game_pill.set_state("warn", "未找到")
            self.launch_game_btn.setEnabled(False)
        self.info["run"].setText(
            f"PID {runtime.pid} · {runtime.active_task}" if runtime.status == "running" else "—")
        self.info["task"].setText("在原项目中操作" if project.kind in ("okscript", "maa") else (runtime.tasks_error or f"{len(runtime.tasks)} 个任务"))
        if github is not None:
            latest = github.latest_tag or "—"
            latest += "  " + github.latest_published_at[:10]
            self.info["latest"].setText(latest)
            self.info["latest"].setToolTip("Stars %d · Issues %d · 最近提交 %s" % (
                github.stars, github.issues, github.pushed_at[:10]))
        elif runtime.github_loading:
            self.info["latest"].setText("同步中…")
        elif runtime.github_error:
            self.info["latest"].setText("同步失败（离线 / 限流）")
        else:
            self.info["latest"].setText("尚未同步")

        self.history.setRowCount(len(history))
        for row, entry in enumerate(reversed(history[-6:])):
            self.history.setItem(row, 0, QTableWidgetItem(human_time(entry.ts)))
            self.history.setItem(row, 1, QTableWidgetItem(entry.action))
            result = "—"
            if entry.code is not None:
                result = f"退出码 {entry.code}"
                if entry.duration:
                    result += " · " + human_duration(entry.duration)
            self.history.setItem(row, 2, QTableWidgetItem(result))

        installed = bool(project.directory and Path(project.directory).is_dir())
        if project.mobile_only and project.extra.get("mobile_installed"):
            installed = True
        self.install_card.setVisible(not installed)
        if not installed:
            target = Path(project.directory) if project.directory else None
            if project.mobile_only:
                self.install_hint.setText("手机端项目：下载 APK 并安装到 MuMu 模拟器。")
                self.install_btn.setText("安装到 MuMu")
            else:
                self.install_hint.setText(
                    f"未安装。按 i / 点击右侧按钮会自动下载最新 Release 到固定目录，"
                    f"不会写入其它位置。" + (f"\n当前目标：{target}" if target else ""))
                self.install_btn.setText("一键安装")

    def update_live(self, runtime: Runtime) -> None:
        if not getattr(runtime, "initialized", True):
            self.status.set_state("warn", "需初始化")
        elif runtime.status == "running":
            self.stat_cpu.set_value(f"{runtime.cpu:.1f}%")
            self.stat_mem.set_value(f"{runtime.mem_mb:.0f} MB")
            self.stat_uptime.set_value(human_duration(runtime.uptime()))
        elif runtime.exit_code not in (None, 0):
            self.status.set_state("error", f"退出码 {runtime.exit_code}")
        else:
            self.status.set_state("stopped", "已停止")
        if runtime.project.game_exe:
            if runtime.game_running:
                self.game_pill.set_state("running", "运行中")
            else:
                self.game_pill.set_state("stopped", "已找到")


class TasksPage(QWidget):
    run_requested = Signal(object)          # list[TaskItem]
    refresh_requested = Signal()
    maa_settings_saved = Signal(str, str)
    maa_daily_requested = Signal(str, str, str, int, int, str)
    maa_launch_requested = Signal()
    maa_test_requested = Signal(str, str)
    maa_prepare_requested = Signal()
    config_requested = Signal(str)
    open_gui_requested = Signal()
    queue_gui_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 12, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("任务", self)
        title.setObjectName("PageTitle")
        self.hint = QLabel("", self)
        self.hint.setObjectName("PageHint")
        head.addWidget(title)
        head.addWidget(self.hint, 1)
        self.select_btn = PushButton("全选可运行", self)
        self.clear_btn = PushButton("清空", self)
        self.options_btn = PushButton(FluentIcon.SETTING, "任务选项", self)
        self.run_btn = PrimaryPushButton(FluentIcon.PLAY, "运行当前任务", self)
        self.refresh_btn = PushButton(FluentIcon.SYNC, "刷新", self)
        head.addWidget(self.select_btn)
        head.addWidget(self.clear_btn)
        head.addWidget(self.options_btn)
        head.addWidget(self.run_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        self.gui_card = Card(self)
        gui_layout = QHBoxLayout(self.gui_card)
        gui_layout.setContentsMargins(16, 10, 16, 10)
        gui_layout.setSpacing(10)
        gui_text = QVBoxLayout()
        gui_text.setSpacing(2)
        gui_text.addWidget(SectionTitle("原项目中操作", self.gui_card))
        self.gui_hint = QLabel(
            "任务、角色配置、日常、自动战斗等全部在原项目界面完成；"
            "面板只负责安装、更新、启动、监控和日志，不再复制原项目功能。",
            self.gui_card,
        )
        self.gui_hint.setObjectName("TinyLabel")
        self.gui_hint.setWordWrap(True)
        gui_text.addWidget(self.gui_hint)
        gui_layout.addLayout(gui_text, 1)
        self.gui_btn = PrimaryPushButton(FluentIcon.PLAY, "打开原项目界面", self.gui_card)
        self.gui_btn.clicked.connect(self.open_gui_requested.emit)
        gui_layout.addWidget(self.gui_btn)
        self.gui_card.setVisible(False)
        self.gui_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout.addWidget(self.gui_card)

        self.maa_card = Card(self)
        maa_layout = QVBoxLayout(self.maa_card)
        maa_layout.setContentsMargins(16, 10, 16, 10)
        maa_layout.setSpacing(6)
        maa_title = QHBoxLayout()
        maa_title.addWidget(SectionTitle("MAA 无头控制", self.maa_card))
        self.maa_hint = QLabel("一键日常会自动启动 MuMu、连接 ADB，不使用原 GUI", self.maa_card)
        self.maa_hint.setObjectName("TinyLabel")
        maa_title.addWidget(self.maa_hint, 1)
        self.maa_prepare = PushButton(FluentIcon.SYNC, "一键准备 MAA", self.maa_card)
        self.maa_prepare.setToolTip("自动安装/修复 MAA 与 maa-cli、生成连接配置、启动 MuMu 并等待 ADB")
        self.maa_prepare.clicked.connect(self.maa_prepare_requested.emit)
        maa_title.addWidget(self.maa_prepare)
        self.maa_launch = PushButton(FluentIcon.PLAY, "一键启动 MuMu", self.maa_card)
        self.maa_launch.setToolTip("启动 MuMu 并自动等待 ADB 就绪")
        self.maa_launch.clicked.connect(self.maa_launch_requested.emit)
        maa_title.addWidget(self.maa_launch)
        maa_layout.addLayout(maa_title)

        conn = QHBoxLayout()
        conn.setSpacing(8)
        conn.addWidget(QLabel("ADB", self.maa_card))
        self.maa_adb = QLineEdit(self.maa_card)
        self.maa_adb.setPlaceholderText("MuMu 自带的 adb.exe")
        self.maa_adb.setMinimumWidth(300)
        conn.addWidget(self.maa_adb, 2)
        conn.addWidget(QLabel("地址", self.maa_card))
        self.maa_address = QLineEdit(self.maa_card)
        self.maa_address.setPlaceholderText("127.0.0.1:16384")
        conn.addWidget(self.maa_address, 1)
        self.maa_save = PushButton("保存连接", self.maa_card)
        self.maa_save.clicked.connect(
            lambda: self.maa_settings_saved.emit(self.maa_adb.text().strip(),
                                                 self.maa_address.text().strip()))
        conn.addWidget(self.maa_save)
        self.maa_test = PushButton("测试连接", self.maa_card)
        self.maa_test.clicked.connect(
            lambda: self.maa_test_requested.emit(self.maa_adb.text().strip(),
                                                 self.maa_address.text().strip()))
        conn.addWidget(self.maa_test)
        maa_layout.addLayout(conn)

        daily = QHBoxLayout()
        daily.setSpacing(8)
        daily.addWidget(QLabel("一键日常", self.maa_card))
        self.maa_client = ComboBox(self.maa_card)
        self.maa_client.addItems(["Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"])
        self.maa_client.setFixedWidth(110)
        daily.addWidget(self.maa_client)
        daily.addWidget(QLabel("关卡", self.maa_card))
        self.maa_stage = QLineEdit("1-7", self.maa_card)
        self.maa_stage.setFixedWidth(100)
        daily.addWidget(self.maa_stage)
        daily.addWidget(QLabel("理智药", self.maa_card))
        self.maa_medicine = QSpinBox(self.maa_card)
        self.maa_medicine.setRange(0, 99)
        daily.addWidget(self.maa_medicine)
        daily.addWidget(QLabel("源石", self.maa_card))
        self.maa_stone = QSpinBox(self.maa_card)
        self.maa_stone.setRange(0, 99)
        daily.addWidget(self.maa_stone)
        self.maa_daily = PrimaryPushButton(FluentIcon.PLAY, "一键日常", self.maa_card)
        self.maa_daily.setToolTip("自动安装/配置 MAA、启动 MuMu、等待 ADB，然后执行日常")
        self.maa_daily.clicked.connect(
            lambda: self.maa_daily_requested.emit(
                self.maa_adb.text().strip(), self.maa_address.text().strip(),
                self.maa_stage.text().strip(), self.maa_medicine.value(),
                self.maa_stone.value(), self.maa_client.currentText()))
        daily.addWidget(self.maa_daily, 1)
        maa_layout.addLayout(daily)

        self.maa_status = QLabel("未准备 · 点击“一键日常”自动完成安装、连接和执行", self.maa_card)
        self.maa_status.setObjectName("TinyLabel")
        maa_layout.addWidget(self.maa_status)
        self.maa_progress = GlowProgressBar(self.maa_card)
        self.maa_progress.setValue(0)
        self.maa_progress.setFixedHeight(8)
        self.maa_progress.setVisible(False)
        maa_layout.addWidget(self.maa_progress)
        layout.addWidget(self.maa_card)

        self.table = _table(self, ["选", "任务", "类型", "说明"])
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 42)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.command_hint = QLabel("", self)
        self.command_hint.setObjectName("PathLabel")
        self.command_hint.setWordWrap(True)
        layout.addWidget(self.command_hint)

        self.tasks: list[TaskItem] = []
        self._maa_state = "idle"
        self._gui_mode = False
        self.select_btn.clicked.connect(self._select_all)
        self.clear_btn.clicked.connect(self._clear_all)
        self.options_btn.clicked.connect(self._open_options)
        self.run_btn.clicked.connect(self._run)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.table.itemActivated.connect(lambda _item: self._run_one())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._item_changed)

    def set_tasks(self, project: Project, runtime: Runtime, adapter: Adapter) -> None:
        self.tasks = list(runtime.tasks)
        self._gui_mode = False
        self.gui_card.setVisible(True)
        self.gui_btn.setText(
            "安装到 MuMu" if project.mobile_only else
            "打开 MAA" if project.kind == "maa" else "打开原项目界面"
        )
        self.gui_hint.setText(
            "手机端 APK：下载后安装到 MuMu 模拟器。"
            if project.mobile_only else
            "MAA 的任务、干员、关卡和设置都在 MAA 原项目内操作。"
            if project.kind == "maa" else
            "任务、角色配置、日常、自动战斗等全部在原项目界面完成。"
        )
        self.maa_card.setVisible(False)
        self.table.setVisible(False)
        self.select_btn.setVisible(False)
        self.clear_btn.setVisible(False)
        self.run_btn.setVisible(False)
        self.options_btn.setVisible(False)
        self.hint.setText(
            "  ·  " + adapter.mode_text()
            + (f"  ·  v{runtime.version}" if runtime.version else "")
        )
        self.command_hint.setText(
            "面板只负责安装、自动更新、启动和监控；具体任务请点“打开 MAA”或“打开原项目界面”后操作。"
        )

    def set_maa_busy(self, busy: bool) -> None:
        self._maa_busy = busy
        if busy:
            self._maa_state = "busy"
        for widget in (self.maa_prepare, self.maa_launch, self.maa_save,
                       self.maa_test, self.maa_daily):
            widget.setEnabled(not busy)
        if busy:
            self.maa_progress.setVisible(True)
            self.maa_progress.setValue(0)
            self.maa_status.setStyleSheet("")
            self.maa_status.setText("正在准备 MAA…")

    def set_maa_progress(self, value: float, message: str) -> None:
        self._maa_state = "busy"
        self.maa_progress.setVisible(True)
        self.maa_progress.setValue(int(max(0.0, min(100.0, value))))
        self.maa_status.setStyleSheet("")
        self.maa_status.setText(message)
        self.maa_status.setToolTip(message)

    def set_maa_ready(self, address: str = "") -> None:
        self._maa_state = "ready"
        self._maa_busy = False
        self.set_maa_busy(False)
        self.maa_progress.setVisible(True)
        self.maa_progress.setValue(100)
        self.maa_status.setStyleSheet("")
        self.maa_status.setText("MAA 已就绪" + (f" · {address}" if address else ""))
        self.maa_status.setToolTip(self.maa_status.text())

    def set_mumu_ready(self, address: str = "") -> None:
        self._maa_state = "ready"
        self._maa_busy = False
        self.set_maa_busy(False)
        self.maa_progress.setVisible(True)
        self.maa_progress.setValue(100)
        self.maa_status.setStyleSheet("")
        self.maa_status.setText("MuMu 已启动" + (f" · ADB {address}" if address else ""))
        self.maa_status.setToolTip(self.maa_status.text())

    def set_mumu_failed(self, message: str) -> None:
        self._maa_state = "failed"
        self._maa_busy = False
        self.set_maa_busy(False)
        self.maa_progress.setVisible(False)
        self.maa_progress.setValue(0)
        self.maa_status.setStyleSheet(f"color: {ERROR};")
        self.maa_status.setText("MuMu 启动失败 · 可重试")
        self.maa_status.setToolTip(message)

    def set_maa_failed(self, message: str) -> None:
        self._maa_state = "failed"
        self._maa_busy = False
        self.set_maa_busy(False)
        self.maa_progress.setVisible(False)
        self.maa_progress.setValue(0)
        self.maa_status.setStyleSheet(f"color: {ERROR};")
        self.maa_status.setText("MAA 准备失败 · 点“一键准备 MAA”重试")
        self.maa_status.setToolTip(message)

    def _checked_rows(self) -> list[int]:
        rows = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rows.append(row)
        return rows

    def selected_tasks(self) -> list[TaskItem]:
        rows = self._target_rows()
        return [self.tasks[row] for row in rows if 0 <= row < len(self.tasks)]

    def _target_rows(self) -> list[int]:
        rows = self._checked_rows()
        if not rows:
            rows = [index.row() for index in self.table.selectionModel().selectedRows()]
        if not rows:
            row = self.table.currentRow()
            if row >= 0:
                rows = [row]
        return sorted(set(rows))

    def _select_all(self) -> None:
        self.table.blockSignals(True)
        for row, task in enumerate(self.tasks):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked if task.kind != "trigger" else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_run_label()

    def _clear_all(self) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self._update_run_label()

    def _item_changed(self, _item) -> None:
        self._update_run_label()

    def _update_run_label(self) -> None:
        if self._gui_mode:
            count = len(self._target_rows())
            self.run_btn.setText(
                f"由原项目执行（{count}）" if count > 1 else "由原项目执行当前任务")
            return
        count = len(self._target_rows())
        if count > 1:
            self.run_btn.setText(f"运行选中任务（{count}）")
        elif count == 1:
            self.run_btn.setText("运行当前/选中任务")
        else:
            self.run_btn.setText("运行当前任务")

    def _selection_changed(self) -> None:
        self._update_run_label()
        row = self.table.currentRow()
        if self._gui_mode:
            if 0 <= row < len(self.tasks):
                task = self.tasks[row]
                self.command_hint.setText(
                    f"当前：{task.name}  ·  选中后由面板在原项目 GUI 中点击开始；"
                    f"原界面会最小化。")
            else:
                self.command_hint.setText(
                    "勾选任务后点“由原项目执行”；面板会最小化原界面并点击对应开始按钮。")
            return
        if 0 <= row < len(self.tasks):
            task = self.tasks[row]
            self.command_hint.setText(
                f"当前：{task.name}  ·  {task.hint or task.key}  ·  勾选或 Ctrl/Shift 多选可批量执行")
        else:
            self.command_hint.setText("勾选多个任务可批量执行；常驻任务不参与批量")

    def _run_one(self) -> None:
        if self._gui_mode:
            row = self.table.currentRow()
            if 0 <= row < len(self.tasks):
                self.queue_gui_requested.emit([self.tasks[row]])
            return
        row = self.table.currentRow()
        if 0 <= row < len(self.tasks):
            self.run_requested.emit([self.tasks[row]])

    def _run(self) -> None:
        if self._gui_mode:
            tasks = self.selected_tasks()
            if tasks:
                self.queue_gui_requested.emit(tasks)
            return
        tasks = self.selected_tasks()
        if tasks:
            self.run_requested.emit(tasks)

    def _open_options(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.tasks):
            self.config_requested.emit(self.tasks[row].key)


class LogsPage(QWidget):
    log_selected = Signal(str)
    filter_changed = Signal(str)
    follow_changed = Signal(bool)
    open_dir_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 12, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("日志", self)
        title.setObjectName("PageTitle")
        head.addWidget(title)
        self.combo = ComboBox(self)
        self.combo.setMinimumWidth(280)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("过滤日志…")
        self.follow = SwitchButton(self)
        self.follow.setText("跟随")
        self.follow.setOnText("开")
        self.follow.setOffText("关")
        self.follow.setChecked(True)
        self.open_btn = icon_button(FluentIcon.FOLDER, "打开日志目录", self)
        self.reload_btn = icon_button(FluentIcon.SYNC, "重新读取", self)
        head.addWidget(self.combo, 1)
        head.addWidget(self.filter, 1)
        head.addWidget(self.follow)
        head.addWidget(self.open_btn)
        head.addWidget(self.reload_btn)
        layout.addLayout(head)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.view, 1)
        self.status = QLabel("", self)
        self.status.setObjectName("PageHint")
        layout.addWidget(self.status)

        self.combo.currentIndexChanged.connect(self._combo_changed)
        self.filter.textChanged.connect(self.filter_changed.emit)
        self.follow.checkedChanged.connect(self.follow_changed.emit)
        self.open_btn.clicked.connect(self.open_dir_requested.emit)
        self.reload_btn.clicked.connect(self.refresh_requested.emit)

    def set_files(self, files: list[Path], current: str) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for path in files:
            try:
                size = path.stat().st_size
                label = f"{path.name}  ·  {size / 1024:.0f} KB"
            except OSError:
                label = path.name
            self.combo.addItem(label, str(path))
        if not files:
            self.combo.addItem("（暂无日志）", "")
        index = self.combo.findData(current)
        if index < 0 and self.combo.count():
            index = 0
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        if index >= 0 and self.combo.itemData(index):
            self.log_selected.emit(self.combo.itemData(index))

    def current_path(self) -> str:
        return self.combo.currentData() or ""

    def _combo_changed(self) -> None:
        path = self.current_path()
        if path:
            self.log_selected.emit(path)

    def clear(self) -> None:
        self.view.clear()

    def append_lines(self, lines: list[str], reset: bool = False) -> None:
        if reset:
            self.view.clear()
        needle = self.filter.text().strip().lower()
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        text = "\n".join(line for line in lines if not needle or needle in line.lower())
        if text:
            self.view.appendPlainText(text)
        if self.follow.isChecked() and at_bottom:
            self.view.moveCursor(QTextCursor.End)
            bar.setValue(bar.maximum())
        self.status.setText(f"{self.view.blockCount()} 行 · 跟随：{'开' if self.follow.isChecked() else '关'}")


class ConfigPage(QWidget):
    open_config_requested = Signal()
    open_logs_requested = Signal()
    open_source_requested = Signal()
    open_repo_requested = Signal()
    refresh_requested = Signal()
    save_requested = Signal(str, str)
    game_display_changed = Signal(str)
    game_background_changed = Signal(bool)
    display_refresh_requested = Signal()
    display_apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 12, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("显示", self)
        title.setObjectName("PageTitle")
        self.hint = QLabel("", self)
        self.hint.setObjectName("PageHint")
        head.addWidget(title)
        head.addWidget(self.hint, 1)
        self.config_btn = PushButton(FluentIcon.FOLDER, "配置目录", self)
        self.log_btn = PushButton(FluentIcon.DOCUMENT, "日志目录", self)
        self.source_btn = PushButton(FluentIcon.CODE, "源码目录", self)
        self.repo_btn = PushButton(FluentIcon.LINK, "原项目地址", self)
        self.save_btn = PrimaryPushButton(FluentIcon.SAVE, "保存当前文件", self)
        self.refresh_btn = icon_button(FluentIcon.SYNC, "刷新", self)
        head.addWidget(self.config_btn)
        head.addWidget(self.log_btn)
        head.addWidget(self.source_btn)
        head.addWidget(self.repo_btn)
        head.addWidget(self.save_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        self.display_card = Card(self)
        display_layout = QVBoxLayout(self.display_card)
        display_layout.setContentsMargins(16, 10, 16, 10)
        display_layout.setSpacing(6)
        display_head = QHBoxLayout()
        display_head.addWidget(SectionTitle("游戏启动显示", self.display_card))
        self.display_hint = QLabel("", self.display_card)
        self.display_hint.setObjectName("TinyLabel")
        display_head.addWidget(self.display_hint, 1)
        self.display_refresh = icon_button(FluentIcon.SYNC, "重新检测屏幕", self.display_card)
        display_head.addWidget(self.display_refresh)
        display_layout.addLayout(display_head)
        display_row = QHBoxLayout()
        display_row.setSpacing(8)
        display_row.addWidget(QLabel("启动到", self.display_card))
        self.display_combo = ComboBox(self.display_card)
        self.display_combo.setMinimumWidth(300)
        display_row.addWidget(self.display_combo, 1)
        self.background_check = CheckBox("启动后把游戏放到后台（最小化）", self.display_card)
        display_row.addWidget(self.background_check)
        self.display_apply = PushButton(FluentIcon.PLAY, "应用到当前游戏", self.display_card)
        display_row.addWidget(self.display_apply)
        display_layout.addLayout(display_row)
        layout.addWidget(self.display_card)
        self.display_empty = QLabel("当前项目不需要额外的显示设置，任务在原项目内操作。", self)
        self.display_empty.setObjectName("PageHint")
        self.display_empty.setVisible(False)
        layout.addWidget(self.display_empty)
        self._display_populating = False

        self.table = _table(self, ["文件", "大小", "修改时间"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 2)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(False)
        self.preview.setMaximumBlockCount(2000)
        layout.addWidget(self.preview, 3)
        self.files: list[Path] = []

        self.config_btn.clicked.connect(self.open_config_requested.emit)
        self.log_btn.clicked.connect(self.open_logs_requested.emit)
        self.source_btn.clicked.connect(self.open_source_requested.emit)
        self.repo_btn.clicked.connect(self.open_repo_requested.emit)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.save_btn.clicked.connect(self._save)
        self.table.itemSelectionChanged.connect(self._preview)
        self.table.itemDoubleClicked.connect(lambda _item: self.preview.setFocus())
        # 面板不再内置配置文件编辑器；配置页只保留显示设置。
        for widget in (self.hint, self.config_btn, self.log_btn, self.source_btn,
                       self.repo_btn, self.save_btn, self.refresh_btn,
                       self.table, self.preview):
            widget.setVisible(False)
        layout.removeWidget(self.table)
        layout.removeWidget(self.preview)
        layout.addStretch(1)
        self.display_refresh.clicked.connect(self.display_refresh_requested.emit)
        self.display_apply.clicked.connect(self.display_apply_requested.emit)
        self.display_combo.currentIndexChanged.connect(self._display_changed)
        self.background_check.toggled.connect(self._background_changed)

    def set_display_settings(self, project: Project, displays: list, key: str,
                             background: bool) -> None:
        visible = project.game_kind == "pc" and project.kind != "maa"
        self.display_card.setVisible(visible)
        self.display_empty.setVisible(not visible)
        self._display_populating = True
        self.display_combo.clear()
        selected = 0
        for index, display in enumerate(displays):
            self.display_combo.addItem(display.label, userData=display.device)
            if key and (key == display.device or key == display.monitor_id):
                selected = index
        if displays:
            self.display_combo.setCurrentIndex(selected)
        self.background_check.setChecked(bool(background))
        self.display_apply.setEnabled(bool(displays))
        self.display_hint.setText(
            f"检测到 {len(displays)} 个屏幕；只移动游戏窗口，不修改系统显示设置"
            if displays else "没有检测到屏幕")
        self._display_populating = False

    def current_display_key(self) -> str:
        data = self.display_combo.currentData()
        return str(data) if data else ""

    def background_enabled(self) -> bool:
        return self.background_check.isChecked()

    def _display_changed(self, _index: int) -> None:
        if self._display_populating:
            return
        key = self.current_display_key()
        if key:
            self.game_display_changed.emit(key)

    def _background_changed(self, checked: bool) -> None:
        if self._display_populating:
            return
        self.game_background_changed.emit(bool(checked))

    def current_path(self) -> Path | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.files):
            return self.files[row]
        return None

    def select_path(self, path: str | Path) -> bool:
        target = str(Path(path))
        for row, item in enumerate(self.files):
            if str(item).lower() == target.lower():
                self.table.selectRow(row)
                self._preview()
                return True
        return False

    def set_files(self, files: list[Path], base: Path | None) -> None:
        self.files = files
        self.table.setRowCount(len(files))
        for row, path in enumerate(files):
            try:
                stat = path.stat()
                size = stat.st_size / 1024
                stamp = human_time(stat.st_mtime)
            except OSError:
                size, stamp = 0, "—"
            name = str(path.relative_to(base)) if base else str(path)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(f"{size:.0f} KB"))
            self.table.setItem(row, 2, QTableWidgetItem(stamp))
        self.hint.setText(f"{len(files)} 个文件 · 可直接编辑并保存" if files else "未找到配置文件")
        if files:
            self.table.selectRow(0)
            self._preview()

    def _preview(self) -> None:
        path = self.current_path()
        if path is None:
            self.preview.clear()
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.preview.setPlainText(f"无法读取：{exc}")
            return
        self.preview.setPlainText(text)

    def _save(self) -> None:
        path = self.current_path()
        if path is not None:
            self.save_requested.emit(str(path), self.preview.toPlainText())


class InstallPage(QWidget):
    install_requested = Signal(object, bool)
    update_requested = Signal(object)
    update_check_requested = Signal()
    auto_update_changed = Signal(bool)
    mobile_install_requested = Signal(object)
    mumu_requested = Signal()
    apple_help_requested = Signal()
    open_gui_requested = Signal(object)
    mumu_site_requested = Signal()
    refresh_requested = Signal()
    source_requested = Signal()

    def __init__(self, projects: list[Project], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: dict[str, tuple[QLabel, QLabel, PushButton, ProgressBar]] = {}
        self._installing = False
        self._active_ids: set[str] = set()
        self._last_errors: dict[str, str] = {}
        self._updates: dict[str, str] = {}
        self._latest_ids: set[str] = set()
        self._needs_init: set[str] = set()
        self._projects = list(projects)
        self._row_cards = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 12, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("更新", self)
        title.setObjectName("PageTitle")
        self.root_label = QLabel("", self)
        self.root_label.setObjectName("PageHint")
        head.addWidget(title)
        head.addWidget(self.root_label, 1)
        self.all_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, "安装全部缺失", self)
        self.update_btn = PushButton(FluentIcon.SYNC, "检查更新", self)
        self.auto_update = CheckBox("自动更新原项目", self)
        self.auto_update.setToolTip("启动后自动检查 GitHub Release；有更新时保留配置并覆盖升级")
        self.refresh_btn = icon_button(FluentIcon.SYNC, "刷新", self)
        head.addWidget(self.update_btn)
        head.addWidget(self.auto_update)
        head.addWidget(self.all_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        overall = QHBoxLayout()
        overall.setSpacing(10)
        self.overall_label = QLabel("总进度 · 等待安装", self)
        self.overall_label.setObjectName("PageHint")
        self.overall_label.setMinimumWidth(190)
        self.overall_bar = GlowProgressBar(self)
        self.overall_bar.setValue(0)
        overall.addWidget(self.overall_label)
        overall.addWidget(self.overall_bar, 1)
        layout.addLayout(overall)

        self.mumu_card = Card(self)
        mumu_layout = QHBoxLayout(self.mumu_card)
        mumu_layout.setContentsMargins(16, 8, 16, 8)
        mumu_layout.setSpacing(8)
        mumu_layout.addWidget(SectionTitle("安卓总局 · MuMu", self.mumu_card))
        self.mumu_status = QLabel("未连接 MuMu", self.mumu_card)
        self.mumu_status.setObjectName("TinyLabel")
        mumu_layout.addWidget(self.mumu_status, 1)
        self.mumu_btn = PushButton(FluentIcon.SYNC, "连接 MuMu", self.mumu_card)
        self.mumu_btn.clicked.connect(self.mumu_requested.emit)
        mumu_layout.addWidget(self.mumu_btn)
        self.mumu_site = PushButton(FluentIcon.LINK, "MuMu 官网", self.mumu_card)
        self.mumu_site.clicked.connect(self.mumu_site_requested.emit)
        mumu_layout.addWidget(self.mumu_site)
        layout.addWidget(self.mumu_card)
        apple_row = QHBoxLayout()
        self.apple_notice = QLabel(
            "苹果总局：PC 工具可在此查看；iOS 设备安装需在 Apple 设备/PlayCover 完成，Windows 不能直接安装 IPA。",
            self,
        )
        self.apple_notice.setObjectName("PageHint")
        self.apple_notice.setWordWrap(True)
        apple_row.addWidget(self.apple_notice, 1)
        self.apple_help = PushButton(FluentIcon.LINK, "苹果安装说明", self)
        self.apple_help.clicked.connect(self.apple_help_requested.emit)
        apple_row.addWidget(self.apple_help)
        self.apple_notice.setVisible(False)
        self.apple_help.setVisible(False)
        layout.addLayout(apple_row)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards_widget = QWidget()
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)
        self.cards_layout = cards_layout
        scroll.setWidget(cards_widget)
        layout.addWidget(scroll, 1)

        for project in projects:
            card = Card(self)
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            left = QVBoxLayout()
            left.setSpacing(4)
            game = QLabel(f"{project.game}  ·  {project.name}    {project.platform_text}", card)
            game.setObjectName("GameName")
            path = QLabel("", card)
            path.setObjectName("PathLabel")
            path.setWordWrap(True)
            status = QLabel("", card)
            status.setObjectName("Metric")
            bar = GlowProgressBar(card)
            bar.setValue(0)
            bar.setFixedHeight(8)
            left.addWidget(game)
            left.addWidget(path)
            left.addWidget(status)
            left.addWidget(bar)
            button = PushButton(
                FluentIcon.DOWNLOAD,
                "安装到 MuMu" if project.mobile_only else "安装",
                card,
            )
            button.clicked.connect(
                lambda _checked=False, p=project: self._project_button(p))
            row.addLayout(left, 1)
            row.addWidget(button)
            cards_layout.addWidget(card)
            self.rows[project.id] = (path, status, button, bar)
            self._row_cards[project.id] = card

        self.source_card = Card(self)
        source_layout = QVBoxLayout(self.source_card)
        source_layout.setContentsMargins(16, 10, 16, 10)
        source_layout.setSpacing(5)
        source_head = QHBoxLayout()
        source_head.addWidget(SectionTitle("源码下载", self.source_card))
        self.source_hint = QLabel("写入 sources/，只下载源码，不含图片/OCR 模型；不碰已安装项目", self.source_card)
        self.source_hint.setObjectName("TinyLabel")
        source_head.addWidget(self.source_hint, 1)
        self.source_btn = PrimaryPushButton(FluentIcon.CODE, "下载全部源码", self.source_card)
        self.source_btn.clicked.connect(self.source_requested.emit)
        source_head.addWidget(self.source_btn)
        source_layout.addLayout(source_head)
        self.source_status = QLabel("源码快照：等待下载", self.source_card)
        self.source_status.setObjectName("TinyLabel")
        source_layout.addWidget(self.source_status)
        self.source_bar = GlowProgressBar(self.source_card)
        self.source_bar.setValue(0)
        self.source_bar.setFixedHeight(8)
        source_layout.addWidget(self.source_bar)
        # 面板不再内置源码下载；安装页只保留原项目安装/更新。
        self.source_card.setVisible(False)
        cards_layout.addStretch(1)
        self.all_btn.clicked.connect(self._install_all)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.update_btn.clicked.connect(self.update_check_requested.emit)
        self.auto_update.toggled.connect(self.auto_update_changed.emit)

    def add_project(self, project: Project) -> None:
        if project.id in self.rows:
            return
        card = Card(self)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        left = QVBoxLayout()
        left.setSpacing(4)
        game = QLabel(f"{project.game}  ·  {project.name}    {project.platform_text}", card)
        game.setObjectName("GameName")
        path = QLabel("", card)
        path.setObjectName("PathLabel")
        path.setWordWrap(True)
        status = QLabel("", card)
        status.setObjectName("Metric")
        bar = GlowProgressBar(card)
        bar.setValue(0)
        bar.setFixedHeight(8)
        left.addWidget(game)
        left.addWidget(path)
        left.addWidget(status)
        left.addWidget(bar)
        button = PushButton(
            FluentIcon.DOWNLOAD,
            "安装到 MuMu" if project.mobile_only else "安装",
            card,
        )
        button.clicked.connect(lambda _checked=False, p=project: self._project_button(p))
        row.addLayout(left, 1)
        row.addWidget(button)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.rows[project.id] = (path, status, button, bar)
        self._row_cards[project.id] = card
        self._projects.append(project)

    def _install_all(self) -> None:
        if not self._installing:
            self.install_requested.emit(None, False)

    def _project_button(self, project: Project) -> None:
        if project.id in self._needs_init:
            self.open_gui_requested.emit(project)
        elif project.mobile_only:
            self.mobile_install_requested.emit(project)
        elif project.id in self._updates:
            self.update_requested.emit(project)
        else:
            self.install_requested.emit(project, False)

    def set_auto_update(self, enabled: bool) -> None:
        self.auto_update.blockSignals(True)
        self.auto_update.setChecked(bool(enabled))
        self.auto_update.blockSignals(False)

    def set_update_check_busy(self, busy: bool) -> None:
        self.update_btn.setEnabled(not busy)

    def set_update_available(self, project_id: str, latest_tag: str,
                             current: str = "") -> None:
        self._updates[project_id] = latest_tag
        self._latest_ids.discard(project_id)
        self._needs_init.discard(project_id)
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, button, bar = row
        status.setStyleSheet("")
        status.setText(f"可更新 {current or '?'} → {latest_tag}")
        status.setToolTip(f"最新版本 {latest_tag}；更新会保留用户配置")
        button.setText("更新")
        bar.setValue(100)

    def clear_update(self, project_id: str) -> None:
        self._updates.pop(project_id, None)

    def mark_latest(self, project_id: str) -> None:
        self._updates.pop(project_id, None)
        self._latest_ids.add(project_id)

    def show_latest(self) -> None:
        self.overall_bar.setValue(100)
        self.overall_label.setText("更新检查 · 已是最新，不更新")

    def set_bureau(self, bureau: str) -> None:
        android = bureau == "android"
        apple = bureau == "apple"
        self.mumu_card.setVisible(android)
        self.apple_notice.setVisible(apple)
        self.apple_help.setVisible(apple)
        for project in self._projects:
            row = self.rows.get(project.id)
            if row is None:
                continue
            visible = project.visible_in_bureau(bureau)
            card = self._row_cards.get(project.id)
            if card is not None:
                card.setVisible(visible)
            for widget in row:
                widget.setVisible(visible)

    def set_mumu_progress(self, value: float, message: str) -> None:
        self.mumu_status.setText(message)
        self.mumu_btn.setEnabled(False)

    def set_mumu_ready(self, address: str = "") -> None:
        self.mumu_status.setText("已连接" + (f" · {address}" if address else ""))
        self.mumu_btn.setEnabled(True)

    def set_mumu_failed(self, message: str) -> None:
        self.mumu_status.setText("MuMu 连接失败：" + message)
        self.mumu_btn.setEnabled(True)

    def set_mobile_done(self, project_id: str, tag: str = "") -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, button, bar = row
        status.setStyleSheet("")
        status.setText("已安装到 MuMu" + (f" · {tag}" if tag else ""))
        button.setText("重新安装")
        bar.setValue(100)
        self._last_errors.pop(project_id, None)

    def set_mobile_failed(self, project_id: str, message: str) -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, button, bar = row
        status.setStyleSheet(f"color: {ERROR};")
        status.setText("安装到 MuMu 失败")
        status.setToolTip(message)
        button.setText("重试安装")
        bar.setValue(0)


    def begin_install(self, projects: list[Project]) -> None:
        """进入安装状态：清空上一次结果，显示每个项目的独立进度。"""
        self._installing = True
        self._active_ids = {project.id for project in projects}
        self._last_errors = {key: value for key, value in self._last_errors.items()
                             if key not in self._active_ids}
        self.overall_bar.setValue(0)
        self.overall_label.setText(f"总进度 · 0% · 0/{len(projects)}")
        self.all_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        for project in projects:
            row = self.rows.get(project.id)
            if row is None:
                continue
            _, status, button, bar = row
            status.setStyleSheet("")
            status.setText("准备安装…")
            status.setToolTip("")
            bar.setValue(0)
            button.setEnabled(False)

    def set_project_started(self, project_id: str, _name: str = "") -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, _, bar = row
        status.setStyleSheet("")
        status.setText("准备安装…")
        bar.setValue(0)

    def set_project_progress(self, project_id: str, value: float, message: str) -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, _, bar = row
        status.setStyleSheet("")
        status.setText(message or "安装中…")
        status.setToolTip(message or "")
        bar.setValue(int(max(0.0, min(100.0, value))))

    def set_project_done(self, project_id: str, tag: str = "") -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        self._last_errors.pop(project_id, None)
        self._updates.pop(project_id, None)
        self._latest_ids.discard(project_id)
        _, status, button, bar = row
        status.setStyleSheet("")
        status.setText("安装完成" + (f" · {tag}" if tag else ""))
        status.setToolTip(status.text())
        button.setText("覆盖安装")
        bar.setValue(100)

    def set_project_failed(self, project_id: str, message: str) -> None:
        row = self.rows.get(project_id)
        if row is None:
            return
        _, status, _, bar = row
        self._last_errors[project_id] = message
        status.setStyleSheet(f"color: {ERROR};")
        status.setText("安装失败 · 可重试")
        status.setToolTip(message)
        bar.setValue(0)

    def set_overall(self, value: float, message: str = "") -> None:
        self.overall_bar.setValue(int(max(0.0, min(100.0, value))))
        suffix = f" · {message}" if message else ""
        self.overall_label.setText(f"总进度 · {value:.0f}%{suffix}")
        self.overall_label.setToolTip(message)

    def finish_install(self, success: int, total: int, errors: list[str]) -> None:
        self._installing = False
        self._active_ids.clear()
        self.refresh_btn.setEnabled(True)
        self.all_btn.setEnabled(True)
        self.overall_bar.setValue(100)
        if errors:
            self.overall_label.setText(f"总进度 · 完成 {success}/{total} · {len(errors)} 个失败")
            self.overall_label.setToolTip("\n".join(errors))
        else:
            self.overall_label.setText(f"总进度 · 全部完成（{success}/{total}）")
            self.overall_label.setToolTip("")
        for project_id, (_, status, button, bar) in self.rows.items():
            button.setEnabled(True)
            if project_id in self._last_errors:
                status.setStyleSheet(f"color: {ERROR};")
                status.setText("安装失败 · 可重试")
                button.setText("重试安装")
            elif bar.value() >= 100:
                status.setStyleSheet("")
                button.setText("覆盖安装")
            else:
                status.setStyleSheet("")
                button.setText("安装")

    def update_projects(self, projects: list[Project], runtimes: dict, root: Path) -> None:
        self.root_label.setText(f"安装根目录：{root}")
        if self._installing:
            self.all_btn.setEnabled(False)
            return
        missing = False
        for project in projects:
            row = self.rows.get(project.id)
            if row is None:
                continue
            path, status, button, bar = row
            runtime = runtimes.get(project.id)
            version = getattr(runtime, "version", "") if runtime is not None else ""
            initialized = bool(getattr(runtime, "initialized", True))
            installed = bool(project.directory and Path(project.directory).is_dir() and initialized)
            if project.directory and Path(project.directory).is_dir() and not initialized:
                self._needs_init.add(project.id)
                status.setStyleSheet("")
                status.setText("启动器已安装 · 首次启动需初始化")
                button.setText("打开初始化")
                bar.setValue(0)
                continue
            self._needs_init.discard(project.id)
            if not installed and not project.mobile_only:
                missing = True
            path.setText("MuMu 模拟器" if project.mobile_only else (project.directory or "未安装"))
            if project.id in self._last_errors:
                status.setStyleSheet(f"color: {ERROR};")
                status.setText("安装失败 · 可重试")
                button.setText("重试安装")
                bar.setValue(0)
                continue
            status.setStyleSheet("")
            if project.mobile_only:
                if project.extra.get("mobile_installed"):
                    status.setText("已安装到 MuMu")
                    button.setText("重新安装")
                else:
                    status.setText("手机端 · 仅安卓")
                    button.setText("安装到 MuMu")
                bar.setValue(100 if project.extra.get("mobile_installed") else 0)
                continue
            if installed:
                latest = self._updates.get(project.id, "")
                latest_info = ""
                github_info = getattr(runtime, "github", None)
                if github_info is not None:
                    latest_info = str(getattr(github_info, "latest_tag", "") or "")
                if latest or (latest_info and version and is_newer(latest_info, version)):
                    latest = latest or latest_info
                    status.setText(f"已安装 v{version} · 可更新 {latest}")
                    status.setToolTip(f"最新版本 {latest}；更新会保留用户配置")
                    button.setText("更新")
                elif project.id in self._latest_ids or (
                    latest_info and version and not is_newer(latest_info, version)
                ):
                    status.setText(
                        ("已安装 v" + version if version else "已安装")
                        + " · 版本已至最新")
                    status.setToolTip("GitHub 最新 Release 与当前版本一致")
                    button.setText("覆盖安装")
                else:
                    status.setText("已安装" + (f" · v{version}" if version else ""))
                    status.setToolTip("点击“检查更新”获取 GitHub 最新版本")
                    button.setText("覆盖安装")
                bar.setValue(100)
            else:
                status.setText("等待安装")
                button.setText("安装")
                bar.setValue(0)
        if not missing and not self._last_errors:
            self.overall_bar.setValue(100)
            self.overall_label.setText("总进度 · 全部已安装")
        elif self.overall_bar.value() >= 100 and not self._last_errors:
            self.overall_bar.setValue(0)
            self.overall_label.setText("总进度 · 等待安装")
        self.all_btn.setEnabled(missing)

    def set_source_status(self, status: dict) -> None:
        names = list(status)
        present = [name for name, meta in status.items() if meta.get("exists")]
        tags = []
        for name in present:
            meta = status.get(name, {})
            if meta.get("downloaded"):
                tags.append(f"{name} {meta.get('downloaded')} 文件")
        self.source_status.setText(
            f"源码快照：{len(present)}/{len(names)} 已有"
            + (" · " + " / ".join(tags[:3]) if tags else " · sources/")
        )
        partial = [name for name, meta in status.items()
                   if "partial" in str(meta.get("mode", "")).lower()]
        if partial:
            self.source_status.setText(
                self.source_status.text() + " · " + "、".join(partial) + " 可补齐")
        self.source_status.setToolTip("\n".join(
            f"{name}: {meta.get('downloaded', 0)} 文件, {meta.get('bytes', 0) / 1024 / 1024:.1f} MB"
            for name, meta in status.items()
        ))

    def begin_source(self) -> None:
        self.source_btn.setEnabled(False)
        self.source_bar.setValue(0)
        self.source_status.setStyleSheet("")
        self.source_status.setText("正在读取源码清单…")

    def set_source_progress(self, value: float, message: str) -> None:
        self.source_bar.setValue(int(max(0.0, min(100.0, value))))
        self.source_status.setText(message)
        self.source_status.setToolTip(message)

    def finish_source(self, ok: bool, message: str = "") -> None:
        self.source_btn.setEnabled(True)
        if ok:
            self.source_bar.setValue(100)
            self.source_status.setStyleSheet("")
            self.source_status.setText("源码下载完成 · sources/")
        else:
            self.source_status.setStyleSheet(f"color: {ERROR};")
            self.source_status.setText("源码下载失败 · 可重试")
            self.source_status.setToolTip(message)

