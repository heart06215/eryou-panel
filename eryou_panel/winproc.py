"""Windows 进程/版本信息，使用 ctypes 实现，避免额外依赖。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    version_dll = ctypes.WinDLL("version", use_last_error=True)
else:  # pragma: no cover - 面板只面向 Windows
    kernel32 = None  # type: ignore[assignment]
    psapi = None  # type: ignore[assignment]
    version_dll = None  # type: ignore[assignment]

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


def _configure_api() -> None:
    """显式声明 64 位句柄签名，避免 ctypes 默认 c_int 截断。"""
    if not IS_WINDOWS:
        return
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    version_dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version_dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version_dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version_dll.GetFileVersionInfoW.restype = wintypes.BOOL
    version_dll.VerQueryValueW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)
    ]
    version_dll.VerQueryValueW.restype = wintypes.BOOL


_configure_api()


@dataclass
class ProcInfo:
    pid: int
    name: str
    path: str


def _filetime_to_unix(ft: FILETIME) -> float:
    value = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    if not value:
        return 0.0
    return value / 10_000_000 - 11644473600


def list_processes() -> list[ProcInfo]:
    """列出所有进程（含可执行文件路径，取不到时为空串）。"""
    if not IS_WINDOWS:
        return []
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []
    result: list[ProcInfo] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            if pid:
                result.append(ProcInfo(pid=pid, name=str(entry.szExeFile), path=process_path(pid)))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def process_path(pid: int) -> str:
    if not IS_WINDOWS:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return str(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def process_start_time(pid: int) -> float:
    if not IS_WINDOWS:
        return 0.0
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0
    try:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return _filetime_to_unix(created)
        return 0.0
    finally:
        kernel32.CloseHandle(handle)


def _process_times(pid: int) -> tuple[float, float]:
    """返回 (累计 CPU 秒, 进程启动 unix 时间)。"""
    if not IS_WINDOWS:
        return 0.0, 0.0
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0, 0.0
    try:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return 0.0, 0.0
        total = ((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime) + \
                ((user.dwHighDateTime << 32) | user.dwLowDateTime)
        return total / 10_000_000, _filetime_to_unix(created)
    finally:
        kernel32.CloseHandle(handle)


def process_memory_mb(pid: int) -> float:
    if not IS_WINDOWS:
        return 0.0
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024 * 1024)
        return 0.0
    finally:
        kernel32.CloseHandle(handle)


class MetricsSampler:
    """保存上一次 CPU 采样，按 PID 计算占用率。"""

    def __init__(self) -> None:
        self._last: dict[int, tuple[float, float]] = {}

    def sample(self, pid: int) -> tuple[float, float, float]:
        """返回 (CPU%, 内存 MB, 启动时间)。"""
        now = time.time()
        total, created = _process_times(pid)
        cpu = 0.0
        prev = self._last.get(pid)
        self._last[pid] = (total, now)
        if prev is not None:
            dt = now - prev[1]
            if dt > 0.05:
                cores = os.cpu_count() or 1
                cpu = max(0.0, (total - prev[0]) / dt / cores * 100.0)
        return cpu, process_memory_mb(pid), created

    def forget(self, pid: int) -> None:
        self._last.pop(pid, None)


def kill_tree(pid: int) -> None:
    if not pid:
        return
    if IS_WINDOWS:
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=0x08000000,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    except Exception:
        pass


def file_version(path: str) -> str:
    """读取 EXE 的 FileVersion，失败返回空串。"""
    if not IS_WINDOWS or not path or not os.path.isfile(path):
        return ""
    try:
        size = version_dll.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buffer):
            return ""
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_dll.VerQueryValueW(buffer, ctypes.c_wchar_p("\\"), ctypes.byref(value), ctypes.byref(length)):
            return ""
        if not value or length.value < ctypes.sizeof(VS_FIXEDFILEINFO):
            return ""
        info = ctypes.cast(value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
        if not any(parts):
            return ""
        return ".".join(str(p) for p in parts)
    except Exception:
        return ""
