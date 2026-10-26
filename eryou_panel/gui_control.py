"""在原项目 GUI 中排队任务，并尽量把原窗口最小化。

ok-script 的一次性任务卡片每个都有 Start 按钮；原项目自身会把后点击的任务
排入队列（StartController 支持 current_task 队列）。因此面板只负责：
1. 启动官方 GUI；
2. 最小化 GUI；
3. 按顺序点击选中任务的 Start。
不修改原项目，也不伪造任务状态。
"""
from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from typing import Callable

Log = Callable[[str], None]
Progress = Callable[[int, str], None]

START_RE = re.compile(r"(start|begin|开始|启动)", re.I)


def minimize_windows_for_pid(pid: int) -> int:
    """Win32 兜底最小化：即使 UIA 不可用也不让原窗口挡住面板。"""
    if not pid:
        return 0
    user32 = ctypes.windll.user32
    found = 0
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        nonlocal found
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            found += 1
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def _find_window(pid: int, title_hint: str = "", timeout: float = 60.0):
    from pywinauto import Desktop

    deadline = time.time() + timeout
    desktop = Desktop(backend="uia")
    while time.time() < deadline:
        minimize_windows_for_pid(pid)
        for window in desktop.windows():
            try:
                if int(window.process_id()) == int(pid):
                    return window
                if title_hint and title_hint.lower() in (window.window_text() or "").lower():
                    return window
            except Exception:
                continue
        time.sleep(1.0)
    return None


def _start_buttons(container):
    try:
        return [
            button for button in container.descendants(control_type="Button")
            if START_RE.match((button.window_text() or "").strip())
        ]
    except Exception:
        return []


def _find_task_card(window, names: list[str]):
    """按 UIA 树顺序选择任务文本之后的第一个 Start。

    窗口最小化后 rectangle() 会返回无效坐标，不能用屏幕位置；Qt 的
    TaskCard 在 UIA 树里通常是“任务标题 -> Start 按钮”的文档顺序。
    """
    try:
        controls = list(window.descendants())
    except Exception:
        return None
    starts = [
        (index, control) for index, control in enumerate(controls)
        if control.element_info.control_type == "Button"
        and START_RE.search((control.window_text() or "").strip())
    ]
    lowered = [name.lower() for name in names if name]
    for index, control in enumerate(controls):
        if control.element_info.control_type != "Text":
            continue
        text = (control.window_text() or "").strip().lower()
        if not text or not any(low == text or low in text for low in lowered):
            continue
        for start_index, button in starts:
            if start_index > index:
                return button
        for start_index, button in reversed(starts):
            if start_index < index:
                return button
    return None


def queue_tasks_in_gui(pid: int, names: list[list[str]] | list[str], log: Log | None = None,
                       progress: Progress | None = None, title_hint: str = "") -> list[str]:
    """把任务依次交给原项目 GUI 的 Start 按钮（原项目负责排队/执行）。"""
    if not pid:
        raise RuntimeError("原项目 GUI 没有可用的进程 PID")
    if names and isinstance(names[0], str):
        names = [[str(item)] for item in names]
    window = _find_window(pid, title_hint)
    if window is None:
        raise RuntimeError("没有找到原项目 GUI 窗口，请先启动原项目")
    try:
        window.minimize()
    except Exception:
        minimize_windows_for_pid(pid)
    started: list[str] = []
    for index, candidates in enumerate(names):
        label = candidates[0]
        if progress:
            progress(index, f"原项目：点击 {label}")
        button = _find_task_card(window, candidates)
        if button is None:
            # 少量项目会把任务名翻译到界面；仅记录，不伪造执行。
            if log:
                log(f"未找到任务按钮：{label}")
            continue
        try:
            button.invoke()
        except Exception:
            try:
                button.click_input()
            except Exception as exc:
                if log:
                    log(f"点击任务失败 {label}: {exc}")
                continue
        started.append(label)
        time.sleep(0.6)
    try:
        window.minimize()
    except Exception:
        minimize_windows_for_pid(pid)
    if progress:
        progress(len(names), f"已交给原项目 {len(started)}/{len(names)} 个任务")
    return started
