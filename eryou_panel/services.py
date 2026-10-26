"""进程托管、日志跟随、GitHub 信息与运行历史。"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .adapters import Adapter, AdapterError
from .models import HistoryEntry, Project, Runtime, TaskItem
from .util import atomic_write, data_dir, human_duration, tail_lines
from .winproc import IS_WINDOWS, MetricsSampler, ProcInfo, kill_tree, list_processes, process_start_time

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", text)[:60]


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "history.jsonl")
        self._lock = threading.Lock()

    def append(self, entry: HistoryEntry) -> None:
        record = {
            "ts": entry.ts,
            "project_id": entry.project_id,
            "project_name": entry.project_name,
            "action": entry.action,
            "detail": entry.detail,
            "code": entry.code,
            "duration": entry.duration,
        }
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def recent(self, project_id: str = "", limit: int = 50) -> list[HistoryEntry]:
        if not self.path.is_file():
            return []
        rows: list[HistoryEntry] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh.readlines()[-800:]:
                    try:
                        raw = json.loads(line)
                    except ValueError:
                        continue
                    if project_id and raw.get("project_id") != project_id:
                        continue
                    rows.append(HistoryEntry(
                        ts=float(raw.get("ts", 0)),
                        project_id=str(raw.get("project_id", "")),
                        project_name=str(raw.get("project_name", "")),
                        action=str(raw.get("action", "")),
                        detail=str(raw.get("detail", "")),
                        code=raw.get("code"),
                        duration=raw.get("duration"),
                    ))
        except OSError:
            return []
        return rows[-limit:]


class LogTailer:
    """按字节偏移增量读取日志，处理半行与文件截断。"""

    def __init__(self) -> None:
        self.path: str = ""
        self.offset: int = 0
        self._buffer: bytes = b""

    def reset(self, path: str, prefill_lines: int = 200) -> list[str]:
        self.path = path
        self._buffer = b""
        self.offset = 0
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        lines = tail_lines(path, prefill_lines)
        self.offset = size
        return lines

    def poll(self) -> tuple[list[str], bool]:
        """返回 (新行, 是否需要清屏)。"""
        if not self.path:
            return [], False
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return [], False
        if size < self.offset:
            # 文件被截断或轮转
            lines = tail_lines(self.path, 200)
            self.offset = size
            self._buffer = b""
            return lines, True
        if size == self.offset:
            return [], False
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(size - self.offset)
        except OSError:
            return [], False
        self.offset = size
        data = self._buffer + chunk
        parts = data.split(b"\n")
        self._buffer = parts.pop() if parts else b""
        lines = [p.decode("utf-8", errors="replace").rstrip("\r") for p in parts]
        return lines, False


class ProcessService:
    def __init__(self, history: HistoryStore | None = None) -> None:
        self.log_dir = data_dir() / "panel-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.history = history or HistoryStore()
        self.sampler = MetricsSampler()
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._snapshot: list[ProcInfo] = []
        self._snapshot_at = 0.0
        self.on_exit: Callable[[Runtime, int, str], None] | None = None
        self.on_output: Callable[[Runtime, str], None] | None = None

    # ---- 进程发现 -------------------------------------------------
    def processes(self) -> list[ProcInfo]:
        now = time.time()
        if now - self._snapshot_at > 0.45:
            self._snapshot = list_processes()
            self._snapshot_at = now
        return self._snapshot

    def find_external(self, project: Project) -> ProcInfo | None:
        directory = project.directory.strip().rstrip("\\/").lower().replace("/", "\\")
        if not directory:
            return None
        prefix = directory + "\\"
        best: ProcInfo | None = None
        for proc in self.processes():
            path = proc.path.lower().replace("/", "\\")
            if not path or not path.startswith(prefix):
                continue
            name = proc.name.lower()
            if any(part in name for part in ("unins", "update", "crash")):
                continue
            if best is None or "maa" in name:
                best = proc
        return best

    # ---- 状态刷新 -------------------------------------------------
    def refresh(self, runtime: Runtime, snapshot: list[ProcInfo] | None = None) -> None:
        project = runtime.project
        popen = self._procs.get(project.id)
        if popen is not None:
            code = popen.poll()
            if code is None:
                runtime.status = "running"
                runtime.pid = popen.pid
                self._metrics(runtime)
            # 已退出时由监控线程做交接/收尾，这里不要抢先标记停止
            return

        external = self.find_external(project)
        if external is not None:
            runtime.status = "running"
            runtime.pid = external.pid
            if not runtime.started_at:
                runtime.started_at = process_start_time(external.pid)
            self._metrics(runtime)
            return
        runtime.status = "stopped"
        runtime.pid = None
        runtime.cpu = 0.0
        runtime.mem_mb = 0.0

    def _metrics(self, runtime: Runtime) -> None:
        if not runtime.pid:
            return
        cpu, mem, created = self.sampler.sample(runtime.pid)
        runtime.cpu = cpu
        runtime.mem_mb = mem
        if created and not runtime.started_at:
            runtime.started_at = created

    # ---- 启动 -----------------------------------------------------
    def start_gui(self, runtime: Runtime, adapter: Adapter) -> None:
        command = adapter.gui_command()
        env = adapter.gui_environment() if hasattr(adapter, "gui_environment") else adapter.environment()
        self._spawn(runtime, adapter, command, "启动 GUI", handoff=True, env_override=env)

    def start_task(self, runtime: Runtime, adapter: Adapter, task: TaskItem, arg: str = "",
                   label: str = "") -> None:
        command = adapter.task_command(task, arg)
        self._spawn(runtime, adapter, command, label or f"任务 · {task.name}",
                    workdir=adapter.task_workdir(), handoff=False)

    def start_shell(self, runtime: Runtime, adapter: Adapter, command_line: str) -> None:
        if not command_line.strip():
            raise AdapterError("命令为空")
        # 允许用户输入完整命令行，交给系统 shell 解析（引号/管道等）
        self._spawn(runtime, adapter, command_line, "自定义命令", use_shell=True)

    def _spawn(self, runtime: Runtime, adapter: Adapter, command, label: str,
               use_shell: bool = False, workdir: str = "", handoff: bool = False,
               env_override: dict[str, str] | None = None) -> None:
        project = runtime.project
        if not adapter.available:
            raise AdapterError("项目目录不存在，请先在设置中指定路径")
        directory = workdir or project.directory
        if not os.path.isdir(project.directory):
            raise AdapterError("项目目录不存在：" + project.directory)
        if not os.path.isdir(directory):
            raise AdapterError("运行目录不存在：" + directory)

        with self._lock:
            existing = self._procs.get(project.id)
            if existing is not None and existing.poll() is None:
                raise AdapterError("该项目已有面板启动的进程在运行，请先停止")
            if self.find_external(project) is not None:
                raise AdapterError("检测到该项目已有外部实例在运行，请先关闭它")

        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.log_dir / f"{safe_name(project.id)}-{stamp}-{safe_name(label)}.log"
        try:
            log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
        except OSError as exc:
            raise AdapterError(f"无法创建日志文件：{exc}") from exc

        shown = command if isinstance(command, str) else subprocess.list2cmdline(command)
        log_fh.write(f"# {label}\n# 项目: {project.game} / {project.name}\n")
        log_fh.write(f"# 命令: {shown}\n# 目录: {directory}\n# 开始: {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        log_fh.write("-" * 72 + "\n")

        try:
            popen = subprocess.Popen(
                command,
                cwd=directory,
                env=env_override or adapter.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=use_shell,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            log_fh.write("# 启动失败\n")
            log_fh.close()
            raise

        runtime.status = "running"
        runtime.pid = popen.pid
        runtime.started_at = time.time()
        runtime.exit_code = None
        runtime.last_error = ""
        runtime.last_action = label
        runtime.last_action_at = time.time()
        runtime.active_task = label
        runtime.log_path = str(log_path)
        runtime.log_offset = 0
        self._procs[project.id] = popen

        self.history.append(HistoryEntry(
            ts=time.time(), project_id=project.id, project_name=project.name,
            action=label, detail=shown,
        ))

        threading.Thread(
            target=self._monitor, args=(runtime, popen, log_path, log_fh, shown, label, handoff),
            daemon=True, name="proc-" + project.id,
        ).start()

    def _monitor(self, runtime: Runtime, popen: subprocess.Popen, log_path: Path,
                 log_fh, shown: str, label: str, handoff: bool = False) -> None:
        started = time.time()
        try:
            if popen.stdout is not None:
                for line in popen.stdout:
                    log_fh.write(line)
                    if self.on_output:
                        try:
                            self.on_output(runtime, line.rstrip("\r\n"))
                        except Exception:
                            pass
        except Exception:
            pass
        code = popen.wait()
        duration = time.time() - started
        try:
            log_fh.write("-" * 72 + "\n")
            log_fh.write(f"# 退出码: {code}  耗时: {human_duration(duration)}\n")
            log_fh.flush()
            log_fh.close()
        except OSError:
            pass
        self.sampler.forget(popen.pid)

        # GUI 启动器可能拉起真正的 working 进程后自行退出；任务进程不做交接
        if handoff and code == 0:
            time.sleep(1.2)
            self._snapshot_at = 0.0
            external = self.find_external(runtime.project)
            if external is not None:
                with self._lock:
                    if self._procs.get(runtime.project.id) is popen:
                        self._procs.pop(runtime.project.id, None)
                runtime.pid = external.pid
                runtime.status = "running"
                runtime.exit_code = None
                runtime.started_at = process_start_time(external.pid) or time.time()
                runtime.active_task = label
                return

        with self._lock:
            if self._procs.get(runtime.project.id) is popen:
                self._procs.pop(runtime.project.id, None)
        runtime.exit_code = code
        runtime.status = "stopped"
        runtime.pid = None
        runtime.cpu = 0.0
        runtime.mem_mb = 0.0
        runtime.last_action = label
        runtime.last_action_at = time.time()
        runtime.active_task = ""
        if code not in (0, None):
            runtime.last_error = f"进程退出码 {code}"
        self.history.append(HistoryEntry(
            ts=time.time(), project_id=runtime.project.id, project_name=runtime.project.name,
            action="结束 · " + label, detail=shown, code=code, duration=duration,
        ))
        if self.on_exit:
            try:
                self.on_exit(runtime, code, str(log_path))
            except Exception:
                pass

    # ---- 停止 -----------------------------------------------------
    def stop(self, runtime: Runtime) -> str:
        project = runtime.project
        popen = self._procs.get(project.id)
        if popen is not None and popen.poll() is None:
            kill_tree(popen.pid)
            try:
                popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                popen.kill()
            self.history.append(HistoryEntry(
                ts=time.time(), project_id=project.id, project_name=project.name, action="停止",
                detail="面板启动的进程",
            ))
            return "已发送停止指令"
        external = self.find_external(project)
        if external is not None:
            kill_tree(external.pid)
            self.history.append(HistoryEntry(
                ts=time.time(), project_id=project.id, project_name=project.name, action="停止",
                detail=f"PID {external.pid}（外部实例）",
            ))
            return f"已结束外部进程 PID {external.pid}"
        return "没有在运行的进程"

    def is_managed(self, project_id: str) -> bool:
        popen = self._procs.get(project_id)
        return popen is not None and popen.poll() is None

    def command_preview(self, adapter: Adapter, task: TaskItem, arg: str = "") -> str:
        try:
            return subprocess.list2cmdline(adapter.task_command(task, arg))
        except AdapterError:
            return "—"


class GithubService:
    def __init__(self, token: str = "", ttl: int = 900) -> None:
        self.token = token
        self.ttl = ttl
        self.cache_path = data_dir() / "github-cache.json"
        self._memory: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._memory = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._memory = {}

    def _save(self) -> None:
        try:
            atomic_write(self.cache_path, json.dumps(self._memory, ensure_ascii=False, indent=2))
        except OSError:
            pass

    def cached(self, repo: str) -> dict | None:
        with self._lock:
            return self._memory.get(repo)

    def fetch(self, repo: str) -> dict:
        """同步请求 GitHub；调用方应放到工作线程。"""
        import requests

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "eryou-panel/1.0",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        info: dict = {"repo": repo, "fetched_at": time.time()}
        try:
            response = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=8)
            if response.status_code == 200:
                data = response.json()
                info.update({
                    "description": data.get("description") or "",
                    "stars": data.get("stargazers_count") or 0,
                    "issues": data.get("open_issues_count") or 0,
                    "pushed_at": data.get("pushed_at") or "",
                    "homepage": data.get("homepage") or "",
                })
            else:
                info["error"] = f"HTTP {response.status_code}"
            release = requests.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                headers=headers, timeout=8,
            )
            if release.status_code == 200:
                rel = release.json()
                info.update({
                    "latest_tag": rel.get("tag_name") or "",
                    "latest_url": rel.get("html_url") or "",
                    "latest_published_at": rel.get("published_at") or "",
                })
            elif "error" not in info:
                info["error"] = f"Release HTTP {release.status_code}"
        except Exception as exc:  # 网络异常不应影响面板
            info["error"] = type(exc).__name__ + ": " + str(exc)[:120]
        with self._lock:
            if "error" not in info:
                self._memory[repo] = info
                self._save()
            else:
                old = self._memory.get(repo)
                if old:
                    old = dict(old)
                    old["error"] = info["error"]
                    return old
        return info
