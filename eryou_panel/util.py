"""通用小工具：路径、时间、文本、原子写入。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    """EXE 所在目录；源码运行时为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """打包后指向 PyInstaller 解压目录，源码运行时指向项目根目录。"""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", "") else app_dir()
    return base / relative


def data_dir() -> Path:
    """可写的数据目录：EXE 目录优先，否则 LOCALAPPDATA。"""
    path = app_dir()
    if not os.access(path, os.W_OK):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "EryouPanel"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_text(path: str | Path, limit: int = 4_000_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def human_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(0.0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def human_time(ts: float) -> str:
    import datetime

    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def short_time(ts: float) -> str:
    import datetime

    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def tail_lines(path: str | Path, max_lines: int = 400, max_bytes: int = 512_000) -> list[str]:
    """读取文件尾部，按行返回，避免把巨大的日志读进内存。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    if start > 0:
        index = text.find("\n")
        if index >= 0:
            text = text[index + 1:]
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return [line.rstrip("\r") for line in lines]


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def normalize_dir(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'")
    if not value:
        return ""
    return str(Path(os.path.expandvars(value)).expanduser())


def open_path(path: str) -> None:
    if not path:
        return
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", path])


def open_url(url: str) -> None:
    if not url:
        return
    import webbrowser

    webbrowser.open(url)


def which(name: str) -> str:
    """在 PATH 中查找可执行文件，找不到返回空串。"""
    return shutil.which(name) or ""


def load_json(path: Path, fallback: dict | None = None) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError):
        pass
    return dict(fallback or {})


def python_candidates(directory: str) -> list[str]:
    """项目目录里可用的 Python 解释器。"""
    found: list[str] = []
    if directory:
        for rel in (
            r".venv\Scripts\python.exe",
            r"venv\Scripts\python.exe",
            r"env\Scripts\python.exe",
        ):
            p = Path(directory) / rel
            if p.is_file():
                found.append(str(p))
    for name in ("python.exe", "python3.exe"):
        p = which(name)
        if p:
            found.append(p)
    if not getattr(sys, "frozen", False):
        found.append(sys.executable)
    seen: set[str] = set()
    unique = []
    for item in found:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def browse_folder(title: str = "选择目录") -> str:
    """调用 Windows 原生目录选择框；取消或失败返回空串。"""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)

        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT),
                ("lpfn", ctypes.c_void_p),
                ("lParam", wintypes.LPARAM),
                ("iImage", ctypes.c_int),
            ]

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        ole32.CoInitialize(None)

        display = ctypes.create_unicode_buffer(260)
        info = BROWSEINFOW()
        info.hwndOwner = None
        info.pidlRoot = None
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = title
        info.ulFlags = 0x00000001 | 0x00000040  # 只显示文件系统目录 / 可编辑路径
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return ""
        path_buf = ctypes.create_unicode_buffer(1024)
        if shell32.SHGetPathFromIDListW(pidl, path_buf):
            return str(path_buf.value)
        return ""
    except Exception:
        return ""

