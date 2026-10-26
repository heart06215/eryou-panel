"""GUI 入口：创建 QApplication 与悬浮窗。"""
from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ..config import Config
from .theme import apply_theme
from .window import FloatingPanel


def hide_console() -> None:
    """双击启动时隐藏控制台；从终端/脚本启动时保留，避免遮住父窗口。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.GetConsoleProcessList.argtypes = [
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        pids = (ctypes.c_ulong * 16)()
        count = kernel32.GetConsoleProcessList(pids, 16)
        shells = {"powershell.exe", "pwsh.exe", "cmd.exe", "windowsterminal.exe",
                  "openconsole.exe", "bash.exe", "wt.exe", "conhost.exe"}
        try:
            from ..winproc import process_path

            for index in range(max(0, min(count, 16))):
                name = os.path.basename(process_path(int(pids[index]))).lower()
                if name in shells:
                    return
        except Exception:
            pass
        if 0 < count <= 2:
            user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def run_gui(config: Config) -> int:
    hide_console()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("二游面板")
    app.setOrganizationName("EryouPanel")
    apply_theme(app)
    window = FloatingPanel(config)
    app.setQuitOnLastWindowClosed(not window._tray_available)
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()
