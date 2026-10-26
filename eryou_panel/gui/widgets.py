"""悬浮窗基础组件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    FluentIcon,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SwitchButton,
    TransparentToolButton,
)

from ..models import Project, Runtime, TaskItem
from ..util import human_duration
from .theme import (
    ERROR,
    FAINT,
    GOLD,
    GOLD_DEEP,
    INK,
    MUTED,
    OK,
    WARN,
)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, object_name: str = "Card") -> None:
        super().__init__(parent)
        self.setObjectName(object_name)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")


class Pill(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusPill")
        self.setProperty("state", "stopped")
        self.setAlignment(Qt.AlignCenter)

    def set_state(self, state: str, text: str) -> None:
        self.setText(text)
        self.setProperty("state", state)
        self.setMinimumWidth(54)
        self.setAlignment(Qt.AlignCenter)
        self.style().unpolish(self)
        self.style().polish(self)


def icon_button(icon: FluentIcon, tooltip: str, parent: QWidget | None = None) -> TransparentToolButton:
    button = TransparentToolButton(icon.icon(), parent)
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(30, 30)
    button.setToolTip(tooltip)
    return button


class TitleBar(QWidget):
    pin_toggled = Signal(bool)
    compact_toggled = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(48)
        self._pinned = True
        self._compact = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(6)

        brand = QLabel("◆  二游面板", self)
        brand.setObjectName("Brand")
        self.subtitle = QLabel("", self)
        self.subtitle.setObjectName("Subtitle")
        layout.addWidget(brand)
        layout.addSpacing(8)
        layout.addWidget(self.subtitle)
        layout.addStretch(1)

        self.pin_button = icon_button(FluentIcon.PIN, "取消置顶", self)
        self.compact_button = icon_button(FluentIcon.CARE_DOWN_SOLID, "展开详细面板", self)
        self.settings_button = icon_button(FluentIcon.SETTING, "设置", self)
        self.min_button = icon_button(FluentIcon.MINIMIZE, "最小化", self)
        self.close_button = icon_button(FluentIcon.CLOSE, "隐藏到托盘", self)
        for button in (self.pin_button, self.compact_button, self.settings_button,
                       self.min_button, self.close_button):
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(19, 19))
            layout.addWidget(button)

        self.pin_button.clicked.connect(self._on_pin)
        self.compact_button.clicked.connect(self.compact_toggled.emit)
        self.settings_button.clicked.connect(self.settings_clicked.emit)
        self.min_button.clicked.connect(self.minimize_clicked.emit)
        self.close_button.clicked.connect(self.close_clicked.emit)

    def _on_pin(self) -> None:
        self.set_pinned(not self._pinned)
        self.pin_toggled.emit(self._pinned)

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_button.setIcon((FluentIcon.PIN if pinned else FluentIcon.UNPIN).icon())
        self.pin_button.setToolTip("取消置顶" if pinned else "窗口置顶")

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.compact_button.setIcon(
            (FluentIcon.CARE_DOWN_SOLID if compact else FluentIcon.CARE_UP_SOLID).icon()
        )
        self.compact_button.setToolTip("展开详细面板" if compact else "收起为悬浮窗")

    def set_summary(self, text: str) -> None:
        self.subtitle.setText(text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.compact_toggled.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MetricLabel(QLabel):
    def __init__(self, text: str = "", strong: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("MetricStrong" if strong else "Metric")


class CompactRow(QFrame):
    activated = Signal(str)
    toggle_requested = Signal(str)
    detail_requested = Signal(str)

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CompactRow")
        self.project = project
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(70)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.game = QLabel(project.game, self)
        self.game.setObjectName("GameName")
        self.name = QLabel(project.name, self)
        self.name.setObjectName("ProjectName")
        top.addWidget(self.game)
        top.addWidget(self.name)
        top.addStretch(1)
        self.status = Pill("未安装", self)
        top.addWidget(self.status)
        left.addLayout(top)

        info = QHBoxLayout()
        info.setSpacing(12)
        self.metric_label = MetricLabel("", strong=True, parent=self)
        self.task_label = MetricLabel("", parent=self)
        info.addWidget(self.metric_label)
        info.addWidget(self.task_label)
        info.addStretch(1)
        self.metric_label.setVisible(False)
        self.task_label.setText(project.platform_text)
        self.task_label.setVisible(True)
        left.addLayout(info)
        layout.addLayout(left, 1)

        self.action = icon_button(FluentIcon.PLAY, "启动", self)
        self.detail = icon_button(FluentIcon.CHEVRON_RIGHT, "打开详情", self)
        layout.addWidget(self.action)
        layout.addWidget(self.detail)
        self.action.clicked.connect(lambda: self.toggle_requested.emit(self.project.id))
        self.detail.clicked.connect(lambda: self.detail_requested.emit(self.project.id))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.project.id)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def update_runtime(self, runtime: Runtime, adapter_mode: str) -> None:
        version = f"v{runtime.version}" if runtime.version else ""
        if not self.project.directory:
            self.status.set_state("stopped", "未安装")
            self.metric_label.setText("—")
            self.task_label.setText(self.project.platform_text)
            self.action.setIcon(FluentIcon.DOWNLOAD.icon())
            self.action.setToolTip("安装")
            return
        if runtime.status == "running":
            self.status.set_state("running", "运行中")
            self.metric_label.setText(
                f"PID {runtime.pid}   CPU {runtime.cpu:4.1f}%   MEM {runtime.mem_mb:.0f} MB")
            self.task_label.setText(self.project.platform_text)
            self.action.setIcon(FluentIcon.PAUSE.icon())
            self.action.setToolTip("停止")
        elif runtime.exit_code not in (None, 0):
            self.status.set_state("error", f"退出 {runtime.exit_code}")
            self.metric_label.setText("—")
            self.task_label.setText(runtime.last_action or "运行失败")
            self.action.setIcon(FluentIcon.PLAY.icon())
            self.action.setToolTip("启动")
        else:
            self.status.set_state("stopped", "已停止")
            self.metric_label.setText("CPU —   MEM —")
            self.task_label.setText(self.project.platform_text)
            self.action.setIcon(FluentIcon.PLAY.icon())
            self.action.setToolTip("启动")


class ProjectCard(QFrame):
    activated = Signal(str)

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.project = project
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(78)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(7)
        self.game = QLabel(project.game, self)
        self.game.setObjectName("GameName")
        self.name = QLabel(project.name, self)
        self.name.setObjectName("ProjectName")
        self.name.setStyleSheet("font-size:16px; font-weight:700;")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setMinimumWidth(0)
        self.name.setToolTip(project.name)
        self.game.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        top.addWidget(self.game)
        top.addWidget(self.name, 1)
        self.status = Pill("未安装", self)
        top.addWidget(self.status)
        layout.addLayout(top)
        self.platform_label = QLabel(project.platform_text, self)
        self.platform_label.setObjectName("TinyLabel")
        layout.addWidget(self.platform_label)
        self.metric_label = MetricLabel("", strong=True, parent=self)
        self.metric_label.setVisible(False)
        # 项目名只显示一次；不再重复路径、仓库和版本名。
        self.path = QLabel("", self)
        self.path.setVisible(False)
        self.repo = QLabel("", self)
        self.repo.setVisible(False)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.project.id)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def update_runtime(self, runtime: Runtime, adapter_mode: str) -> None:
        version = f"v{runtime.version}" if runtime.version else ""
        if self.project.mobile_only and self.project.extra.get("mobile_installed") and not self.project.directory:
            self.status.set_state("running", "已安装")
            self.metric_label.setText("手机端 · MuMu")
            return
        if not self.project.directory:
            self.status.set_state("stopped", "未安装")
            self.metric_label.setText(adapter_mode)
            return
        if runtime.status == "running":
            self.status.set_state("running", "运行中")
            self.metric_label.setText(
                f"CPU {runtime.cpu:4.1f}%    MEM {runtime.mem_mb:.0f} MB    {human_duration(runtime.uptime())}")
        elif runtime.exit_code not in (None, 0):
            self.status.set_state("error", f"退出 {runtime.exit_code}")
            self.metric_label.setText(runtime.last_action or "运行失败")
        else:
            self.status.set_state("stopped", "已停止")
            self.metric_label.setText("  ·  ".join(x for x in (adapter_mode, version) if x))


def _compact_path(path: str, limit: int = 30) -> str:
    if not path:
        return ""
    try:
        from pathlib import Path

        from ..util import app_dir

        p = Path(path)
        try:
            if p.resolve().parent == app_dir().resolve():
                return p.name
        except OSError:
            pass
        parts = p.parts
        compact = "\\".join(parts[-2:]) if len(parts) >= 2 else path
    except Exception:
        compact = path
    if len(compact) > limit:
        compact = "…" + compact[-(limit - 1):]
    return compact


class StatCard(Card):
    def __init__(self, caption: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(74)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.value = QLabel(value, self)
        self.value.setObjectName("StatValue")
        label = QLabel(caption, self)
        label.setObjectName("StatLabel")
        layout.addWidget(self.value)
        layout.addWidget(label)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


def _elide_path(path: str, limit: int) -> str:
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


class ConfirmDialog(QDialog):
    def __init__(self, title: str, body: str, confirm: str = "确定",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        heading = QLabel(title, self)
        heading.setObjectName("DialogTitle")
        text = QLabel(body, self)
        text.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(text)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        ok = PrimaryPushButton(confirm, self)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)


class ArgDialog(QDialog):
    def __init__(self, title: str, prompt: str, default: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        heading = QLabel(title, self)
        heading.setObjectName("DialogTitle")
        hint = QLabel(prompt or "参数（可留空）", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        self.input = QLineEdit(default, self)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addWidget(self.input)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        ok = PrimaryPushButton("运行", self)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.input.returnPressed.connect(self.accept)
        self.input.setFocus()

    def value(self) -> str:
        return self.input.text()


class CommandDialog(QDialog):
    def __init__(self, title: str, example: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        heading = QLabel("命令行", self)
        heading.setObjectName("DialogTitle")
        hint = QLabel("在项目目录中通过系统 shell 执行，输出进入日志页。", self)
        hint.setObjectName("DialogHint")
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(example or "例如：ok-nte.exe -t DailyRoutineTask -h")
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addWidget(self.input)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        ok = PrimaryPushButton("执行", self)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.input.returnPressed.connect(self.accept)
        self.input.setFocus()

    def value(self) -> str:
        return self.input.text().strip()


class InstallDialog(QDialog):
    """带进度与日志的安装窗口。"""
    progress = Signal(float, str)
    log = Signal(str)
    one_done = Signal(object)
    failed = Signal(str)
    finished_all = Signal()

    def __init__(self, projects: list[Project], root, token: str = "", force: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.root = root
        self.token = token
        self.force = force
        self.results: list = []
        self.errors: list[str] = []
        self._busy = False
        self._started = False
        self.setWindowTitle("安装")
        self.setMinimumSize(720, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        heading = QLabel("安装 · " + "、".join(p.game for p in self.projects), self)
        heading.setObjectName("DialogTitle")
        self.path_label = QLabel(self)
        self.path_label.setObjectName("DialogHint")
        self.path_label.setWordWrap(True)
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setValue(0)
        self.status = QLabel("准备下载…", self)
        self.status.setObjectName("DialogHint")
        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = PushButton("关闭", self)
        self.start_button = PrimaryPushButton("开始安装", self)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)
        layout.addWidget(heading)
        layout.addWidget(self.path_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status)
        layout.addWidget(self.log_view, 1)
        layout.addLayout(buttons)

        lines = [f"安装根目录：{root}", "下载与解压均在该目录内完成，不写入其它位置。"]
        for project in projects:
            lines.append(f"{project.game} / {project.name} → {root / _install_name(project)}")
        if force:
            lines.append("覆盖模式：旧目录会先备份，成功后删除备份。")
        self.path_label.setText("\n".join(lines))
        self.close_button.clicked.connect(self._close)
        self.start_button.clicked.connect(self.start)
        self.progress.connect(self._on_progress)
        self.log.connect(self._append_log)
        self.one_done.connect(self._on_one_done)
        self.failed.connect(self._on_failed)
        self.finished_all.connect(self._on_finished)
        # 打开即开始
        from PySide6.QtCore import QTimer

        QTimer.singleShot(350, self.start)

    def start(self) -> None:
        if self._busy or self._started:
            return
        self._started = True
        self._busy = True
        self.start_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.log_view.clear()
        self._append_log("开始安装…")
        from .workers import InstallWorker

        self.worker = InstallWorker(self.projects, self.root, self.token, self.force, self)
        self.worker.progress.connect(self.progress.emit)
        self.worker.log.connect(self.log.emit)
        self.worker.one_done.connect(self.one_done.emit)
        self.worker.failed.connect(self.failed.emit)
        self.worker.finished_all.connect(self.finished_all.emit)
        self.worker.start()

    def _on_progress(self, value: float, message: str) -> None:
        self.progress_bar.setValue(int(value))
        self.status.setText(f"[{value:5.1f}%] {message}")

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _on_one_done(self, result) -> None:
        self.results.append(result)
        self._append_log(f"完成：{result.project_name} {result.tag} → {result.path}")

    def _on_failed(self, message: str) -> None:
        self.errors.append(message)
        self._append_log("错误：" + message)
        self.status.setText("安装失败")

    def _on_finished(self) -> None:
        self._busy = False
        self.close_button.setEnabled(True)
        if self.errors:
            self.start_button.setEnabled(False)
            self.status.setText("安装结束，存在失败项；关闭后重新安装")
        else:
            self.status.setText("全部安装完成")
            self.start_button.setVisible(False)
        self.close_button.setFocus()

    def _close(self) -> None:
        if self._busy:
            return
        self.accept()

    def reject(self) -> None:
        if self._busy:
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)


def _install_name(project: Project) -> str:
    from ..installer import install_name

    return install_name(project)


class InstallStatusDialog(QDialog):
    """左下角“安装情况”小窗：查看每个项目的安装状态与进度。"""

    def __init__(self, projects: list[Project], install_page, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.install_page = install_page
        self.open_page = False
        self._rows: dict[str, tuple[QLabel, QLabel, ProgressBar]] = {}
        self.setWindowTitle("安装情况")
        self.setMinimumSize(540, 380)
        self.setMaximumWidth(640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        heading = QLabel("安装情况", self)
        heading.setObjectName("DialogTitle")
        layout.addWidget(heading)
        self.summary = QLabel("", self)
        self.summary.setObjectName("DialogHint")
        layout.addWidget(self.summary)

        for project in projects:
            card = Card(self)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 10, 14, 10)
            row.setSpacing(10)
            left = QVBoxLayout()
            left.setSpacing(3)
            name = QLabel(f"{project.game}  ·  {project.name}", card)
            name.setObjectName("GameName")
            path = QLabel(project.directory or "未设置安装目录", card)
            path.setObjectName("TinyLabel")
            path.setWordWrap(True)
            status = QLabel("", card)
            status.setObjectName("Metric")
            bar = ProgressBar(card)
            bar.setValue(0)
            bar.setFixedHeight(8)
            left.addWidget(name)
            left.addWidget(path)
            left.addWidget(status)
            left.addWidget(bar)
            row.addLayout(left, 1)
            layout.addWidget(card)
            self._rows[project.id] = (path, status, bar)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = PushButton("关闭", self)
        self.open_btn = PrimaryPushButton("打开安装页", self)
        self.close_btn.clicked.connect(self.reject)
        self.open_btn.clicked.connect(self._open_install_page)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.open_btn)
        layout.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        self._refresh()

    def _open_install_page(self) -> None:
        self.open_page = True
        self.accept()

    def _refresh(self) -> None:
        installed = 0
        active = set(getattr(self.install_page, "_active_ids", set()))
        errors = getattr(self.install_page, "_last_errors", {})
        page_rows = getattr(self.install_page, "rows", {})
        for project in self.projects:
            row = self._rows.get(project.id)
            if row is None:
                continue
            path, status, bar = row
            path.setText(project.directory or "未设置安装目录")
            is_installed = bool(project.directory and Path(project.directory).is_dir())
            if is_installed:
                installed += 1
            source = page_rows.get(project.id)
            if source is not None:
                bar.setValue(source[3].value())
                text = source[1].text()
            else:
                bar.setValue(100 if is_installed else 0)
                text = "已安装" if is_installed else "未安装"
            if project.id in errors:
                status.setStyleSheet(f"color: {ERROR};")
                status.setText(text or "安装失败")
            elif project.id in active:
                status.setStyleSheet(f"color: {GOLD_DEEP};")
                status.setText(text or "安装中…")
            else:
                status.setStyleSheet(f"color: {OK};" if is_installed else "")
                status.setText(text or ("已安装" if is_installed else "未安装"))
        total = len(self.projects)
        if active:
            summary = f"{total} 个项目 · 正在安装 {len(active)} 个"
        else:
            summary = f"{total} 个项目 · {installed} 已安装"
            if installed == total:
                summary += " · 全部就绪"
            else:
                summary += f" · {total - installed} 个未安装"
        self.summary.setText(summary)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)

