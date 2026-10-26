"""MAA 无头环境的一键准备：安装、配置、启动 MuMu、等待 ADB。

面板不打开 MAA GUI；所有 MAA 操作都通过本模块与 maa-cli 完成。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters import MaaAdapter
from .installer import install_maa_cli, install_project
from .models import Project
from .util import data_dir, projects_dir, which
from .winproc import list_processes

Progress = Callable[[float, str], None]
Log = Callable[[str], None]
Cancel = Callable[[], bool]

CREATE_NO_WINDOW = 0x08000000
DEFAULT_MAA_ADDRESSES = (
    "127.0.0.1:16384",   # MuMu 12
    "127.0.0.1:7555",    # MuMu 6
    "127.0.0.1:5555",    # generic emulator
    "127.0.0.1:62001",   # Nox
    "127.0.0.1:62025",
    "127.0.0.1:21503",   # LDPlayer
    "emulator-5554",
)


class MaaSetupError(Exception):
    pass


@dataclass
class MumuStartResult:
    project_id: str
    adb: str
    address: str
    launched: bool = False


@dataclass
class MaaSetupResult:
    project_id: str
    path: str
    adb: str
    address: str
    cli: str
    installed: bool = False
    launched: bool = False


def _report(progress: Progress | None, value: float, message: str) -> None:
    if progress:
        progress(max(0.0, min(100.0, value)), message)


def _say(log: Log | None, message: str) -> None:
    if log:
        log(message)


def _run(adb: str, *args: str, timeout: int = 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adb, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def find_adb(project: Project) -> str:
    """定位 MuMu 自带 adb，优先使用面板检测到的路径。"""
    candidates: list[str] = []
    if project.game_adb:
        candidates.append(project.game_adb)
    game_dir = Path(project.game_dir) if project.game_dir else None
    if game_dir is not None:
        candidates.extend([
            str(game_dir / "adb.exe"),
            str(game_dir / "nx_main" / "adb.exe"),
            str(game_dir.parent / "nx_main" / "adb.exe"),
            str(game_dir.parent / "adb.exe"),
        ])
    candidates.append(which("adb"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


def find_mumu_manager(project: Project) -> str:
    """定位 MuMuManager/mumu-cli；它才是启动 MuMu VM 的官方入口。"""
    game_dir = Path(project.game_dir) if project.game_dir else None
    if game_dir is None:
        return ""
    for base in (game_dir, game_dir.parent, game_dir.parent.parent):
        for name in ("MuMuManager.exe", "mumu-cli.exe"):
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
            candidate = base / "nx_main" / name
            if candidate.is_file():
                return str(candidate)
    return ""


def mumu_info(project: Project) -> dict:
    manager = find_mumu_manager(project)
    if not manager:
        return {}
    try:
        result = subprocess.run(
            [manager, "info", "-v", "0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout or "{}")
        return data if isinstance(data, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def mumu_address(project: Project) -> str:
    """从 MuMu 官方管理接口 / vm_config.json 读取 ADB 端口。"""
    info = mumu_info(project)
    try:
        port = int(info.get("adb_port") or 0)
        host = str(info.get("adb_host_ip") or "127.0.0.1")
    except (TypeError, ValueError):
        port = 0
        host = "127.0.0.1"
    if 0 < port < 65536:
        return f"{host}:{port}"
    game_dir = Path(project.game_dir) if project.game_dir else None
    if game_dir is None:
        return ""
    roots = [game_dir, game_dir.parent]
    for base in roots:
        try:
            configs = sorted(base.glob("vms/*/configs/vm_config.json"))
        except OSError:
            configs = []
        for path in configs:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                port = data["vm"]["nat"]["port_forward"]["adb"]["host_port"]
                port = int(port)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if 0 < port < 65536:
                return f"127.0.0.1:{port}"
    return ""


def adb_devices(adb: str) -> list[tuple[str, str]]:
    if not adb:
        return []
    try:
        result = _run(adb, "devices", timeout=10)
    except Exception:
        return []
    devices: list[tuple[str, str]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def first_ready_device(adb: str, preferred: str = "") -> str:
    devices = adb_devices(adb)
    if preferred:
        for serial, state in devices:
            if serial == preferred and state == "device":
                return serial
    for serial, state in devices:
        if state == "device":
            return serial
    return ""


def start_emulator(project: Project, log: Log | None = None) -> bool:
    """启动 MuMu VM；优先使用官方 MuMuManager，避免只打开主窗口不启动安卓。"""
    manager = find_mumu_manager(project)
    if manager:
        info = mumu_info(project)
        if info.get("is_android_started") or info.get("player_state") == "start_finished":
            return False
        try:
            result = subprocess.run(
                [manager, "control", "-v", "0", "launch"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            raise MaaSetupError(f"MuMuManager 启动失败：{exc}") from exc
        text = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise MaaSetupError("MuMuManager 启动失败：" + (text[:200] or f"退出码 {result.returncode}"))
        _say(log, "已通过 MuMuManager 启动安卓设备")
        return True

    process_names = [
        name.strip().lower()
        for name in (project.game_process or "MuMuNxMain.exe").split(";")
        if name.strip()
    ]
    try:
        running = {proc.name.lower() for proc in list_processes()}
    except Exception:
        running = set()
    if running & set(process_names):
        return False
    exe = project.game_exe
    if not exe or not Path(exe).is_file():
        raise MaaSetupError("未找到 MuMu 启动器，请先在概览“对应游戏”中重新查找或手动填写")
    if Path(exe).name.lower() in ("maa.exe", "maa-cli.exe"):
        raise MaaSetupError("当前游戏路径指向 MAA 本体，请在概览重新查找 MuMu")
    try:
        subprocess.Popen(
            [exe],
            cwd=project.game_dir or str(Path(exe).parent),
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        raise MaaSetupError(f"启动 MuMu 失败：{exc}") from exc
    _say(log, f"已启动 MuMu：{Path(exe).name}")
    return True


def wait_for_adb(adb: str, preferred: str = "", *, timeout: int = 120,
                 progress: Progress | None = None, log: Log | None = None,
                 cancel: Cancel | None = None) -> str:
    """轮询 ADB；自动尝试 MuMu/常见端口，直到出现 device。"""
    if not adb:
        raise MaaSetupError("未找到 ADB。请确认 MuMu 已安装，或在 MAA 连接卡中填写 adb.exe")
    ready = first_ready_device(adb, preferred)
    if ready:
        return ready

    candidates = [preferred, *DEFAULT_MAA_ADDRESSES]
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)

    started = time.time()
    deadline = started + max(15, timeout)
    last_connect: dict[str, float] = {}
    last_report = 0.0
    while time.time() < deadline:
        if cancel and cancel():
            raise MaaSetupError("已取消 MAA 准备")
        ready = first_ready_device(adb, preferred)
        if ready:
            _say(log, f"ADB 已就绪：{ready}")
            return ready
        now = time.time()
        for address in unique:
            if now - last_connect.get(address, 0.0) < 5.0:
                continue
            last_connect[address] = now
            try:
                result = _run(adb, "connect", address, timeout=8)
                text = ((result.stdout or "") + (result.stderr or "")).strip()
                if text and ("connected" in text.lower() or "already" in text.lower()):
                    _say(log, f"ADB 连接 {address}：{text.splitlines()[0]}")
            except Exception:
                pass
        if now - last_report > 3.0:
            last_report = now
            fraction = min(0.95, max(0.0, (now - started) / max(1, timeout)))
            _report(progress, fraction * 100, "等待 MuMu 的 ADB 就绪…")
        time.sleep(1.5)
    return ""


def start_mumu(project: Project, preferred_address: str = "", timeout: int = 120,
               progress: Progress | None = None, log: Log | None = None,
               cancel: Cancel | None = None) -> MumuStartResult:
    """一键启动 MuMu 并等待 ADB；不安装/启动 MAA，不启动游戏。"""
    _report(progress, 0, "查找 MuMu / ADB…")
    adb = find_adb(project)
    if not adb:
        raise MaaSetupError("未找到 ADB。请确认 MuMu 已安装，或手动填写 adb.exe")
    project.game_adb = adb
    detected = mumu_address(project)
    selected = preferred_address or detected or "127.0.0.1:16384"

    launched = False
    manager = find_mumu_manager(project)
    info = mumu_info(project) if manager else {}
    vm_started = bool(info.get("is_android_started") or info.get("player_state") == "start_finished")
    if manager:
        if not vm_started:
            _report(progress, 8, "启动 MuMu…")
            launched = start_emulator(project, log)
        else:
            _report(progress, 8, "MuMu 已在运行")
    elif not first_ready_device(adb, selected):
        _report(progress, 8, "启动 MuMu…")
        launched = start_emulator(project, log)
    else:
        _report(progress, 8, "MuMu 已在运行")

    _report(progress, 15, "等待 MuMu ADB 就绪…")
    ready = wait_for_adb(
        adb,
        preferred=selected,
        timeout=timeout,
        progress=lambda fraction, message: _report(
            progress, 15 + max(0.0, min(1.0, fraction / 100.0)) * 75, message),
        log=log,
        cancel=cancel,
    )
    if not ready:
        raise MaaSetupError(
            "MuMu 的 ADB 未就绪。请确认模拟器已启动、ADB 调试已开启，"
            "或把端口改成 MuMu 的 ADB 端口后重试。"
        )
    project.extra["maa_address"] = ready
    try:
        adapter = MaaAdapter(project)
        if adapter.available:
            adapter.ensure_profile(force=True)
    except Exception:
        pass
    _report(progress, 100, f"MuMu 已启动 · ADB {ready}")
    _say(log, f"MuMu ADB 已就绪：{adb} → {ready}")
    return MumuStartResult(
        project_id=project.id,
        adb=adb,
        address=ready,
        launched=launched,
    )


def prepare_maa(project: Project, root: Path, token: str = "", preferred_address: str = "",
                launch_emulator: bool = True, progress: Progress | None = None,
                log: Log | None = None, cancel: Cancel | None = None) -> MaaSetupResult:
    """一键准备 MAA：安装/修复 → maa-cli → 本地资源 → 启动 MuMu → ADB。"""
    root = Path(root)
    installed = False
    adapter = MaaAdapter(project)

    if not adapter.gui_exe():
        _report(progress, 1, "安装 MAA…")
        force = bool(project.directory and Path(project.directory).exists())
        result = install_project(
            project,
            root,
            force=force,
            token=token,
            progress=lambda value, message: _report(progress, 2 + value * 0.58, message),
            log=log,
        )
        project.directory = result.path
        installed = True
        adapter = MaaAdapter(project)
        _say(log, f"MAA 已安装：{result.tag or result.message}")
    else:
        _report(progress, 60, "MAA 已安装")

    if not adapter.cli():
        _report(progress, 62, "安装 maa-cli…")
        work = root / ".eryou-install-tmp" / f"maa-cli-{int(time.time())}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            runtime = projects_dir() / "maa-runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            install_maa_cli(runtime, work, token, log)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    _report(progress, 68, "准备本地 MAA 资源…")
    adapter.ensure_data_layout()
    adb = find_adb(project)
    if not adb:
        raise MaaSetupError("未找到 ADB。请确认 MuMu 已安装，或在任务页填写 adb.exe")
    project.game_adb = adb

    detected = mumu_address(project)
    if detected:
        _say(log, f"检测到 MuMu ADB 地址：{detected}")
    addresses = [preferred_address, detected, *DEFAULT_MAA_ADDRESSES]
    selected = next((item for item in addresses if item), "")
    if not selected:
        selected = "127.0.0.1:16384"

    launched = False
    if launch_emulator:
        manager = find_mumu_manager(project)
        info = mumu_info(project) if manager else {}
        vm_started = bool(info.get("is_android_started") or info.get("player_state") == "start_finished")
        need_launch = (not vm_started) if manager else (not first_ready_device(adb, selected))
        if need_launch:
            _report(progress, 72, "启动 MuMu…")
            launched = start_emulator(project, log)

    _report(progress, 76, "等待 ADB 就绪…")
    ready = wait_for_adb(
        adb,
        preferred=selected,
        timeout=120,
        progress=lambda fraction, message: _report(progress, 76 + fraction * 0.22, message),
        log=log,
        cancel=cancel,
    )
    if not ready:
        raise MaaSetupError(
            "MuMu 的 ADB 未就绪。请确认模拟器已启动、ADB 调试已开启，"
            "或把端口改成 MuMu 的 ADB 端口后重试。"
        )

    project.extra["maa_address"] = ready
    adapter.ensure_profile(force=True)
    _report(progress, 100, f"MAA 已就绪 · {ready}")
    _say(log, f"MAA 配置完成：{adb} → {ready}")
    return MaaSetupResult(
        project_id=project.id,
        path=project.directory,
        adb=adb,
        address=ready,
        cli=adapter.cli(),
        installed=installed,
        launched=launched,
    )
