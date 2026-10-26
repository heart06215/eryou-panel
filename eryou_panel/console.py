"""从 EXE 启动时准备 Windows 控制台：UTF-8、VT、合适的窗口尺寸。"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", wintypes.SHORT),
        ("Top", wintypes.SHORT),
        ("Right", wintypes.SHORT),
        ("Bottom", wintypes.SHORT),
    ]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


def prepare_console() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleTitleW("ERYOU PANEL")
        handle = kernel32.GetStdHandle(-11)
        if not handle or handle == -1:
            return
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return
        cols = info.srWindow.Right - info.srWindow.Left + 1
        rows = info.srWindow.Bottom - info.srWindow.Top + 1
        if cols >= 108 and rows >= 32:
            return
        target_cols = max(cols, 118)
        target_rows = max(rows, 34)
        buffer = COORD(target_cols, max(info.dwSize.Y, target_rows + 300))
        kernel32.SetConsoleScreenBufferSize(handle, buffer)
        window = SMALL_RECT(0, 0, target_cols - 1, target_rows - 1)
        kernel32.SetConsoleWindowInfo(handle, True, ctypes.byref(window))
    except Exception:
        # 控制台 API 在某些终端（如 Windows Terminal）下可能失败，忽略即可
        pass


def prepare_cli() -> None:
    """CLI 模式只设置 UTF-8，不改变用户终端窗口大小。"""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
