"""本地显示屏检测，以及把游戏窗口移动到指定屏幕。

这里只移动游戏窗口，不切换主屏、不关闭显示器、不改显示拓扑。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterable

from .models import Project
from .winproc import list_processes

IS_WINDOWS = __import__("os").name == "nt"

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
else:  # pragma: no cover
    _user32 = None  # type: ignore[assignment]

_INTERNAL_VENDORS = {
    "AUO", "BOE", "CMN", "CSO", "IVO", "LGD", "LEN", "NCP", "PAN", "SDC", "SHP",
}

_SW_MINIMIZE = 6
_SW_RESTORE = 9
_SW_SHOW = 5
_GWL_STYLE = -16
_WS_POPUP = 0x80000000
_WS_OVERLAPPEDWINDOW = 0x00CF0000
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


@dataclass(frozen=True)
class DisplayInfo:
    device: str
    monitor_id: str
    name: str
    vendor: str
    internal: bool
    primary: bool
    x: int
    y: int
    width: int
    height: int

    @property
    def key(self) -> str:
        return self.device

    @property
    def label(self) -> str:
        suffix = " · 主屏" if self.primary else ""
        return f"{self.name} · {self.width}×{self.height}{suffix}"


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    pid: int
    process: str
    title: str
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


@dataclass(frozen=True)
class ApplyResult:
    display: DisplayInfo
    window: GameWindow
    background: bool
    message: str


def _display_monitor_ids() -> dict[str, tuple[str, str]]:
    if not IS_WINDOWS:
        return {}
    result: dict[str, tuple[str, str]] = {}
    index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(adapter)
        if not _user32.EnumDisplayDevicesW(None, index, ctypes.byref(adapter), 0):
            break
        index += 1
        if not (adapter.StateFlags & 0x1):
            continue
        monitor = _DISPLAY_DEVICEW()
        monitor.cb = ctypes.sizeof(monitor)
        if _user32.EnumDisplayDevicesW(adapter.DeviceName, 0, ctypes.byref(monitor), 0):
            result[adapter.DeviceName] = (monitor.DeviceID, monitor.DeviceString)
    return result


def _enum_monitors() -> list[tuple[str, tuple[int, int, int, int], bool]]:
    if not IS_WINDOWS:
        return []
    out: list[tuple[str, tuple[int, int, int, int], bool]] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(_RECT), wintypes.LPARAM)

    def callback(hmonitor, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if _user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            out.append((info.szDevice,
                        (r.left, r.top, r.right, r.bottom),
                        bool(info.dwFlags & 1)))
        return True

    _user32.EnumDisplayMonitors(0, 0, proc_type(callback), 0)
    return out


def list_displays() -> list[DisplayInfo]:
    """按“内置屏幕优先”的顺序返回当前活动显示屏。"""
    if not IS_WINDOWS:
        return []
    ids = _display_monitor_ids()
    monitors = _enum_monitors()
    displays: list[DisplayInfo] = []
    for device, rect, primary in monitors:
        monitor_id, _device_string = ids.get(device, ("", ""))
        code = monitor_id.split("\\")[1] if "\\" in monitor_id else ""
        vendor = code[:3].upper() if code else ""
        internal = vendor in _INTERNAL_VENDORS
        if len(monitors) == 1:
            internal = True
        if internal:
            name = f"内置屏幕 · {vendor}" if vendor else "内置屏幕"
        else:
            name = f"外接显示器 · {vendor}" if vendor else "外接显示器"
        left, top, right, bottom = rect
        displays.append(DisplayInfo(
            device=device, monitor_id=monitor_id, name=name, vendor=vendor,
            internal=internal, primary=primary, x=left, y=top,
            width=max(0, right - left), height=max(0, bottom - top),
        ))
    displays.sort(key=lambda d: (not d.internal, not d.primary, d.device))
    return displays


def default_display(displays: list[DisplayInfo] | None = None) -> DisplayInfo | None:
    displays = list_displays() if displays is None else displays
    if not displays:
        return None
    for display in displays:
        if display.internal:
            return display
    for display in displays:
        if display.primary:
            return display
    return displays[0]


def resolve_display(key: str, displays: list[DisplayInfo] | None = None) -> DisplayInfo | None:
    displays = list_displays() if displays is None else displays
    if not displays:
        return None
    key = (key or "").strip()
    if key:
        for display in displays:
            if key == display.device or key == display.monitor_id:
                return display
        lowered = key.lower()
        if lowered in ("internal", "laptop", "内置"):
            for display in displays:
                if display.internal:
                    return display
        if lowered in ("primary", "主屏"):
            for display in displays:
                if display.primary:
                    return display
    return default_display(displays)


def _visible_windows() -> list[GameWindow]:
    if not IS_WINDOWS:
        return []
    out: list[GameWindow] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _data):
        if not _user32.IsWindowVisible(hwnd):
            return True
        # 跳过工具窗口，避免把托盘/输入法窗口当成游戏。
        exstyle = _user32.GetWindowLongW(hwnd, -20)
        if exstyle & 0x00000080:
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)
        rect = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        width = max(0, rect.right - rect.left)
        height = max(0, rect.bottom - rect.top)
        if width < 200 or height < 200:
            return True
        out.append(GameWindow(
            hwnd=int(hwnd), pid=int(pid.value), process="", title=title.value,
            x=rect.left, y=rect.top, width=width, height=height,
        ))
        return True

    _user32.EnumWindows(proc_type(callback), 0)
    return out


def find_game_windows(project: Project) -> list[GameWindow]:
    names = [n.strip().lower() for n in (project.game_process or "").split(";") if n.strip()]
    exe_name = ""
    if project.game_exe:
        exe_name = project.game_exe.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    if exe_name:
        names.append(exe_name)
    # 启动器窗口通常也很大，但它不是游戏；有真实游戏进程时优先真实游戏。
    non_launcher = [n for n in names if "launcher" not in n]
    if non_launcher:
        names = non_launcher
    names = list(dict.fromkeys(names))
    if not names:
        return []
    wanted = set(names)
    processes = list_processes()
    pids = {p.pid for p in processes if p.name.lower() in wanted}
    if not pids:
        return []
    proc_names = {p.pid: p.name for p in processes if p.pid in pids}
    windows: list[GameWindow] = []
    for window in _visible_windows():
        if window.pid not in pids:
            continue
        windows.append(GameWindow(
            hwnd=window.hwnd, pid=window.pid,
            process=proc_names.get(window.pid, ""), title=window.title,
            x=window.x, y=window.y, width=window.width, height=window.height,
        ))
    windows.sort(key=lambda w: w.area, reverse=True)
    return windows


def _inside(rect: tuple[int, int, int, int], display: DisplayInfo) -> bool:
    left, top, right, bottom = rect
    target = (display.x, display.y, display.x + display.width, display.y + display.height)
    return (left >= target[0] and top >= target[1]
            and right <= target[2] and bottom <= target[3])


def apply_game_window(window: GameWindow, display: DisplayInfo,
                      background: bool = False) -> ApplyResult:
    if not IS_WINDOWS:
        raise RuntimeError("仅支持 Windows")
    hwnd = window.hwnd
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    style = _user32.GetWindowLongW(hwnd, _GWL_STYLE)
    fullscreen = bool(style & _WS_POPUP) and not bool(style & _WS_OVERLAPPEDWINDOW)
    current_rect = (window.x, window.y, window.x + window.width, window.y + window.height)
    if fullscreen or not _inside(current_rect, display):
        if fullscreen:
            left, top, width, height = display.x, display.y, display.width, display.height
        else:
            width = min(window.width, display.width)
            height = min(window.height, display.height)
            left = display.x + max(0, (display.width - width) // 2)
            top = display.y + max(0, (display.height - height) // 2)
        # 先在原屏幕隐藏，避免用户看到游戏闪现在错误屏幕上。
        _user32.ShowWindow(hwnd, 0)
        moved = _user32.SetWindowPos(
            hwnd, 0, left, top, width, height,
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
        )
        time.sleep(0.3)
    else:
        moved = True
    _user32.ShowWindow(hwnd, _SW_RESTORE if not background else _SW_MINIMIZE)
    if not background:
        _user32.SetForegroundWindow(hwnd)
    mode = "后台" if background else "前台"
    if moved:
        message = f"游戏已放到 {display.label}（{mode}）"
    else:
        message = f"窗口移动失败，可能为独占全屏；请在游戏内选择 {display.name}"
    return ApplyResult(display=display, window=window, background=background, message=message)


def apply_game_display(project: Project, display_key: str = "",
                       background: bool = False) -> ApplyResult:
    displays = list_displays()
    display = resolve_display(display_key, displays)
    if display is None:
        raise RuntimeError("没有检测到可用显示屏")
    windows = find_game_windows(project)
    if not windows:
        raise RuntimeError("没有找到游戏窗口，游戏可能还没启动")
    return apply_game_window(windows[0], display, background)
