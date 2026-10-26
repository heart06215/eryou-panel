"""GUI 后台线程：安装与 GitHub 同步。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..installer import install_project
from ..maa_support import prepare_maa, start_mumu
from ..models import Project
from ..source_fetch import fetch_sources
from ..gui_control import queue_tasks_in_gui
from ..services import GithubService
from ..games import detect_games


class InstallWorker(QThread):
    progress = Signal(float, str)
    log = Signal(str)
    one_done = Signal(object)
    failed = Signal(str)
    finished_all = Signal()
    project_started = Signal(str, str)
    project_progress = Signal(str, float, str)
    project_failed = Signal(str, str)
    overall = Signal(float, str)
    all_finished = Signal(int, int, object)

    def __init__(self, projects: list[Project], root, token: str = "", force: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.root = root
        self.token = token
        self.force = force

    def run(self) -> None:
        total = max(1, len(self.projects))
        failures: list[str] = []
        for index, project in enumerate(self.projects):
            self.project_started.emit(project.id, project.name)
            self.project_progress.emit(project.id, 0.0, "准备安装…")
            self.overall.emit(index / total * 100, f"({index + 1}/{total}) 准备 {project.game}")

            def report(value: float, message: str, pid=project.id, idx=index,
                       game=project.game) -> None:
                self.project_progress.emit(pid, value, message)
                self.progress.emit(value, message)
                self.overall.emit(
                    (idx + max(0.0, min(100.0, value)) / 100.0) / total * 100.0,
                    f"({idx + 1}/{total}) {game} · {message}",
                )

            try:
                result = install_project(
                    project,
                    self.root,
                    force=self.force,
                    token=self.token,
                    progress=report,
                    log=lambda message: self.log.emit(message),
                )
            except Exception as exc:
                failures.append(f"{project.game}: {exc}")
                self.project_failed.emit(project.id, str(exc))
                self.failed.emit(str(exc))
                self.overall.emit((index + 1) / total * 100, f"({index + 1}/{total}) {project.game} 失败")
                continue
            self.one_done.emit(result)
            self.project_progress.emit(project.id, 100.0, "安装完成")
            self.overall.emit((index + 1) / total * 100, f"({index + 1}/{total}) {project.game} 完成")
        self.all_finished.emit(total - len(failures), total, failures)
        self.finished_all.emit()


class MumuStartWorker(QThread):
    progress = Signal(float, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, preferred_address: str = "", parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.preferred_address = preferred_address
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = start_mumu(
                self.project,
                preferred_address=self.preferred_address,
                progress=lambda value, message: self.progress.emit(value, message),
                log=lambda message: self.log.emit(message),
                cancel=lambda: self._cancel,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class GuiQueueWorker(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, pid: int, names: list[list[str]] | list[str], title_hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self.pid = pid
        self.names = names
        self.title_hint = title_hint

    def run(self) -> None:
        try:
            started = queue_tasks_in_gui(
                self.pid,
                self.names,
                log=lambda message: self.log.emit(message),
                progress=lambda value, message: self.progress.emit(value, message),
                title_hint=self.title_hint,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(started)


class GameDisplayWorker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, display_key: str, background: bool = False,
                 timeout: int = 120, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.display_key = display_key
        self.background = background
        self.timeout = timeout

    def run(self) -> None:
        import time
        from ..display_control import (apply_game_window, find_game_windows,
                                       list_displays, resolve_display)
        try:
            displays = list_displays()
            display = resolve_display(self.display_key, displays)
            if display is None:
                self.failed.emit("没有检测到可用显示屏")
                return
            deadline = time.time() + max(10, self.timeout)
            while time.time() < deadline:
                windows = find_game_windows(self.project)
                if windows:
                    result = apply_game_window(windows[0], display, self.background)
                    self.done.emit(result)
                    return
                self.progress.emit(0, "等待游戏窗口出现…")
                time.sleep(1.0)
            self.failed.emit("等待游戏窗口超时")
        except Exception as exc:
            self.failed.emit(str(exc))

class UpdateCheckWorker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, projects: list[Project], versions: dict, token: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.versions = versions
        self.token = token

    def run(self) -> None:
        from ..updates import check_project_update
        try:
            results = []
            for index, project in enumerate(self.projects):
                self.progress.emit(index, f"检查更新：{project.name}")
                results.append(check_project_update(
                    project, str(self.versions.get(project.id, "") or ""), self.token))
            self.done.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))

class MobileInstallWorker(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, maa_project: Project, token: str = "", parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.maa_project = maa_project
        self.token = token

    def run(self) -> None:
        import shutil
        from ..mobile_install import prepare_apk, install_apk
        temp = None
        try:
            self.progress.emit(5, "获取 GitHub 最新 APK…")
            apk, tag, temp = prepare_apk(self.project, self.token, self.log.emit)
            self.progress.emit(55, "APK 已下载，连接 MuMu…")
            result = install_apk(self.maa_project, apk, self.log.emit)
            self.progress.emit(100, "已安装到 MuMu")
            self.done.emit({"project_id": self.project.id, "tag": tag,
                            "package": result, "apk": apk.name})
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if temp is not None:
                shutil.rmtree(temp, ignore_errors=True)

class SourceFetchWorker(QThread):
    progress = Signal(float, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, names: list[str], parent=None) -> None:
        super().__init__(parent)
        self.names = names
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            results = fetch_sources(
                self.names,
                progress=lambda value, message: self.progress.emit(value, message),
                log=lambda message: self.log.emit(message),
                cancel=lambda: self._cancel,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(results)


class MaaSetupWorker(QThread):
    progress = Signal(float, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, root, token: str = "", preferred_address: str = "",
                 launch_emulator: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.root = root
        self.token = token
        self.preferred_address = preferred_address
        self.launch_emulator = launch_emulator
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = prepare_maa(
                self.project,
                self.root,
                token=self.token,
                preferred_address=self.preferred_address,
                launch_emulator=self.launch_emulator,
                progress=lambda value, message: self.progress.emit(value, message),
                log=lambda message: self.log.emit(message),
                cancel=lambda: self._cancel,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class GithubWorker(QThread):
    done = Signal(str, dict)

    def __init__(self, service: GithubService, repo: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.repo = repo

    def run(self) -> None:
        self.done.emit(self.repo, self.service.fetch(self.repo))


class GameScanWorker(QThread):
    done = Signal(object)

    def __init__(self, projects, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.force = force

    def run(self) -> None:
        self.done.emit(detect_games(self.projects, force=self.force))
