"""无边框亚克力悬浮窗主窗口。"""
from __future__ import annotations

import ctypes
import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SwitchButton,
    TransparentToolButton,
)
from qframelesswindow import AcrylicWindow

from ..adapters import AdapterError, make_adapter
from ..config import Config
from ..discovery import probe
from ..display_control import default_display, list_displays
from ..games import actual_game_exe, apply_hit, game_workdir
from ..installer import install_name, latest_release
from ..maa_support import mumu_address
from ..models import GithubInfo, Project, Runtime, TaskItem
from ..services import GithubService, HistoryStore, LogTailer, ProcessService, safe_name
from ..util import app_dir, human_duration, open_path, open_url, resource_path, tail_lines
from .pages import ConfigPage, InstallPage, LogsPage, OverviewPage, TasksPage
from .theme import apply_theme
from .widgets import (
    ArgDialog,
    CommandDialog,
    CompactRow,
    ConfirmDialog,
    InstallStatusDialog,
    ProjectCard,
    TitleBar,
)
from .workers import (
    GameDisplayWorker,
    GuiQueueWorker,
    MobileInstallWorker,
    UpdateCheckWorker,
    GameScanWorker,
    GithubWorker,
    InstallWorker,
    MaaSetupWorker,
    MumuStartWorker,
    SourceFetchWorker,
)
from ..source_fetch import SOURCE_PROJECTS, source_status
from ..task_aliases import task_candidates


class Controller:
    """把无界面后端装配成 GUI 可用的状态容器。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.history = HistoryStore()
        self.service = ProcessService(self.history)
        self.github = GithubService(config.github_token, config.github_ttl)
        self.runtimes: dict[str, Runtime] = {}
        self.adapters: dict[str, object] = {}
        for project in config.projects:
            self.runtimes[project.id] = Runtime(project=project)
            self.adapters[project.id] = make_adapter(project)
        self.service.on_exit = self._on_exit
        self.on_exit = None

    def _on_exit(self, runtime: Runtime, code: int, log_path: str) -> None:
        if self.on_exit is not None:
            try:
                self.on_exit(runtime, code, log_path)
            except Exception:
                pass

    def load_tasks(self, project_id: str) -> None:
        runtime = self.runtimes[project_id]
        adapter = self.adapters[project_id]
        runtime.version = adapter.version()
        try:
            runtime.tasks = adapter.tasks()
            runtime.tasks_error = ""
        except Exception as exc:
            runtime.tasks = []
            runtime.tasks_error = f"读取任务失败：{exc}"
        runtime.tasks_loaded_at = time.time()

    def rebind(self, project_id: str) -> None:
        project = self.config.get(project_id)
        if project is None:
            return
        self.runtimes[project_id].project = project
        self.adapters[project_id] = make_adapter(project)
        self.load_tasks(project_id)

    def refresh(self) -> None:
        snapshot = self.service.processes()
        for runtime in self.runtimes.values():
            self.service.refresh(runtime, snapshot)


class FloatingPanel(AcrylicWindow):
    exit_signal = Signal(object, int, str)

    def __init__(self, config: Config, compact: bool | None = None) -> None:
        super().__init__()
        self.config = config
        self.controller = Controller(config)
        self.controller.on_exit = lambda runtime, code, path: self.exit_signal.emit(runtime, code, path)
        self.selected_id = config.projects[0].id if config.projects else ""
        self.current_log = ""
        self.tailer = LogTailer()
        self._log_poll = True
        self._github_workers: dict[str, GithubWorker] = {}
        self._game_worker: GameScanWorker | None = None
        self._expanded_size = QSize(1180, 760)
        self._compact_size = QSize(460, 336)
        self._pos = QPoint(0, 0)
        self._compact = bool(config.ui.get("compact", True) if compact is None else compact)
        self._topmost = bool(config.ui.get("always_on_top", True))
        self._close_to_tray = bool(config.ui.get("close_to_tray", True))
        self._opacity = float(config.ui.get("opacity", 0.97))
        self._tray_tip_shown = False
        self._quitting = False
        self._batch_queue: list[tuple[TaskItem, str]] = []
        self._batch_index = 0
        self._batch_active = False
        self._batch_project_id = ""
        self._install_worker: InstallWorker | None = None
        self._maa_worker: MaaSetupWorker | None = None
        self._mumu_worker: MumuStartWorker | None = None
        self._source_worker: SourceFetchWorker | None = None
        self._gui_queue_worker: GuiQueueWorker | None = None
        self._game_display_worker: GameDisplayWorker | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._mobile_worker: MobileInstallWorker | None = None
        self._mobile_permissions: set[str] = set()
        self._displays: list = []
        self._bureau = "pc"
        self._maa_setup_project_id = ""

        self.setWindowTitle("二游面板")
        self.setWindowIcon(self._app_icon())
        self.setMinimumSize(440, 320)
        self._hide_default_title_bar()
        self._build_ui()
        self._build_tray()
        self._load_tasks()
        self._restore_window()
        self._connect_signals()
        self.exit_signal.connect(self._exit_on_ui)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._select_project(self.selected_id, force=True)
        self._tick()
        self.install_page.set_auto_update(bool(self.config.ui.get("auto_update", False)))
        self._set_bureau("pc")
        QTimer.singleShot(400, self._refresh_source_status)
        QTimer.singleShot(500, lambda: self._scan_games(False))
        QTimer.singleShot(250, self._first_show)
        QTimer.singleShot(5000, self._auto_update_check)

    # ---- 构建 -----------------------------------------------------
    def _hide_default_title_bar(self) -> None:
        try:
            self.titleBar.hide()
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.setStyleSheet("")
        root = QWidget(self)
        root.setObjectName("RootCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = TitleBar(root)
        layout.addWidget(self.title_bar)

        self.stack = QStackedWidget(root)
        self.compact_page = self._build_compact(root)
        self.expanded_page = self._build_expanded(root)
        self.stack.addWidget(self.compact_page)
        self.stack.addWidget(self.expanded_page)
        layout.addWidget(self.stack, 1)

        self.title_bar.set_compact(self._compact)
        self.title_bar.set_pinned(self._topmost)
        self.stack.setCurrentIndex(0 if self._compact else 1)

    def _build_compact(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 2, 12, 10)
        layout.setSpacing(6)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.compact_rows_layout = rows
        self.compact_rows_parent = content
        self.compact_rows: dict[str, CompactRow] = {}
        for project in self.config.projects:
            row = CompactRow(project, content)
            self.compact_rows[project.id] = row
            rows.addWidget(row)
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.compact_hint = QLabel(f"{len(self.config.projects)} 个项目 · 悬浮监控", page)
        self.compact_hint.setObjectName("PageHint")
        self.compact_install = PushButton(FluentIcon.INFO, "安装情况", page)
        self.compact_install.setToolTip("查看每个项目的安装状态和进度")
        self.compact_install.clicked.connect(self._show_install_status)
        footer.addWidget(self.compact_hint, 1)
        footer.addWidget(self.compact_install)
        layout.addLayout(footer)
        return page

    def _build_expanded(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(12, 2, 14, 14)
        layout.setSpacing(14)

        sidebar = QWidget(page)
        sidebar.setFixedWidth(236)
        sidebar_outer = QVBoxLayout(sidebar)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(8)
        head = QHBoxLayout()
        label = QLabel("项目", sidebar)
        label.setObjectName("SectionTitle")
        sub = QLabel("支持设备的项目", sidebar)
        sub.setObjectName("TinyLabel")
        head.addWidget(label)
        head.addWidget(sub)
        head.addStretch(1)
        sidebar_outer.addLayout(head)
        bureau_row = QHBoxLayout()
        bureau_row.setSpacing(4)
        self.bureau_android = PushButton("安卓", sidebar)
        self.bureau_apple = PushButton("苹果", sidebar)
        self.bureau_pc = PushButton("电脑端", sidebar)
        self.bureau_android.clicked.connect(lambda: self._set_bureau("android"))
        self.bureau_apple.clicked.connect(lambda: self._set_bureau("apple"))
        self.bureau_pc.clicked.connect(lambda: self._set_bureau("pc"))
        for button in (self.bureau_android, self.bureau_apple, self.bureau_pc):
            button.setMinimumHeight(30)
            bureau_row.addWidget(button, 1)
        sidebar_outer.addLayout(bureau_row)
        scroll = QScrollArea(sidebar)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        side = QVBoxLayout(content)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        self.sidebar_cards_layout = side
        self.sidebar_cards_parent = content
        self.cards: dict[str, ProjectCard] = {}
        for project in self.config.projects:
            card = ProjectCard(project, content)
            self.cards[project.id] = card
            side.addWidget(card)
        self.add_project_btn = PushButton(FluentIcon.ADD, "添加二游项目", content)
        self.add_project_btn.setMinimumHeight(44)
        self.add_project_btn.setToolTip("输入 GitHub 地址，自动解析并加入项目列表")
        self.add_project_btn.clicked.connect(self._add_custom_project)
        side.addWidget(self.add_project_btn)
        side.addStretch(1)
        scroll.setWidget(content)
        sidebar_outer.addWidget(scroll, 1)
        self.side_summary = QLabel("", sidebar)
        self.side_summary.setObjectName("PageHint")
        self.side_summary.setWordWrap(True)
        self.side_install = PushButton(FluentIcon.INFO, "安装情况", sidebar)
        self.side_install.setToolTip("查看每个项目的安装状态和进度")
        self.side_install.clicked.connect(self._show_install_status)
        self.side_settings = PushButton(FluentIcon.SETTING, "总设置", sidebar)
        self.side_settings.clicked.connect(self._open_settings)
        sidebar_outer.addWidget(self.side_summary)
        sidebar_outer.addWidget(self.side_install)
        sidebar_outer.addWidget(self.side_settings)
        layout.addWidget(sidebar)

        self.global_bar = QWidget(page)
        global_layout = QHBoxLayout(self.global_bar)
        global_layout.setContentsMargins(0, 0, 0, 4)
        global_layout.setSpacing(8)
        self.global_update = PushButton(FluentIcon.SYNC, "更新", self.global_bar)
        self.global_display = PushButton(FluentIcon.SETTING, "显示", self.global_bar)
        for button in (self.global_update, self.global_display):
            button.setMinimumHeight(38)
            button.setMinimumWidth(110)
            button.setStyleSheet("font-size:14px;")
            global_layout.addWidget(button)
        global_layout.addStretch(1)

        self.content_stack = QStackedWidget(page)
        self.tabs = QTabWidget(self.content_stack)
        self.project_tabs = self.tabs
        self.tabs.setDocumentMode(True)
        self.overview = OverviewPage(self.tabs)
        self.tasks = TasksPage(self.tabs)
        self.tasks.setVisible(False)
        self.logs = LogsPage(self.tabs)
        self.tabs.addTab(self.overview, "项目")
        self.tabs.addTab(self.logs, "日志")
        self.tabs.tabBar().setMinimumHeight(36)
        self.tabs.setStyleSheet(
            "QTabBar::tab { min-width: 84px; height: 34px; font-size: 13px; padding: 0 16px; }"
        )
        self.install_page = InstallPage(self.config.projects, page)
        self.config_page = ConfigPage(page)
        self.content_stack.addWidget(self.tabs)
        self.content_stack.addWidget(self.install_page)
        self.content_stack.addWidget(self.config_page)
        self.global_update.clicked.connect(lambda: self._show_global("update"))
        self.global_display.clicked.connect(lambda: self._show_global("display"))
        right = QWidget(page)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addWidget(self.global_bar)
        right_layout.addWidget(self.content_stack, 1)
        layout.addWidget(right, 1)
        return page

    def _build_tray(self) -> None:
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(self._app_icon(), self)
        menu = QMenu(self)
        self.act_show = QAction("显示 / 隐藏", self)
        self.act_show.triggered.connect(self._toggle_visible)
        self.act_top = QAction("窗口置顶", self, checkable=True)
        self.act_top.setChecked(self._topmost)
        self.act_top.triggered.connect(self._set_topmost_from_menu)
        self.act_compact = QAction("紧凑悬浮窗", self, checkable=True)
        self.act_compact.setChecked(self._compact)
        self.act_compact.triggered.connect(lambda: self.set_compact(self.act_compact.isChecked()))
        self.act_install = QAction("安装缺失项目", self)
        self.act_install.triggered.connect(lambda: self._install_projects(None))
        self.act_quit = QAction("退出", self)
        self.act_quit.triggered.connect(self._quit)
        menu.addAction(self.act_show)
        menu.addAction(self.act_top)
        menu.addAction(self.act_compact)
        menu.addSeparator()
        menu.addAction(self.act_install)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("二游面板 · 异环 / 鸣潮 / 明日方舟")
        self.tray.activated.connect(self._tray_activated)
        if self._tray_available:
            self.tray.show()

    def _connect_signals(self) -> None:
        self.title_bar.pin_toggled.connect(self.set_topmost)
        self.title_bar.compact_toggled.connect(lambda: self.set_compact(not self._compact))
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.maximize_clicked.connect(self._toggle_maximize)
        self.title_bar.close_clicked.connect(self._close_clicked)
        self.title_bar.settings_clicked.connect(self._open_settings)
        for row in self.compact_rows.values():
            row.activated.connect(lambda pid: self._select_project(pid))
            row.detail_requested.connect(self._open_detail)
            row.toggle_requested.connect(self._toggle_project)
        for card in self.cards.values():
            card.activated.connect(lambda pid: self._select_project(pid))
        self.overview.start_requested.connect(self._start_selected_action)
        self.overview.stop_requested.connect(self._stop_selected)
        self.overview.command_requested.connect(self._open_command)
        self.overview.settings_requested.connect(self._open_project_settings)
        self.overview.open_dir_requested.connect(self._open_dir)
        self.overview.open_repo_requested.connect(self._open_repo)
        self.overview.sync_requested.connect(lambda: self._refresh_github(self.selected_id, True))
        self.overview.install_requested.connect(lambda: self._install_projects(self.selected_id))
        self.overview.logs_requested.connect(lambda: self._open_tab(3))
        self.overview.launch_game_requested.connect(self._launch_game)
        self.overview.launch_emulator_requested.connect(self._launch_emulator)
        self.overview.find_game_requested.connect(lambda: self._scan_games(True))
        self.overview.update_check_requested.connect(lambda: self._check_updates(False))
        self.tasks.run_requested.connect(self._run_tasks)
        self.tasks.maa_settings_saved.connect(self._maa_save_settings)
        self.tasks.maa_daily_requested.connect(self._maa_daily)
        self.tasks.maa_launch_requested.connect(self._launch_emulator)
        self.tasks.maa_test_requested.connect(self._maa_test)
        self.tasks.maa_prepare_requested.connect(self._maa_prepare)
        self.tasks.open_gui_requested.connect(self._open_project_gui)
        self.tasks.queue_gui_requested.connect(self._queue_gui_tasks)
        self.tasks.config_requested.connect(self._open_task_options)
        self.tasks.refresh_requested.connect(self._reload_selected)
        self.logs.log_selected.connect(self._select_log)
        self.logs.filter_changed.connect(self._filter_logs)
        self.logs.follow_changed.connect(lambda value: setattr(self, "_log_poll", value))
        self.logs.open_dir_requested.connect(self._open_log_dir)
        self.logs.refresh_requested.connect(self._refresh_logs)
        self.config_page.open_config_requested.connect(self._open_config_dir)
        self.config_page.open_logs_requested.connect(self._open_log_dir)
        self.config_page.open_source_requested.connect(self._open_source_dir)
        self.config_page.open_repo_requested.connect(self._open_repo)
        self.config_page.refresh_requested.connect(self._reload_selected)
        self.config_page.game_display_changed.connect(self._game_display_changed)
        self.config_page.game_background_changed.connect(self._game_background_changed)
        self.config_page.display_refresh_requested.connect(self._refresh_displays)
        self.config_page.display_apply_requested.connect(self._apply_current_game_display)
        self.config_page.save_requested.connect(self._save_config)
        self.install_page.install_requested.connect(self._install_from_page)
        self.install_page.update_requested.connect(
            lambda project: self._install_projects(project.id, True))
        self.install_page.update_check_requested.connect(lambda: self._check_updates(False))
        self.install_page.auto_update_changed.connect(self._auto_update_changed)
        self.install_page.mobile_install_requested.connect(self._install_mobile_project)
        self.install_page.mumu_requested.connect(self._connect_mumu)
        self.install_page.apple_help_requested.connect(
            lambda: open_url("https://docs.maa.plus/zh-cn/manual/device/macos.html"))
        self.install_page.mumu_site_requested.connect(
            lambda: open_url("https://mumu.163.com/"))
        self.install_page.refresh_requested.connect(self._reload_selected)
        self.install_page.source_requested.connect(self._download_sources)
        self.tabs.currentChanged.connect(self._tab_changed)

        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("Ctrl+I"), self,
                  lambda: self._install_projects(self.selected_id))
        QShortcut(QKeySequence("Ctrl+Shift+I"), self,
                  lambda: self._install_projects(None))
        QShortcut(QKeySequence("F5"), self, self._reload_selected)
        QShortcut(QKeySequence("Ctrl+G"), self, lambda: self._scan_games(True))
        for index, project in enumerate(self.config.projects[:9]):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self,
                      lambda pid=project.id: self._select_project(pid, True))

    # ---- 生命周期 -------------------------------------------------
    def _first_show(self) -> None:
        self._apply_topmost(self._topmost)
        self.setWindowOpacity(self._opacity)
        self._set_round_corners()
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore_window(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        size = self._compact_size if self._compact else self._expanded_size
        pos = self.config.ui.get("pos")
        x = y = None
        if isinstance(pos, list) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
            except (TypeError, ValueError):
                x = y = None
        if x is None or y is None or not screen.contains(QRect(x, y, 80, 80)):
            x = screen.right() - size.width() - 24
            y = screen.top() + 24
        self.setGeometry(x, y, size.width(), size.height())
        self._pos = QPoint(x, y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._set_round_corners()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self.isMaximized():
            self._pos = self.pos()

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if self._quitting or (app is not None and app.closingDown()):
            self._save_ui()
            self.tray.hide()
            super().closeEvent(event)
            return
        if self._close_to_tray and self._tray_available:
            event.ignore()
            self.hide()
            if not self._tray_tip_shown:
                self._tray_tip_shown = True
                self.tray.showMessage("二游面板", "已隐藏到托盘，双击图标恢复。",
                                      QSystemTrayIcon.Information, 2500)
            return
        self._save_ui()
        self.tray.hide()
        super().closeEvent(event)

    def _save_ui(self) -> None:
        self.config.ui.update({
            "compact": self._compact,
            "always_on_top": self._topmost,
            "close_to_tray": self._close_to_tray,
            "opacity": round(self._opacity, 2),
            "pos": [self._pos.x(), self._pos.y()],
        })
        self.config.save()

    # ---- 窗口控制 -------------------------------------------------
    def set_compact(self, compact: bool) -> None:
        if compact == self._compact and self.stack.currentIndex() == (0 if compact else 1):
            return
        self._compact = compact
        self.stack.setCurrentIndex(0 if compact else 1)
        self.title_bar.set_compact(compact)
        self.act_compact.setChecked(compact)
        size = self._compact_size if compact else self._expanded_size
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left(), self._pos.x()), area.right() - size.width())
        y = min(max(area.top(), self._pos.y()), area.bottom() - size.height())
        self.setGeometry(x, y, size.width(), size.height())
        self._pos = QPoint(x, y)
        self._save_ui()
        self._tick()
        if not compact:
            self._refresh_selected()

    def set_topmost(self, enabled: bool) -> None:
        self._topmost = enabled
        self.act_top.setChecked(enabled)
        self.title_bar.set_pinned(enabled)
        self._apply_topmost(enabled)
        self._save_ui()

    def _apply_topmost(self, enabled: bool) -> None:
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_bool
            flag = -1 if enabled else -2  # HWND_TOPMOST / HWND_NOTOPMOST
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(flag), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    def _set_round_corners(self) -> None:
        if os.name != "nt":
            return
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 33, ctypes.byref(value), 4)
        except Exception:
            pass

    def _app_icon(self) -> QIcon:
        path = resource_path("assets/panel.png")
        if path.is_file():
            return QIcon(str(path))
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            if self.isMinimized():
                self.showNormal()
            else:
                self.show()
            self.raise_()
            self.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._toggle_visible()

    def _close_clicked(self) -> None:
        self.close()

    def _quit(self) -> None:
        self._quitting = True
        self._save_ui()
        for worker in list(self._github_workers.values()):
            try:
                worker.wait(3000)
            except Exception:
                pass
        self.tray.hide()
        QApplication.quit()

    def _set_topmost_from_menu(self, checked: bool) -> None:
        self.set_topmost(checked)

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.title_bar.set_maximized(False)
        else:
            if self._compact:
                self.set_compact(False)
            self.showMaximized()
            self.title_bar.set_maximized(True)

    def _open_detail(self, project_id: str) -> None:
        self._select_project(project_id)
        self.set_compact(False)
        self._open_tab(0)

    def _open_tab(self, index: int) -> None:
        self.set_compact(False)
        if index == 1:
            self._show_global("update")
        elif index == 2:
            self._show_global("display")
        elif index == 3:
            self._show_project_view()
            self.project_tabs.setCurrentIndex(1)
        else:
            self._show_project_view()
            self.project_tabs.setCurrentIndex(0)

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_logs()

    # ---- 数据刷新 -------------------------------------------------
    def _load_tasks(self) -> None:
        for project in self.config.projects:
            self.controller.load_tasks(project.id)

    def _tick(self) -> None:
        self.controller.refresh()
        proc_names = {p.name.lower() for p in self.controller.service.processes()}
        running = 0
        for project in self.config.projects:
            runtime = self.controller.runtimes[project.id]
            adapter = self.controller.adapters[project.id]
            names = [n.strip().lower() for n in (project.game_process or "").split(";") if n.strip()]
            runtime.game_running = bool(names and any(name in proc_names for name in names))
            if runtime.status == "running":
                running += 1
            row = self.compact_rows.get(project.id)
            if row is not None:
                row.update_runtime(runtime, adapter.mode_text())
            card = self.cards.get(project.id)
            if card is not None:
                card.update_runtime(runtime, adapter.mode_text())
        total = len(self.config.projects)
        if self._compact:
            self.title_bar.set_summary(f"{running}/{total}")
        else:
            self.title_bar.set_summary(f"{running}/{total} 运行中  ·  {time.strftime('%H:%M')}")
        self.compact_hint.setText(f"{total} 个项目 · {running} 个运行中")
        self.side_summary.setText(f"{total} 个项目 · {running} 个运行中")
        installed = sum(
            1 for project in self.config.projects
            if (project.directory and Path(project.directory).is_dir())
            or (project.mobile_only and project.extra.get("mobile_installed"))
        )
        if self._install_worker is not None:
            install_text = "安装情况 · 安装中"
        elif getattr(self.install_page, "_last_errors", {}):
            install_text = f"安装情况 · {len(self.install_page._last_errors)} 个失败"
        elif installed == total:
            install_text = "安装情况 · 全部已安装"
        else:
            install_text = f"安装情况 · {installed}/{total} 已安装"
        self.side_install.setText(install_text)
        self.compact_install.setText(f"安装情况 · {installed}/{total}")
        selected = self._selected()
        if selected is not None:
            project, runtime, adapter = selected
            self.overview.update_live(runtime)
            self._poll_log()
        self.install_page.update_projects(self.config.projects, self.controller.runtimes,
                                          self.config.install_root_path())

    def _selected(self):
        if not self.selected_id:
            return None
        project = self.config.get(self.selected_id)
        if project is None:
            return None
        return project, self.controller.runtimes[self.selected_id], self.controller.adapters[self.selected_id]

    def _select_project(self, project_id: str, force: bool = False) -> None:
        if not project_id:
            return
        if project_id == self.selected_id and not force:
            return
        self.selected_id = project_id
        for pid, card in self.cards.items():
            card.set_selected(pid == project_id)
        for pid, row in self.compact_rows.items():
            row.set_selected(pid == project_id)
        self._refresh_selected()
        self._refresh_logs()
        self._refresh_github(project_id, False)
        self._show_project_view()
        project = self.config.get(project_id)
        if project is not None and project.mobile_only and project.id not in self._mobile_permissions:
            QTimer.singleShot(120, lambda p=project: self._ask_mobile_permission(p))

    def _refresh_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        runtime.history = self.controller.history.recent(project.id, 6)
        github = runtime.github
        if github is None and project.repo:
            cached = self.controller.github.cached(project.repo)
            if cached:
                github = _github_info(cached)
                runtime.github = github
        self.overview.set_project(project, runtime, adapter, github, runtime.history)
        self._refresh_selected_tasks()
        self._refresh_selected_config()

    def _refresh_selected_tasks(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        self.tasks.set_tasks(project, runtime, adapter)
        if project.kind in ("okscript", "maa"):
            self.tasks.run_btn.setEnabled(False)
            return
        runnable = True
        note = ""
        if project.kind == "maa" and hasattr(adapter, "cli") and not adapter.cli():
            runnable = False
            note = "未找到 maa-cli：任务不可直接运行，GUI 仍可启动"
        runner = getattr(adapter, "runner", None)
        if callable(runner) and runner() == "missing":
            runnable = False
            note = "未找到 ok-*.exe / Python：任务不可运行"
        self.tasks.run_btn.setEnabled(runnable and not self._batch_active)
        if note:
            self.tasks.command_hint.setText(note)

    def _refresh_selected_config(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
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
        self.config_page.set_files(files, config_dir)
        self._refresh_display_settings(project)

    def _refresh_display_settings(self, project: Project) -> None:
        self._displays = list_displays()
        key = str(project.extra.get("game_display", "") or "")
        if not key and self._displays:
            fallback = default_display(self._displays)
            key = fallback.device if fallback else ""
        self.config_page.set_display_settings(
            project, self._displays, key,
            bool(project.extra.get("game_background", False)),
        )

    def _game_display_changed(self, key: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, _runtime, _adapter = selected
        project.extra["game_display"] = str(key)
        self.config.save()
        display = next((d for d in self._displays if d.device == key or d.monitor_id == key), None)
        if display is not None:
            self._info(f"游戏启动屏幕：{display.label}")

    def _game_background_changed(self, checked: bool) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, _runtime, _adapter = selected
        project.extra["game_background"] = bool(checked)
        self.config.save()
        self._info("游戏启动后放到后台" if checked else "游戏启动后显示在前台")

    def _refresh_displays(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, _runtime, _adapter = selected
        self._refresh_display_settings(project)
        self._info(f"已检测到 {len(self._displays)} 个显示屏")

    def _apply_current_game_display(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, _runtime, _adapter = selected
        self._start_game_display_worker(project)

    def _start_game_display_worker(self, project: Project) -> None:
        if project.kind == "maa" or project.game_kind != "pc":
            return
        if self._game_display_worker is not None:
            return
        displays = list_displays()
        self._displays = displays
        if not displays:
            self._info("没有检测到显示屏，无法应用游戏显示设置")
            return
        key = str(project.extra.get("game_display", "") or "")
        if not key:
            fallback = default_display(displays)
            key = fallback.device if fallback else ""
        background = bool(project.extra.get("game_background", False))
        worker = GameDisplayWorker(project, key, background, 120, self)
        worker.progress.connect(lambda _value, message: self.tasks.command_hint.setText(message))
        worker.done.connect(self._game_display_done)
        worker.failed.connect(self._game_display_failed)
        worker.finished.connect(self._game_display_finished)
        self._game_display_worker = worker
        worker.start()

    def _game_display_finished(self) -> None:
        self._game_display_worker = None

    def _game_display_done(self, result) -> None:
        message = getattr(result, "message", "游戏显示设置已应用")
        self._info(message)
        self.tasks.command_hint.setText(message)

    def _game_display_failed(self, message: str) -> None:
        self._info(f"游戏显示设置未应用：{message}")

    def _open_task_options(self, key: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        candidates: list[Path] = []
        if project.kind == "maa":
            if hasattr(adapter, "ensure_profile"):
                try:
                    profile = adapter.ensure_profile(False)
                    if profile:
                        candidates.append(Path(profile))
                except Exception:
                    pass
        else:
            class_name = key.rsplit(".", 1)[-1]
            workdir = getattr(adapter, "task_workdir", lambda: project.directory)()
            if workdir:
                candidates.append(Path(workdir) / "configs" / f"{class_name}.json")
            if project.directory:
                candidates.append(Path(project.directory) / "configs" / f"{class_name}.json")
                try:
                    candidates.extend(Path(project.directory).glob(
                        f"data/apps/*/working/configs/{class_name}.json"))
                except OSError:
                    pass
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            self._info("该任务还没有配置文件；运行一次后会在 configs/ 生成，然后再点“任务选项”")
            return
        self._refresh_selected_config()
        self._open_tab(2)
        self.config_page.select_path(path)

    def _save_config(self, path: str, text: str) -> None:
        target = Path(path)
        suffix = target.suffix.lower()
        if suffix in (".json", ".jsonc"):
            try:
                json.loads(text)
            except ValueError as exc:
                self._error(f"JSON 格式错误，未保存：{exc}")
                return
        try:
            target.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            self._error(f"保存失败：{exc}")
            return
        self._info("配置已保存：" + target.name)
        self._refresh_selected_config()

    def _log_files(self) -> list[Path]:
        selected = self._selected()
        if selected is None:
            return []
        project, runtime, adapter = selected
        files: list[Path] = []
        try:
            files.extend(adapter.log_files()[:30])
        except OSError:
            pass
        try:
            prefix = safe_name(project.id) + "-"
            panel = [p for p in self.controller.service.log_dir.glob(prefix + "*.log") if p.is_file()]
            panel.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            files.extend(panel[:20])
        except OSError:
            pass
        unique: dict[str, Path] = {}
        for path in files:
            unique[str(path)] = path
        return sorted(unique.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    def _refresh_logs(self) -> None:
        files = self._log_files()
        if not files:
            self.current_log = ""
            self.logs.clear()
            self.logs.set_files([], "")
            self.logs.status.setText("暂无日志。启动项目或运行任务后，这里会实时显示输出。")
            self.tailer = LogTailer()
            return
        target = self.current_log if any(str(p) == self.current_log for p in files) else str(files[0])
        self.logs.set_files(files, target)
        if self.current_log != target:
            self._select_log(target)

    def _select_log(self, path: str) -> None:
        if not path:
            return
        self.current_log = path
        self.tailer = LogTailer()
        lines = self.tailer.reset(path, 300)
        self.logs.clear()
        self.logs.append_lines(lines, reset=True)
        self.logs.status.setText(f"{Path(path).name}  ·  跟随中")

    def _filter_logs(self, _text: str) -> None:
        if not self.current_log:
            return
        self.logs.clear()
        self.logs.append_lines(tail_lines(self.current_log, 400), reset=True)

    def _poll_log(self) -> None:
        if not self._log_poll or not self.current_log:
            return
        lines, reset = self.tailer.poll()
        if reset:
            self.logs.clear()
        if lines:
            self.logs.append_lines(lines)

    def _reload_selected(self) -> None:
        if not self.selected_id:
            return
        self.controller.load_tasks(self.selected_id)
        self._refresh_selected()
        self._refresh_logs()
        InfoBar.success("已刷新", "任务与日志已重新加载", parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=1800)

    # ---- 操作 -----------------------------------------------------
    def _stop_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if self._batch_active:
            self._batch_active = False
            self._batch_queue = []
            self._batch_index = 0
            self._batch_project_id = ""
            self._refresh_selected_tasks()
        message = self.controller.service.stop(runtime)
        self._tick()
        self._info(f"{project.game}：{message}")

    def _toggle_project(self, project_id: str) -> None:
        self._select_project(project_id, force=True)
        runtime = self.controller.runtimes[project_id]
        if runtime.status == "running":
            self._stop_selected()
        elif runtime.project.kind in ("okscript", "maa", "generic"):
            self._open_project_gui()
        else:
            self.set_compact(False)
            self._show_project_view()
            self.project_tabs.setCurrentIndex(0)

    def _start_selected_action(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.mobile_only:
            self._install_mobile_project(project)
        elif project.kind in ("okscript", "maa", "generic"):
            self._open_project_gui()
        else:
            self._open_tab(0)

    def _queue_gui_tasks(self, tasks: list[TaskItem]) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.kind != "okscript":
            return
        if not tasks:
            return
        if self._gui_queue_worker is not None:
            self._info("正在向原项目队列提交任务，请稍候")
            return
        if runtime.status != "running":
            try:
                self.controller.service.start_gui(runtime, adapter)
            except AdapterError as exc:
                self._error(str(exc))
                return
            except Exception as exc:
                self._error(_friendly(exc))
                return
            QTimer.singleShot(2600, lambda: self._start_gui_queue(project, tasks))
        else:
            self._start_gui_queue(project, tasks)
        self._start_game_display_worker(project)

    def _start_gui_queue(self, project: Project, tasks: list[TaskItem]) -> None:
        if self._gui_queue_worker is not None:
            return
        runtime = self.controller.runtimes.get(project.id)
        if runtime is None or not runtime.pid:
            self._error("原项目 GUI 没有可用 PID，请先打开原项目")
            return
        queue = [task for task in tasks
                 if task.kind != "trigger"
                 and not task.key.lower().endswith("launchertask")]
        if not queue:
            self._info("启动游戏由原项目自动执行；没有可加入队列的一次性任务")
            return
        names = [task_candidates(project, task.key, task.name) for task in queue]
        self.tasks.run_btn.setEnabled(False)
        worker = GuiQueueWorker(runtime.pid, names, project.name, self)
        worker.progress.connect(lambda _value, message: self.tasks.command_hint.setText(message))
        worker.done.connect(self._gui_queue_done)
        worker.failed.connect(self._gui_queue_failed)
        worker.finished.connect(self._gui_queue_finished)
        self._gui_queue_worker = worker
        worker.start()

    def _gui_queue_finished(self) -> None:
        self._gui_queue_worker = None
        self.tasks.run_btn.setEnabled(True)

    def _gui_queue_done(self, started) -> None:
        started = list(started or [])
        self.tasks.command_hint.setText(
            f"已提交 {len(started)} 个任务到原项目 GUI；原界面已最小化。")
        self._info(f"已交给原项目 GUI：{len(started)} 个任务")

    def _gui_queue_failed(self, message: str) -> None:
        self.tasks.command_hint.setText("原项目 GUI 自动操作失败；可点“打开原项目界面”手动执行")
        self._error("原项目 GUI 操作失败：" + message)

    def _open_project_gui(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.mobile_only:
            self._install_mobile_project(project)
            return
        if project.kind not in ("okscript", "maa", "generic"):
            self._open_tab(0)
            return
        if not project.directory:
            self._install_projects(project.id)
            return
        if runtime.status == "running":
            self._info("原项目界面已在运行")
            return
        try:
            self.controller.service.start_gui(runtime, adapter)
        except AdapterError as exc:
            self._error(str(exc))
            return
        except Exception as exc:
            self._error(_friendly(exc))
            return
        self._after_start(runtime, "已打开原项目界面；任务请在上游界面执行")
        self._start_game_display_worker(project)

    def _after_start(self, runtime: Runtime, message: str) -> None:
        self._select_project(runtime.project.id, force=True)
        self._refresh_selected()
        self.set_compact(False)
        self._show_project_view()
        self.project_tabs.setCurrentIndex(0)
        self._refresh_logs()
        self._info(f"{runtime.project.game} · {message}")

    def _run_tasks(self, tasks) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if runtime.status == "running":
            self._info("请先停止当前进程，再运行任务")
            return
        if project.kind == "okscript":
            self._queue_gui_tasks(list(tasks))
            return
        runner = getattr(adapter, "runner", None)
        if callable(runner) and runner() == "missing":
            self._info("未找到可执行程序，无法运行任务")
            return

        selected_tasks = [task for task in tasks if task.kind != "trigger"]
        triggers = [task for task in tasks if task.kind == "trigger"]
        if triggers:
            if len(tasks) > 1:
                self._info("常驻任务请单独运行，不要与批量任务混选")
                return
            task = triggers[0]
            arg = ""
            if task.arg_prompt:
                dialog = ArgDialog(f"运行常驻任务 · {task.name}", task.arg_prompt, task.arg_default, self)
                if dialog.exec() != QDialog.Accepted:
                    return
                arg = dialog.value()
            self._start_task(task, arg)
            self._info("常驻任务已启动；需要停止时点“停止”")
            return
        if not selected_tasks:
            return

        prepared: list[tuple[TaskItem, str]] = []
        for task in selected_tasks:
            arg = ""
            if task.arg_prompt:
                dialog = ArgDialog(f"运行 · {task.name}", task.arg_prompt, task.arg_default, self)
                if dialog.exec() != QDialog.Accepted:
                    return
                arg = dialog.value()
            prepared.append((task, arg))

        if project.kind == "maa" and not all(task.key == "closedown" for task in selected_tasks):
            # 用户只选任务，不再手动开模拟器、测 ADB；准备完成后自动执行。
            self._start_maa_setup(
                project,
                str(project.extra.get("maa_address", "127.0.0.1:16384")),
                True,
                lambda: self._run_prepared(prepared, project),
            )
            return

        self._run_prepared(prepared, project)

    def _run_prepared(self, prepared: list[tuple[TaskItem, str]], project: Project) -> None:
        if not prepared:
            return
        if len(prepared) == 1:
            self._start_task(*prepared[0])
            return
        self._batch_queue = prepared
        self._begin_batch(prepared, project.id)

    def _begin_batch(self, prepared: list[tuple[TaskItem, str]], project_id: str) -> None:
        if not prepared:
            return
        if len(prepared) == 1:
            self._start_task(*prepared[0])
            return
        self._batch_queue = prepared
        self._batch_index = 0
        self._batch_active = True
        self._batch_project_id = project_id
        self.tasks.run_btn.setEnabled(False)
        self._info(f"批量执行 {len(prepared)} 个任务（按顺序）")
        self._start_next_batch()

    def _maa_save_settings(self, adb: str, address: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.kind != "maa":
            return
        if adb:
            project.game_adb = adb
        project.extra["maa_address"] = address or "127.0.0.1:16384"
        self.config.save()
        try:
            adapter.ensure_profile(force=True)
        except Exception as exc:
            self._error(f"写入 MAA 连接配置失败：{exc}")
            return
        self._info("MAA 连接已保存（maa-cli profile）")

    def _maa_test(self, adb: str, address: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.kind != "maa":
            return
        adb = adb or project.game_adb or "adb"
        address = address or "127.0.0.1:16384"
        try:
            connect = subprocess.run(
                [adb, "connect", address], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=12, creationflags=0x08000000)
            devices = subprocess.run(
                [adb, "devices"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=12, creationflags=0x08000000)
        except Exception as exc:
            self._error("ADB 测试失败：" + _friendly(exc))
            return
        output = ((connect.stdout or "") + (connect.stderr or "") + "\n" +
                  (devices.stdout or "") + (devices.stderr or "")).strip()
        lines = [line for line in output.splitlines() if line.strip()]
        ready = any(line.rstrip().endswith("\tdevice") or " device" in line for line in lines)
        message = " / ".join(lines[:3]) or "无输出"
        if ready:
            self._info("ADB 已连接：" + message[:220])
        else:
            self._error("ADB 未就绪：" + message[:220])

    def _maa_prepare(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, _adapter = selected
        if project.kind != "maa":
            return
        if runtime.status == "running":
            self._info("MAA 任务正在运行，请先停止")
            return
        address = self.tasks.maa_address.text().strip()
        if not address:
            address = str(project.extra.get("maa_address", "127.0.0.1:16384"))
        self._start_maa_setup(project, address, True, None)

    def _start_maa_setup(self, project: Project, address: str, launch_emulator: bool,
                         after) -> None:
        if self._install_worker is not None:
            self._info("项目安装进行中，请稍后")
            return
        if self._maa_worker is not None:
            self._info("MAA 正在准备中，请等待完成")
            return
        if self._mumu_worker is not None:
            self._info("MuMu 正在启动/等待 ADB，请稍候")
            return
        if project.directory:
            project.extra["maa_address"] = address or "127.0.0.1:16384"
        self._maa_setup_project_id = project.id
        self.tasks.set_maa_busy(True)
        self.tasks.set_maa_progress(0, "检查 MAA 环境…")
        worker = MaaSetupWorker(
            project,
            self.config.install_root_path(),
            self.config.github_token,
            address,
            launch_emulator,
            self,
        )
        worker.progress.connect(self.tasks.set_maa_progress)
        worker.done.connect(lambda result: self._maa_setup_done(result, after))
        worker.failed.connect(self._maa_setup_failed)
        worker.finished.connect(self._maa_worker_finished)
        self._maa_worker = worker
        worker.start()

    def _maa_worker_finished(self) -> None:
        self._maa_worker = None

    def _maa_setup_done(self, result, after) -> None:
        project = self.config.get(result.project_id)
        if project is not None:
            project.directory = result.path
            project.game_adb = result.adb
            project.extra["maa_address"] = result.address
            self.controller.rebind(project.id)
            self.config.save()
            self.tasks.set_maa_ready(result.address)
            if self.selected_id == project.id:
                self._select_project(project.id, force=True)
            self._tick()
        if after is not None:
            if project is not None and self.selected_id != project.id:
                self._select_project(project.id, force=True)
            after()
        else:
            self._info(f"MAA 已就绪：{result.address}")

    def _maa_setup_failed(self, message: str) -> None:
        project = self.config.get(self._maa_setup_project_id)
        if project is not None and project.directory and Path(project.directory).is_dir():
            self.controller.rebind(project.id)
            self.config.save()
        self.tasks.set_maa_failed(message)
        self._error("MAA 准备失败：" + message)

    def _maa_daily(self, adb: str, address: str, stage: str,
                   medicine: int, stone: int, client: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, _adapter = selected
        if project.kind != "maa":
            return
        if runtime.status == "running":
            self._info("MAA 任务正在运行，请先停止")
            return
        if adb:
            project.game_adb = adb
        project.extra["maa_address"] = address or "127.0.0.1:16384"
        project.extra["maa_daily"] = {
            "stage": stage,
            "medicine": int(medicine),
            "stone": int(stone),
            "client": client,
        }
        self.config.save()
        spec = (stage, medicine, stone, client)

        def run_daily() -> None:
            project_now = self.config.get(project.id)
            adapter = self.controller.adapters.get(project.id)
            if project_now is None or adapter is None:
                return
            try:
                daily = adapter.write_daily_task(*spec)
            except Exception as exc:
                self._error(f"生成 MAA 日常任务失败：{exc}")
                return
            self._begin_batch([(daily, "")], project.id)

        self._start_maa_setup(project, address, True, run_daily)

    def _start_next_batch(self) -> None:
        if not self._batch_active:
            return
        if self._batch_index >= len(self._batch_queue):
            count = len(self._batch_queue)
            self._batch_active = False
            self._batch_queue = []
            self._batch_index = 0
            self._batch_project_id = ""
            self._refresh_selected_tasks()
            self._info(f"批量任务完成：{count} 项")
            return
        project = self.config.get(self._batch_project_id)
        if project is None:
            self._batch_active = False
            self._batch_project_id = ""
            return
        runtime = self.controller.runtimes[project.id]
        adapter = self.controller.adapters[project.id]
        task, arg = self._batch_queue[self._batch_index]
        self._batch_index += 1
        try:
            self.controller.service.start_task(runtime, adapter, task, arg)
        except Exception as exc:
            self._batch_active = False
            self._batch_queue = []
            self._batch_project_id = ""
            self._refresh_selected_tasks()
            self._error(f"批量任务停止：{_friendly(exc)}")
            return
        self._after_start(runtime, f"批量 {self._batch_index}/{len(self._batch_queue)}：{task.name}")

    def _start_task(self, task: TaskItem, arg: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        try:
            self.controller.service.start_task(runtime, adapter, task, arg)
        except AdapterError as exc:
            self._error(str(exc))
            return
        except Exception as exc:
            self._error(_friendly(exc))
            return
        self._after_start(runtime, f"任务已启动：{task.name}")

    def _open_command(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        example = ""
        if project.kind == "okscript":
            example = "ok-nte.exe -t DailyRoutineTask -h"
            if adapter.exe_path():
                example = Path(adapter.exe_path()).name + " -t DailyRoutineTask -h"
        elif project.kind == "maa":
            example = "maa fight 1-7"
        dialog = CommandDialog(f"命令行 · {project.game}", example, self)
        if dialog.exec() != QDialog.Accepted or not dialog.value():
            return
        try:
            self.controller.service.start_shell(runtime, adapter, dialog.value())
        except Exception as exc:
            self._error(_friendly(exc))
            return
        self._after_start(runtime, "命令已启动")

    def _open_source_dir(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project = selected[0]
        folder = {"maa": "MAA"}.get(project.id, project.id)
        path = app_dir() / "sources" / folder
        if path.is_dir():
            open_path(str(path))
        else:
            self._info("源码快照未下载；运行 scripts/fetch_sources.py 或查看 sources/README.md")

    def _open_dir(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project = selected[0]
        if project.directory and Path(project.directory).is_dir():
            open_path(project.directory)
        else:
            self._info("目录未设置")

    def _open_repo(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project = selected[0]
        open_url(project.repo_url or project.docs_url)

    def _open_config_dir(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        path = selected[2].config_dir()
        if path is None:
            self._info("未找到配置目录（项目可能尚未运行）")
            return
        open_path(str(path))

    def _open_log_dir(self) -> None:
        files = self._log_files()
        if files:
            open_path(str(files[0].parent))
            return
        selected = self._selected()
        if selected and selected[0].directory:
            open_path(selected[0].directory)

    # ---- 游戏探测 / 启动 -------------------------------------------
    def _connect_mumu(self) -> None:
        maa = self.config.get("maa")
        if maa is None:
            return
        if self._mumu_worker is not None:
            self.install_page.set_mumu_progress(0, "MuMu 正在连接…")
            return
        address = str(maa.extra.get("maa_address", "127.0.0.1:16384"))
        self.install_page.set_mumu_progress(0, "正在启动 MuMu…")
        worker = MumuStartWorker(maa, address, self)
        worker.progress.connect(self.install_page.set_mumu_progress)
        worker.done.connect(self._mumu_connect_done)
        worker.failed.connect(self.install_page.set_mumu_failed)
        worker.finished.connect(self._mumu_finished)
        self._mumu_worker = worker
        worker.start()

    def _mumu_connect_done(self, result) -> None:
        project = self.config.get(result.project_id)
        if project is not None:
            project.game_adb = result.adb
            project.extra["maa_address"] = result.address
            self.config.save()
        self.install_page.set_mumu_ready(result.address)
        self._info(f"MuMu 已连接：{result.address}")

    def _ask_mobile_permission(self, project: Project) -> bool:
        if project.id in self._mobile_permissions:
            return True
        dialog = ConfirmDialog(
            "允许安装到模拟器",
            f"是否允许 {project.game} / {project.name} 把自己安装到 MuMu 模拟器？\n\n"
            "允许后会下载该项目的 APK，并通过 ADB 安装到当前 MuMu。",
            "允许并安装", self,
        )
        if dialog.exec() != QDialog.Accepted:
            return False
        self._mobile_permissions.add(project.id)
        return True

    def _install_mobile_project(self, project: Project) -> None:
        if not self._ask_mobile_permission(project):
            return
        if self._mobile_worker is not None:
            self._info("正在安装手机端项目，请稍候")
            return
        maa = self.config.get("maa")
        if maa is None:
            self._error("未找到 MAA 项目，无法启动 MuMu")
            return
        worker = MobileInstallWorker(project, maa, self.config.github_token, self)
        worker.progress.connect(
            lambda value, message: self.install_page.set_project_progress(project.id, value, message))
        worker.progress.connect(lambda value, message: self.install_page.set_mumu_progress(value, message))
        worker.log.connect(lambda message: None)
        worker.done.connect(self._mobile_done)
        worker.failed.connect(lambda message: self._mobile_failed(project.id, message))
        worker.finished.connect(self._mobile_finished)
        self._mobile_worker = worker
        self.install_page.begin_install([project])
        worker.start()

    def _mobile_finished(self) -> None:
        self._mobile_worker = None

    def _mobile_done(self, result) -> None:
        project = self.config.get(result["project_id"])
        if project is not None:
            project.extra["mobile_installed"] = True
            project.extra["mobile_tag"] = result.get("tag", "")
            self.config.save()
        self.install_page.set_mobile_done(result["project_id"], result.get("tag", ""))
        self.install_page.set_mumu_ready("")
        self._info(f"已安装到 MuMu：{result.get('apk', '')}")

    def _mobile_failed(self, project_id: str, message: str) -> None:
        self.install_page.set_mobile_failed(project_id, message)
        self._error("安装到 MuMu 失败：" + message)

    def _auto_update_check(self) -> None:
        if bool(self.config.ui.get("auto_update", False)):
            self._check_updates(True)

    def _auto_update_changed(self, enabled: bool) -> None:
        self.config.ui["auto_update"] = bool(enabled)
        self.config.save()
        self._info("已开启自动更新原项目" if enabled else "已关闭自动更新原项目")
        if enabled:
            self._check_updates(True)

    def _check_updates(self, auto: bool = False) -> None:
        if self._update_worker is not None:
            return
        if self._install_worker is not None or self._source_worker is not None:
            self._info("安装/源码下载进行中，稍后检查更新")
            return
        projects = [p for p in self.config.projects
                    if p.repo and p.directory and Path(p.directory).is_dir()]
        if not projects:
            self._info("没有可检查更新的已安装项目")
            return
        versions = {p.id: getattr(self.controller.runtimes.get(p.id), "version", "")
                    for p in projects}
        self.install_page.set_update_check_busy(True)
        worker = UpdateCheckWorker(projects, versions, self.config.github_token, self)
        worker.progress.connect(lambda _value, message: self.install_page.overall_label.setText(message))
        worker.done.connect(lambda results: self._updates_checked(results, auto))
        worker.failed.connect(self._updates_failed)
        worker.finished.connect(self._updates_finished)
        self._update_worker = worker
        worker.start()

    def _updates_finished(self) -> None:
        self._update_worker = None
        self.install_page.set_update_check_busy(False)

    def _updates_failed(self, message: str) -> None:
        self._info("检查更新失败：" + message)

    def _updates_checked(self, results, auto: bool = False) -> None:
        updates = []
        errors = []
        for info in list(results or []):
            if getattr(info, "available", False):
                updates.append(info)
                self.install_page.set_update_available(
                    info.project_id, info.latest_tag, info.current)
            else:
                if getattr(info, "error", ""):
                    self.install_page.clear_update(info.project_id)
                else:
                    self.install_page.mark_latest(info.project_id)
            if getattr(info, "error", ""):
                errors.append(f"{info.project_name}: {info.error}")
        if updates:
            names = "、".join(f"{u.project_name} {u.latest_tag}" for u in updates)
            self._info(f"发现 {len(updates)} 个原项目更新：{names}")
        else:
            self._info("已是最新，不更新")
            self.install_page.show_latest()
        if auto and updates:
            projects = [self.config.get(u.project_id) for u in updates]
            projects = [p for p in projects
                        if p is not None and self.controller.runtimes[p.id].status != "running"]
            if projects:
                self._info(f"自动更新 {len(projects)} 个原项目，更新会保留用户配置")
                self._start_inline_install(projects, True)
            else:
                self._info("有更新，但原项目正在运行；请先关闭原项目")
        self.install_page.update_projects(
            self.config.projects, self.controller.runtimes, self.config.install_root_path())
        if errors:
            self._info("部分项目检查失败：" + "；".join(errors[:3]))

    def _scan_games(self, force: bool) -> None:
        if self._game_worker is not None and self._game_worker.isRunning():
            return
        selected = self._selected()
        if selected is not None:
            self.overview.game_detail.setText("正在查找本机游戏…")
        worker = GameScanWorker(self.config.projects, force, self)
        worker.done.connect(self._game_scan_done)
        worker.finished.connect(self._game_scan_finished)
        self._game_worker = worker
        worker.start()

    def _game_scan_finished(self) -> None:
        self._game_worker = None

    def _game_scan_done(self, hits) -> None:
        changed = False
        found = 0
        for project_id, hit in (hits or {}).items():
            project = self.config.get(project_id)
            if project is None or hit is None:
                continue
            found += 1
            if apply_hit(project, hit):
                changed = True
        if changed:
            self.config.save()
        self._refresh_selected()
        self._tick()
        if found:
            self._info(f"已找到 {found} 个游戏 / 模拟器")
        else:
            self._info("未找到本机游戏；可在设置中手动填写游戏 EXE")

    def _launch_game(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, adapter = selected
        if project.kind == "okscript":
            # 原项目启动后会自动执行隐藏的 LauncherTask（ok-nte 源码中 enable_after_start=True）。
            # 启动游戏也走官方 GUI，避免面板自己拼启动参数造成不一致。
            self._open_project_gui()
            self._start_game_display_worker(project)
            return
        if project.kind == "maa":
            # MAA 的任务、干员、关卡和设置都在 MAA 原项目里操作。
            self._open_project_gui()
            return
        if project.kind == "generic":
            # BGI / 三月七助手 / 绝区零一条龙等：打开原项目，具体操作在原项目内。
            self._open_project_gui()
            self._start_game_display_worker(project)
            return
        if runtime.game_running:
            self._start_game_display_worker(project)
            self._info("游戏已经在运行，正在应用显示设置")
            return
        target = actual_game_exe(project) or project.game_exe
        if not target or not Path(target).is_file():
            self._info("未找到游戏/模拟器，请点击“重新查找”或在设置中填写游戏 EXE")
            return
        try:
            subprocess.Popen([target], cwd=game_workdir(project, target))
        except Exception as exc:
            self._error("启动游戏失败：" + _friendly(exc))
            return
        self.controller.history.append(HistoryEntry(
            ts=time.time(), project_id=project.id, project_name=project.name,
            action="启动游戏", detail=target,
        ))
        name = Path(target).name
        self._info(f"正在启动：{name}")
        self._start_game_display_worker(project)
        QTimer.singleShot(2500, lambda: self._verify_game_launch(project, target))

    def _launch_emulator(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        project, runtime, _adapter = selected
        if project.kind != "maa":
            return
        if self._mumu_worker is not None:
            self._info("MuMu 正在启动/等待 ADB，请稍候")
            return
        if self._maa_worker is not None or self._install_worker is not None:
            self._info("其它安装/准备任务运行中，请稍候")
            return
        if runtime.game_running:
            self._info("MuMu 已在运行，检查 ADB…")
        address = self.tasks.maa_address.text().strip() or str(
            project.extra.get("maa_address", "127.0.0.1:16384"))
        self.tasks.set_maa_busy(True)
        self.tasks.set_maa_progress(0, "启动 MuMu…")
        worker = MumuStartWorker(project, address, self)
        worker.progress.connect(self.tasks.set_maa_progress)
        worker.done.connect(self._mumu_started)
        worker.failed.connect(self._mumu_failed)
        worker.finished.connect(self._mumu_finished)
        self._mumu_worker = worker
        worker.start()

    def _mumu_finished(self) -> None:
        self._mumu_worker = None

    def _mumu_started(self, result) -> None:
        project = self.config.get(result.project_id)
        if project is not None:
            project.game_adb = result.adb
            project.extra["maa_address"] = result.address
            self.config.save()
            self._tick()
        self.tasks.set_mumu_ready(result.address)
        self._info(f"MuMu 已启动，ADB：{result.address}")

    def _mumu_failed(self, message: str) -> None:
        self.tasks.set_mumu_failed(message)
        self._error("启动 MuMu 失败：" + message)

    def _verify_game_launch(self, project: Project, target: str) -> None:
        runtime = self.controller.runtimes.get(project.id)
        if runtime is None:
            return
        names = [n.strip().lower() for n in (project.game_process or "").split(";") if n.strip()]
        running = runtime.game_running or any(
            proc.name.lower() in names for proc in self.controller.service.processes()
        )
        if running:
            self.controller.history.append(HistoryEntry(
                ts=time.time(), project_id=project.id, project_name=project.name,
                action="游戏已启动", detail=target,
            ))
            self._refresh_selected()
            self._info(f"游戏已启动：{Path(target).name}")
            return
        launcher = project.game_exe
        if launcher and Path(launcher).is_file() and Path(launcher) != Path(target):
            try:
                subprocess.Popen([launcher], cwd=game_workdir(project, launcher))
            except Exception as exc:
                self._error("启动器启动失败：" + _friendly(exc))
                return
            self.controller.history.append(HistoryEntry(
                ts=time.time(), project_id=project.id, project_name=project.name,
                action="启动器回退", detail=launcher,
            ))
            self._refresh_selected()
            self._info(f"游戏本体未启动，已回退到启动器：{Path(launcher).name}")
            return
        self.controller.history.append(HistoryEntry(
            ts=time.time(), project_id=project.id, project_name=project.name,
            action="启动失败", detail=target,
        ))
        self._refresh_selected()
        self._error(f"游戏进程未出现：{Path(target).name}；请用启动器更新或检查游戏完整性")

    # ---- GitHub ---------------------------------------------------
    def _refresh_github(self, project_id: str, force: bool) -> None:
        project = self.config.get(project_id)
        if project is None or not project.repo:
            return
        runtime = self.controller.runtimes[project_id]
        cached = self.controller.github.cached(project.repo)
        if cached:
            self._apply_github(project_id, cached)
        stale = not cached or time.time() - float(cached.get("fetched_at", 0)) > self.config.github_ttl
        if not (force or stale):
            return
        if project_id in self._github_workers and self._github_workers[project_id].isRunning():
            return
        runtime.github_loading = True
        worker = GithubWorker(self.controller.github, project.repo, self)
        worker.done.connect(self._apply_github)
        worker.finished.connect(lambda pid=project_id: self._github_workers.pop(pid, None))
        self._github_workers[project_id] = worker
        worker.start()

    def _apply_github(self, project_id: str, data: dict) -> None:
        runtime = self.controller.runtimes.get(project_id)
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
            self._refresh_selected()

    # ---- 安装 -----------------------------------------------------
    def _install_from_page(self, project, force: bool) -> None:
        self._install_projects(project.id if project is not None else None, force)

    def _show_install_status(self) -> None:
        dialog = InstallStatusDialog(self.config.projects, self.install_page, self)
        dialog.exec()
        if dialog.open_page:
            self.set_compact(False)
            self._show_global("update")

    def _install_projects(self, project_id: str | None, force: bool = False) -> None:
        if self._install_worker is not None:
            self._info("已有安装任务在进行，请等待完成")
            return
        if self._maa_worker is not None:
            self._info("MAA 正在准备中，请等待完成")
            return
        if self._source_worker is not None:
            self._info("源码正在下载中，请等待完成")
            return
        if project_id:
            projects = [self.config.get(project_id)]
            projects = [p for p in projects if p is not None]
            if projects and projects[0].mobile_only:
                self._install_mobile_project(projects[0])
                return
        else:
            projects = [p for p in self.config.projects
                        if not p.mobile_only
                        and (force or not (p.directory and Path(p.directory).is_dir()))]
        if not projects:
            self._info("没有需要安装的项目")
            return
        if any(self.controller.runtimes[p.id].status == "running" for p in projects):
            self._info("请先停止运行中的项目")
            return
        if project_id and projects[0].directory and Path(projects[0].directory).is_dir() and not force:
            dialog = ConfirmDialog(
                "覆盖安装",
                f"{projects[0].game} 已安装：\n{projects[0].directory}\n\n覆盖会备份旧目录，成功后删除备份。",
                "覆盖安装", self)
            if dialog.exec() != QDialog.Accepted:
                return
            force = True
        self._start_inline_install(projects, force)

    def _start_inline_install(self, projects: list[Project], force: bool = False) -> None:
        self.set_compact(False)
        self._show_global("update")
        self.install_page.begin_install(projects)
        worker = InstallWorker(projects, self.config.install_root_path(),
                               self.config.github_token, force, self)
        worker.project_started.connect(self.install_page.set_project_started)
        worker.project_progress.connect(self.install_page.set_project_progress)
        worker.overall.connect(self.install_page.set_overall)
        worker.project_failed.connect(self.install_page.set_project_failed)
        worker.one_done.connect(self._install_one_done)
        worker.all_finished.connect(self._install_all_finished)
        worker.log.connect(lambda message: None)
        worker.finished.connect(self._install_worker_finished)
        self._install_worker = worker
        worker.start()

    def _install_worker_finished(self) -> None:
        self._install_worker = None

    def _refresh_source_status(self) -> None:
        try:
            self.install_page.set_source_status(source_status())
        except Exception:
            pass

    def _download_sources(self) -> None:
        if self._source_worker is not None:
            self._info("源码正在下载，请等待完成")
            return
        if self._install_worker is not None:
            self._info("项目安装进行中，请稍后下载源码")
            return
        names = list(SOURCE_PROJECTS)
        self.install_page.begin_source()
        worker = SourceFetchWorker(names, self)
        worker.progress.connect(self.install_page.set_source_progress)
        worker.done.connect(self._sources_done)
        worker.failed.connect(self._sources_failed)
        worker.finished.connect(self._source_worker_finished)
        self._source_worker = worker
        worker.start()

    def _source_worker_finished(self) -> None:
        self._source_worker = None

    def _sources_done(self, results) -> None:
        self.install_page.finish_source(True)
        self._refresh_source_status()
        self._info(f"源码下载完成：{len(results or [])} 个项目")

    def _sources_failed(self, message: str) -> None:
        self.install_page.finish_source(False, message)
        self._error("源码下载失败：" + message)

    def _install_one_done(self, result) -> None:
        self._installed_one(result)
        self.install_page.set_project_done(result.project_id, getattr(result, "tag", ""))

    def _install_all_finished(self, success: int, total: int, errors) -> None:
        errors = list(errors or [])
        self.install_page.finish_install(success, total, errors)
        self._tick()
        self._refresh_selected()
        if self.selected_id:
            self._refresh_logs()
        if errors:
            self._error(f"安装完成 {success}/{total}，失败 {len(errors)} 个；可在安装页重试")
        else:
            self._info(f"安装完成：{success} 个项目")
            self._scan_games(True)

    def _installed_one(self, result) -> None:
        project = self.config.get(result.project_id)
        if project is None:
            return
        project.directory = result.path
        info = probe(project, result.path)
        project.kind = info.get("kind", project.kind)
        project.exe = info.get("exe", "")
        project.runner = info.get("runner", "exe")
        self.controller.rebind(project.id)
        if project.kind == "maa":
            self._prepare_maa_files(project)
        self.config.save()
        self._tick()

    def _prepare_maa_files(self, project: Project) -> None:
        """安装后自动生成本地 profile/资源链接，不启动模拟器。"""
        try:
            adapter = self.controller.adapters.get(project.id)
            if adapter is None or not hasattr(adapter, "ensure_profile"):
                return
            address = mumu_address(project)
            if address:
                project.extra["maa_address"] = address
            adapter.ensure_data_layout()
            adapter.ensure_profile(force=True)
        except Exception:
            pass

    # ---- 设置 -----------------------------------------------------
    def _show_global(self, kind: str) -> None:
        self.project_tabs.setVisible(False)
        if kind == "update":
            self.content_stack.setCurrentWidget(self.install_page)
            if self._update_worker is None and self._install_worker is None:
                QTimer.singleShot(120, lambda: self._check_updates(False))
            self.global_update.setStyleSheet("font-size:14px; font-weight:700;")
            self.global_display.setStyleSheet("font-size:14px;")
        else:
            self.content_stack.setCurrentWidget(self.config_page)
            self.global_display.setStyleSheet("font-size:14px; font-weight:700;")
            self.global_update.setStyleSheet("font-size:14px;")
        self.global_update.setVisible(True)
        self.global_display.setVisible(True)

    def _show_project_view(self) -> None:
        self.project_tabs.setVisible(True)
        self.content_stack.setCurrentWidget(self.project_tabs)
        self.global_update.setStyleSheet("font-size:14px;")
        self.global_display.setStyleSheet("font-size:14px;")

    def _set_bureau(self, bureau: str) -> None:
        if bureau not in ("android", "apple"):
            return
        self._bureau = bureau
        for key, button in (("android", self.bureau_android),
                            ("apple", self.bureau_apple), ("pc", self.bureau_pc)):
            button.setStyleSheet("font-weight:700;" if bureau == key else "")
        visible_ids = []
        for project in self.config.projects:
            visible = project.visible_in_bureau(bureau)
            card = self.cards.get(project.id)
            if card is not None:
                card.setVisible(visible)
            row = self.compact_rows.get(project.id)
            if row is not None:
                row.setVisible(visible)
            if visible:
                visible_ids.append(project.id)
        self.install_page.set_bureau(bureau)
        self.side_summary.setText(
            f"{len(visible_ids)} 个项目 · {sum(1 for p in self.config.projects if self.controller.runtimes[p.id].status == 'running')} 运行中"
        )
        if self.selected_id not in visible_ids and visible_ids:
            self._select_project(visible_ids[0], force=True)

    def _add_custom_project(self) -> None:
        dialog = AddProjectDialog(self.config, self)
        if dialog.exec() != QDialog.Accepted or dialog.project is None:
            return
        project = dialog.project
        self.config.add_project(project)
        self.controller.runtimes[project.id] = Runtime(project=project)
        self.controller.adapters[project.id] = make_adapter(project)
        self.controller.load_tasks(project.id)
        self._register_project_ui(project)
        self.config.save()
        self._select_project(project.id, force=True)
        self._tick()
        self._info(f"已添加 {project.game} / {project.name}")

    def _register_project_ui(self, project: Project) -> None:
        if project.id in self.cards:
            return
        card = ProjectCard(project, self.sidebar_cards_parent)
        self.cards[project.id] = card
        card.activated.connect(lambda pid: self._select_project(pid))
        index = self.sidebar_cards_layout.indexOf(self.add_project_btn)
        if index < 0:
            index = self.sidebar_cards_layout.count() - 1
        self.sidebar_cards_layout.insertWidget(index, card)
        row = CompactRow(project, self.compact_rows_parent)
        self.compact_rows[project.id] = row
        row.activated.connect(lambda pid: self._select_project(pid))
        row.detail_requested.connect(self._open_detail)
        row.toggle_requested.connect(self._toggle_project)
        self.compact_rows_layout.insertWidget(self.compact_rows_layout.count() - 1, row)
        self.install_page.add_project(project)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            for project in self.config.projects:
                self.controller.rebind(project.id)
            self.controller.github.token = self.config.github_token
            self._opacity = float(self.config.ui.get("opacity", self._opacity))
            self._close_to_tray = bool(self.config.ui.get("close_to_tray", self._close_to_tray))
            self.setWindowOpacity(self._opacity)
            self.set_topmost(bool(self.config.ui.get("always_on_top", self._topmost)))
            self.config.save()
            self._refresh_selected()
            self._refresh_logs()
            self._tick()
            self._info("设置已保存")

    def _open_project_settings(self) -> None:
        self._open_settings()

    # ---- 进程退出 -------------------------------------------------
    def _exit_on_ui(self, runtime: Runtime, code: int, log_path: str) -> None:
        name = f"{runtime.project.game} · {runtime.project.name}"
        if code == 0:
            self._info(f"{name} 运行结束（退出码 0）")
        else:
            self._error(f"{name} 异常退出，退出码 {code}")
        runtime.history = self.controller.history.recent(runtime.project.id, 6)
        if runtime.project.id == self.selected_id:
            self._refresh_selected()
        self._tick()
        if self._batch_active:
            if code != 0:
                self._batch_active = False
                self._batch_queue = []
                self._batch_index = 0
                self._batch_project_id = ""
                self._refresh_selected_tasks()
                self._error("批量任务已停止：有任务异常退出")
            else:
                QTimer.singleShot(600, self._start_next_batch)

    # ---- 提示 -----------------------------------------------------
    def _info(self, message: str) -> None:
        InfoBar.success("二游面板", message, parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=2600)

    def _error(self, message: str) -> None:
        InfoBar.error("二游面板", message, parent=self,
                      position=InfoBarPosition.TOP_RIGHT, duration=5000)


def _friendly(exc: Exception) -> str:
    if getattr(exc, "winerror", None) == 740 or "WinError 740" in str(exc):
        return "该项目需要管理员权限。请退出面板，右键 EryouPanel.exe → 以管理员身份运行。"
    return f"{type(exc).__name__}: {exc}"


def _github_info(data: dict) -> GithubInfo:
    return GithubInfo(
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


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置")
        self.setMinimumWidth(600)
        self.resize(640, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        title = QLabel("设置", self)
        title.setObjectName("DialogTitle")
        hint = QLabel("所有路径与安装都固定在面板目录；修改后立即写入 panel.json。", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.install_root = LineEdit(self)
        self.install_root.setText(str(config.install_root_path()))
        root_label = QLabel(
            '安装根目录 <span style="font-size:11px; color:#8a8175;">（所有项目的安装地点）</span>',
            self,
        )
        root_label.setTextFormat(Qt.RichText)
        root_label.setStyleSheet("font-size:15px; font-weight:600;")
        form.addRow(root_label, self.install_root)
        self.token = LineEdit(self)
        self.token.setText(config.github_token)
        self.token.setEchoMode(QLineEdit.Password)
        form.addRow("GitHub Token", self.token)
        self.opacity = QSlider(Qt.Horizontal, self)
        self.opacity.setRange(70, 100)
        self.opacity.setValue(int(float(config.ui.get("opacity", 0.97)) * 100))
        form.addRow("窗口不透明度", self.opacity)
        self.top = SwitchButton(self)
        self.top.setText("窗口置顶")
        self.top.setOnText("开")
        self.top.setOffText("关")
        self.top.setChecked(bool(config.ui.get("always_on_top", True)))
        form.addRow("置顶", self.top)
        self.tray = SwitchButton(self)
        self.tray.setText("关闭时隐藏到托盘")
        self.tray.setOnText("开")
        self.tray.setOffText("关")
        self.tray.setChecked(bool(config.ui.get("close_to_tray", True)))
        form.addRow("托盘", self.tray)
        layout.addLayout(form)

        for project in config.projects:
            line = QLabel(f"{project.game} / {project.name}", self)
            line.setObjectName("SectionTitle")
            layout.addWidget(line)
            row = QFormLayout()
            path = LineEdit(self)
            path.setText(project.directory)
            exe = LineEdit(self)
            exe.setText(project.exe)
            row.addRow("目录", path)
            row.addRow("EXE", exe)
            game = LineEdit(self)
            game.setText(project.game_exe)
            game.setPlaceholderText("自动探测；也可手动指定游戏 / 模拟器 EXE")
            row.addRow("游戏 EXE", game)
            layout.addLayout(row)
            setattr(self, f"path_{project.id}", path)
            setattr(self, f"exe_{project.id}", exe)
            setattr(self, f"game_{project.id}", game)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        save = PrimaryPushButton("保存", self)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        footer = QWidget(self)
        footer.setLayout(buttons)
        buttons.setContentsMargins(22, 8, 22, 12)
        outer.addWidget(footer)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)

    def _save(self) -> None:
        self.config.install_root = self.install_root.text().strip()
        self.config.github_token = self.token.text().strip()
        for project in self.config.projects:
            project.directory = getattr(self, f"path_{project.id}").text().strip()
            project.exe = getattr(self, f"exe_{project.id}").text().strip()
            project.game_exe = getattr(self, f"game_{project.id}").text().strip()
            if project.game_exe and Path(project.game_exe).is_file():
                project.game_dir = str(Path(project.game_exe).parent)
                if project.kind == "maa":
                    project.game_kind = "maa" if Path(project.game_exe).name.lower() == "maa.exe" else "emulator"
                else:
                    project.game_kind = project.game_kind or "pc"
            if project.directory and Path(project.directory).is_dir():
                info = probe(project, project.directory)
                project.kind = info.get("kind", project.kind)
                project.exe = project.exe or info.get("exe", "")
                project.runner = info.get("runner", "exe")
        self.config.ui["opacity"] = self.opacity.value() / 100
        self.config.ui["always_on_top"] = self.top.isChecked()
        self.config.ui["close_to_tray"] = self.tray.isChecked()
        self.config.save()
        self.accept()

class AddProjectDialog(QDialog):
    """输入 GitHub 地址，自动解析 owner/repo 并加入项目库。"""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.project: Project | None = None
        self.repo = ""
        self.setWindowTitle("添加二游项目")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("从 GitHub 添加", self)
        title.setObjectName("DialogTitle")
        hint = QLabel("填写 GitHub 仓库地址，面板会解析仓库名、说明和最新 Release；安装时直接使用原项目。", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.url = LineEdit(self)
        self.url.setPlaceholderText("https://github.com/owner/repo")
        self.name = LineEdit(self)
        self.game = LineEdit(self)
        self.platform = ComboBox(self)
        self.platform.addItem("电脑端", userData="pc")
        self.platform.addItem("手机端 · 安卓", userData="android")
        self.platform.addItem("手机端 · 苹果", userData="ios")
        form.addRow("GitHub 地址", self.url)
        form.addRow("项目名称", self.name)
        form.addRow("游戏名称", self.game)
        form.addRow("运行平台", self.platform)
        layout.addLayout(form)
        self.status = QLabel("等待解析", self)
        self.status.setObjectName("TinyLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        self.parse_btn = PushButton(FluentIcon.SYNC, "解析", self)
        self.add_btn = PrimaryPushButton(FluentIcon.ADD, "添加", self)
        self.add_btn.setEnabled(False)
        buttons.addWidget(cancel)
        buttons.addWidget(self.parse_btn)
        buttons.addWidget(self.add_btn)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        self.parse_btn.clicked.connect(self._parse)
        self.add_btn.clicked.connect(self._add)

    def _parse(self) -> None:
        text = self.url.text().strip()
        match = re.match(r"^(?:https?://(?:www\.)?github\.com/)?([^/\s]+)/([^/\s#?]+)", text, re.I)
        if not match:
            self.status.setText("地址格式应为 https://github.com/owner/repo")
            return
        owner = match.group(1)
        repo_name = match.group(2).removesuffix(".git")
        self.repo = f"{owner}/{repo_name}"
        self.status.setText("正在从 GitHub 解析…")
        try:
            import requests
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "eryou-panel",
                       "Cache-Control": "no-cache", "Pragma": "no-cache"}
            if self.config.github_token:
                headers["Authorization"] = f"Bearer {self.config.github_token}"
            response = requests.get(f"https://api.github.com/repos/{self.repo}",
                                    headers=headers, timeout=25)
            if response.status_code != 200:
                self.status.setText(f"GitHub 解析失败：HTTP {response.status_code}")
                return
            data = response.json()
            if not self.name.text().strip():
                self.name.setText(str(data.get("name", repo_name)))
            if not self.game.text().strip():
                self.game.setText(str(data.get("name", repo_name)))
            description = str(data.get("description", "") or "").strip()
            try:
                release = latest_release(self.repo, self.config.github_token)
                tag = str(release.get("tag_name", "") or "")
                assets = len(release.get("assets", []) or [])
                release_text = f"最新 Release {tag}，{assets} 个资产" if tag else "暂无 Release"
            except Exception as exc:
                release_text = f"Release 暂不可用：{exc}"
            self.status.setText(f"已解析 {self.repo} · {description[:80]} · {release_text}")
            self.add_btn.setEnabled(True)
        except Exception as exc:
            self.status.setText(f"解析失败：{exc}")

    def _add(self) -> None:
        if not self.repo:
            self.status.setText("请先解析 GitHub 地址")
            return
        slug = re.sub(r"[^a-z0-9._-]+", "-", self.repo.replace("/", "-").lower()).strip("-")
        existing = {p.id for p in self.config.projects}
        project_id = slug or "custom-project"
        index = 2
        while project_id in existing:
            project_id = f"{slug}-{index}"
            index += 1
        game = self.game.text().strip() or self.repo.split("/")[-1]
        name = self.name.text().strip() or self.repo.split("/")[-1]
        release_url = f"https://github.com/{self.repo}"
        platform = str(self.platform.currentData() or "pc")
        self.project = Project(
            id=project_id, game=game, name=name, repo=self.repo,
            kind="generic", game_kind="pc" if platform == "pc" else "emulator",
            platforms=[platform], accent="#b08d57",
            notes="通过 GitHub 地址添加的自定义原项目；任务和配置在原项目内完成。",
            git_url=release_url, docs_url=release_url,
            extra={"allow_any_exe": True, "asset_any": True,
                   "custom": True, "mobile_only": platform != "pc"},
        )
        self.accept()

