"""不同项目类型的适配器：任务清单、启动命令、日志位置。"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .constants import MAA_PRESETS, STATIC_TASKS, TASK_NAMES
from .discovery import EXE_HINTS, find_exe, find_exe_any
from .models import Project, TaskItem
from .util import data_dir, projects_dir, python_candidates, read_text, which
from .winproc import file_version


def _toml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    if isinstance(value, (list, tuple)):
        return f"{key} = [" + ", ".join(_toml_quote(str(v)) for v in value) + "]"
    return f"{key} = {_toml_quote(str(value))}"


class AdapterError(Exception):
    pass


class Adapter:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.directory = project.directory
        self.root = Path(project.directory) if project.directory else None

    # ---- 基本信息 -------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.root and self.root.is_dir())

    def exe_path(self) -> str:
        if not self.root:
            return ""
        if self.project.exe:
            candidate = self.root / self.project.exe
            if candidate.is_file():
                return str(candidate)
            # 用户可能填了绝对路径
            if Path(self.project.exe).is_file():
                return self.project.exe
        rel = find_exe(str(self.root), EXE_HINTS.get(self.project.id, ()))
        if not rel and self.project.extra.get("allow_any_exe"):
            rel = find_exe_any(str(self.root))
        if not rel:
            return ""
        return str(self.root / rel)

    def mode_text(self) -> str:
        return "未安装" if not self.available else "就绪"

    def gui_command(self) -> list[str]:
        raise AdapterError("该类型没有图形界面启动方式")

    def tasks(self) -> list[TaskItem]:
        return []

    def task_command(self, task: TaskItem, arg: str = "") -> list[str]:
        raise AdapterError("该任务暂不支持无头运行")

    def log_files(self) -> list[Path]:
        if not self.root:
            return []
        files: list[Path] = []
        for folder in ("logs", "log", "debug"):
            path = self.root / folder
            if not path.is_dir():
                continue
            try:
                files.extend([p for p in path.rglob("*.log") if p.is_file()])
            except OSError:
                continue
        files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return files

    def config_dir(self) -> Path | None:
        if not self.root:
            return None
        for name in ("configs", "config"):
            path = self.root / name
            if path.is_dir():
                return path
        return None

    def version(self) -> str:
        exe = self.exe_path()
        value = file_version(exe)
        if value:
            return value
        # 通用项目（BGI / 三月七助手 / OneDragon）的主程序可能没有文件版本，
        # 回退到面板安装标记里的 Release tag。
        if self.root:
            marker = self.root.parent / ".eryou-install" / f"{self.project.id}.json"
            try:
                data = json.loads(read_text(marker, 100_000))
            except (OSError, ValueError):
                return ""
            return str(data.get("tag", "") or "").lstrip("vV")
        return ""

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        return env

    def gui_environment(self) -> dict[str, str]:
        return self.environment()

    def task_workdir(self) -> str:
        """无头任务的运行目录，默认项目根目录。"""
        return self.directory


class OkScriptAdapter(Adapter):
    """ok-script 系（ok-nte / ok-ww-enhanced）。"""

    def main_py(self) -> str:
        if not self.root:
            return ""
        candidates = [self.project.extra.get("source_main", ""), "main.py", "main_debug.py"]
        for name in candidates:
            if name and (self.root / name).is_file():
                return name
        return ""

    def runner(self) -> str:
        """返回 exe | python | missing。"""
        if self.project.runner == "python" and self.main_py():
            return "python"
        if self.exe_path():
            return "exe"
        if self.main_py() and python_candidates(self.directory):
            return "python"
        return "missing"

    def python(self) -> str:
        return python_candidates(self.directory)[0] if python_candidates(self.directory) else ""

    def mode_text(self) -> str:
        if not self.available:
            return "未安装"
        if not self.initialized:
            return "启动器已安装 · 需首次初始化"
        runner = self.runner()
        if runner == "exe":
            return "打包版 EXE"
        if runner == "python":
            return "源码 · Python"
        return "缺少主程序"

    def _app_json(self) -> dict:
        if not self.root:
            return {}
        candidates = list(self.root.glob("data/apps/*/app.json"))
        candidates.append(self.root / "app.json")
        for path in candidates:
            try:
                data = json.loads(read_text(path, 500_000))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
        return {}

    @property
    def initialized(self) -> bool:
        data = self._app_json()
        return bool(data.get("installed")) and bool(data.get("current_version"))

    def version(self) -> str:
        data = self._app_json()
        value = str(data.get("current_version") or "").strip()
        if value:
            return value.lstrip("vV")
        if self.root:
            candidates = list(self.root.glob("data/apps/*/app.json"))
            candidates.append(self.root / "app.json")
            for path in candidates:
                try:
                    data = json.loads(read_text(path, 500_000))
                except (OSError, ValueError):
                    continue
                value = str(data.get("current_version") or "").strip()
                if value:
                    return value.lstrip("vV")
        return super().version()

    def gui_command(self) -> list[str]:
        runner = self.runner()
        if runner == "exe":
            return [self.exe_path()]
        if runner == "python":
            return [self.python(), self.main_py()]
        raise AdapterError("目录中未找到可执行程序（ok-*.exe 或 main.py）")

    def _config_file(self) -> Path | None:
        if not self.root:
            return None
        for name in (self.project.extra.get("config_py", "src/config.py"), "src/config.py", "config.py"):
            path = self.root / name
            if path.is_file():
                return path
        return None

    def _task_list(self, text: str, key: str) -> list[tuple[str, str]]:
        match = re.search(r'["\']' + key + r'["\']\s*:\s*\[', text)
        if not match:
            return []
        start = text.find("[", match.start())
        depth = 0
        end = start
        for index in range(start, len(text)):
            char = text[index]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        block = text[start:end + 1]
        pairs: list[tuple[str, str]] = []
        for item in re.finditer(r'\[\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\]', block):
            module, class_name = item.group(1), item.group(2)
            pairs.append((module, class_name))
        return pairs

    def tasks(self) -> list[TaskItem]:
        config_file = self._config_file()
        text = read_text(config_file) if config_file is not None else ""
        result: list[TaskItem] = []
        seen: set[str] = set()
        for key, kind in (("onetime_tasks", "onetime"), ("trigger_tasks", "trigger")):
            for module, class_name in self._task_list(text, key):
                full = f"{module}.{class_name}"
                if full in seen:
                    continue
                seen.add(full)
                name = TASK_NAMES.get(class_name, class_name)
                hint = "常驻触发任务" if kind == "trigger" else "一次性任务"
                result.append(TaskItem(key=full, name=name, kind=kind, hint=hint))
        if result:
            return result
        static = STATIC_TASKS.get(self.project.id, {})
        for kind in ("onetime", "trigger"):
            for _module, class_name in static.get(kind, []):
                if class_name in seen:
                    continue
                seen.add(class_name)
                name = TASK_NAMES.get(class_name, class_name)
                hint = "内置清单 · " + ("常驻触发" if kind == "trigger" else "一次性任务")
                result.append(TaskItem(key=class_name, name=name, kind=kind, hint=hint))
        return result

    def working_exe(self) -> str:
        """pyappify 安装包的真正主程序通常在 data/apps/*/working/ 下。"""
        if not self.root:
            return ""
        for pattern in ("data/apps/*/working/*.exe", "data/apps/*/*.exe"):
            try:
                for path in self.root.glob(pattern):
                    stem = path.stem.lower()
                    if stem.startswith("ok-nte") or stem.startswith("ok-ww"):
                        return str(path)
            except OSError:
                continue
        return ""

    def task_workdir(self) -> str:
        exe = self.working_exe()
        return str(Path(exe).parent) if exe else self.directory

    def task_command(self, task: TaskItem, arg: str = "") -> list[str]:
        # 源码/开发模式仍允许无头任务；打包版没有 ok-script CLI，必须使用官方 GUI。
        if self.runner() == "python" and self.main_py():
            return [self.python(), self.main_py(), "-t", task.key, "-h"]
        raise AdapterError("ok-nte / ok-ww 打包版请使用原项目官方 GUI 运行任务")

    def _nested_dirs(self, name: str) -> list[Path]:
        """打包版 pyappify 运行时把配置/日志放在 data/apps/*/working 下。"""
        if not self.root:
            return []
        result: list[Path] = []
        for pattern in (f"data/apps/*/working/{name}", f"data/apps/*/{name}", f"data/{name}"):
            try:
                result.extend([p for p in self.root.glob(pattern) if p.is_dir()])
            except OSError:
                continue
        return result

    def config_dir(self) -> Path | None:
        base = super().config_dir()
        if base is not None:
            return base
        for path in self._nested_dirs("configs"):
            return path
        return None

    def log_files(self) -> list[Path]:
        files = super().log_files()
        for folder in self._nested_dirs("logs") + self._nested_dirs("log"):
            try:
                files.extend([p for p in folder.rglob("*.log") if p.is_file()])
            except OSError:
                continue
        config_file = self._config_file()
        if config_file is not None and self.root is not None:
            text = read_text(config_file, 200_000)
            for match in re.finditer(r'["\']log_file["\']\s*:\s*["\']([^"\']+\.log)["\']', text):
                candidate = self.root / match.group(1).replace("/", "\\")
                if candidate.is_file() and candidate not in files:
                    files.append(candidate)
        unique: dict[str, Path] = {}
        for path in files:
            unique[str(path)] = path
        ordered = sorted(unique.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return ordered


MAA_FILE_TASKS = {
    "recruit": ("Recruit", {}),
    "infrast": ("Infrast", {
        "mode": 10000,
        "facility": ["Trade", "Reception", "Mfg", "Control", "Power", "Office", "Dorm"],
        "dorm_trust_enabled": True,
    }),
    "mall": ("Mall", {}),
    "award": ("Award", {}),
}


class MaaAdapter(Adapter):
    """MAA：仅通过 maa-cli 无头执行，不启动 MAA GUI。"""

    def _actual_files(self) -> dict[str, Path]:
        """按磁盘上的真实大小写列出文件，避免 Windows 大小写不敏感造成误判。"""
        if not self.root or not self.root.is_dir():
            return {}
        try:
            return {item.name: item for item in self.root.iterdir() if item.is_file()}
        except OSError:
            return {}

    def gui_exe(self) -> str:
        files = self._actual_files()
        gui = files.get("MAA.exe") or files.get("Maa.exe")
        if gui is not None:
            return str(gui)
        return ""

    def runtime_dir(self) -> Path:
        """面板自己的 MAA 配置/数据目录，不写入开源 MAA 目录。"""
        path = projects_dir() / "maa-runtime"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cli(self) -> str:
        runtime = self.runtime_dir()
        for name in ("maa-cli.exe", "maa.exe"):
            candidate = runtime / name
            if candidate.is_file():
                return str(candidate)
        for name in ("maa", "maa-cli"):
            path = which(name)
            if not path:
                continue
            # PATH 里可能只找到 GUI 版的 MAA.exe
            parent = Path(path).parent
            gui = parent / "MAA.exe"
            try:
                if gui.is_file() and os.path.samefile(path, gui):
                    continue
            except OSError:
                pass
            return path
        return ""

    def environment(self) -> dict[str, str]:
        env = super().environment()
        runtime = self.runtime_dir()
        env["MAA_CONFIG_DIR"] = str(runtime / "config")
        env["MAA_DATA_DIR"] = str(runtime)
        return env

    def gui_environment(self) -> dict[str, str]:
        # 原项目 GUI 使用 MAA 自己的配置/数据目录。
        return Adapter.environment(self)

    def ensure_data_layout(self) -> None:
        """在面板目录建立 MAA 资源链接，不修改 MAA 安装目录。"""
        if not self.root:
            return
        source = self.root / "resource"
        runtime = self.runtime_dir()
        target = runtime / "MaaResource" / "resource"
        if target.exists() or not source.is_dir():
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            cmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
            subprocess.run(
                [str(cmd), "/c", "mklink", "/J", str(target), str(source)],
                capture_output=True, timeout=10, creationflags=0x08000000,
            )
        except Exception:
            pass

    def profile_path(self) -> Path | None:
        if not self.root:
            return None
        return self.runtime_dir() / "config" / "profiles" / "default.toml"

    def ensure_profile(self, force: bool = False) -> str:
        """把 MuMu/模拟器的 ADB 路径与地址写进 maa-cli 的默认 profile。"""
        path = self.profile_path()
        if path is None:
            return ""
        self.ensure_data_layout()
        if path.is_file() and not force:
            return str(path)
        adb = self.project.game_adb
        if not adb or not Path(adb).is_file():
            adb = "adb"
        address = str(self.project.extra.get("maa_address", "127.0.0.1:16384"))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[connection]\n"
            f"adb_path = {_toml_quote(adb)}\n"
            f"address = {_toml_quote(address)}\n"
            'config = "General"\n\n'
            "[instance_options]\n"
            'touch_mode = "MaaTouch"\n'
            "deployment_with_pause = false\n"
            "adb_lite_enabled = false\n"
            "kill_adb_on_exit = false\n\n"
            "[behavior]\n"
            "auto_reconnect = true\n"
        )
        path.write_text(content, encoding="utf-8", newline="\n")
        return str(path)

    def mode_text(self) -> str:
        if not self.available:
            return "未安装"
        return "原项目 GUI · MAA"

    def gui_command(self) -> list[str]:
        exe = self.gui_exe()
        if not exe:
            raise AdapterError("目录中未找到 MAA.exe")
        return [exe]

    def config_dir(self) -> Path | None:
        path = self.runtime_dir() / "config"
        return path if path.is_dir() else None

    def tasks(self) -> list[TaskItem]:
        cli = self.cli()
        result: list[TaskItem] = []
        for command, name, hint, arg_prompt in MAA_PRESETS:
            result.append(TaskItem(
                key=command,
                name=name,
                kind="preset",
                hint=hint if cli else hint + "（需要 maa-cli）",
                arg_prompt=arg_prompt,
            ))
        task_dirs: list[Path] = [self.runtime_dir() / "config" / "tasks"]
        if self.root:
            # 只读已有 MAA 配置用于展示，不写入。
            task_dirs.append(self.root / "config" / "tasks")
        seen: set[str] = set()
        for tasks_dir in task_dirs:
            if not tasks_dir.is_dir():
                continue
            try:
                for item in sorted(tasks_dir.iterdir()):
                    if not item.is_file() or item.suffix.lower() not in (".toml", ".yaml", ".yml", ".json"):
                        continue
                    if item.stem.lower().startswith("panel_") or item.stem in seen:
                        continue
                    seen.add(item.stem)
                    result.append(TaskItem(
                        key=item.stem,
                        name=f"自定义 · {item.stem}",
                        kind="custom",
                        hint="maa run " + item.stem,
                    ))
            except OSError:
                pass
        return result

    def task_command(self, task: TaskItem, arg: str = "") -> list[str]:
        if task.command:
            return list(task.command)
        cli = self.cli()
        if not cli:
            raise AdapterError("未找到 maa-cli。请安装 maa-cli，或把 maa.exe 放到 MAA 目录 / PATH 中。")
        try:
            self.ensure_profile(False)
        except Exception:
            pass
        if task.key in MAA_FILE_TASKS:
            name = self.ensure_task_file(task.key)
            return [cli, "--batch", "run", name]
        command = [cli]
        if task.kind == "custom":
            command.extend(["run", task.key])
        else:
            command.append(task.key)
        if arg.strip():
            command.extend(shlex.split(arg, posix=False))
        return command

    def task_dir(self) -> Path | None:
        if not self.root:
            return None
        return self.runtime_dir() / "config" / "tasks"

    def ensure_task_file(self, key: str) -> str:
        task_type, params = MAA_FILE_TASKS[key]
        directory = self.task_dir()
        if directory is None:
            raise AdapterError("MAA 目录未设置")
        directory.mkdir(parents=True, exist_ok=True)
        name = f"panel_{key}"
        path = directory / f"{name}.toml"
        lines = ["[[tasks]]", f'type = {_toml_quote(task_type)}']
        if params:
            lines.append("[tasks.params]")
            lines.extend(_toml_value(k, v) for k, v in params.items())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return name

    def write_daily_task(self, stage: str, medicine: int, stone: int, client: str) -> TaskItem:
        directory = self.task_dir()
        if directory is None:
            raise AdapterError("MAA 目录未设置")
        directory.mkdir(parents=True, exist_ok=True)
        client = (client or "Official").strip()
        stage = (stage or "").strip()
        blocks: list[str] = []
        blocks.append(
            "[[tasks]]\n"
            'type = "StartUp"\n'
            "[tasks.params]\n"
            f"client_type = {_toml_quote(client)}\n"
            "start_game_enabled = true\n"
        )
        fight = [
            "[[tasks]]",
            'type = "Fight"',
            "[tasks.params]",
        ]
        if stage:
            fight.append(f"stage = {_toml_quote(stage)}")
        fight.extend([f"medicine = {int(medicine)}", f"stone = {int(stone)}"])
        blocks.append("\n".join(fight) + "\n")
        blocks.append("[[tasks]]\ntype = \"Recruit\"\n")
        blocks.append(
            "[[tasks]]\n"
            'type = "Infrast"\n'
            "[tasks.params]\n"
            "mode = 10000\n"
            'facility = ["Trade", "Reception", "Mfg", "Control", "Power", "Office", "Dorm"]\n'
            "dorm_trust_enabled = true\n"
        )
        blocks.append("[[tasks]]\ntype = \"Mall\"\n")
        blocks.append("[[tasks]]\ntype = \"Award\"\n")
        blocks.append(
            "[[tasks]]\n"
            'type = "CloseDown"\n'
            "[tasks.params]\n"
            f"client_type = {_toml_quote(client)}\n"
        )
        name = "panel_daily"
        (directory / f"{name}.toml").write_text(
            "\n".join(blocks), encoding="utf-8", newline="\n")
        cli = self.cli()
        if not cli:
            raise AdapterError("未找到 maa-cli")
        self.ensure_profile(False)
        return TaskItem(
            key=name,
            name="一键日常",
            kind="custom",
            command=[cli, "--batch", "run", name],
            hint=f"{client} · {stage or '当前关卡'} · 药{medicine} 石{stone}",
        )

    def log_files(self) -> list[Path]:
        files = super().log_files()
        if self.root:
            extra = self.root / "debug"
            if extra.is_dir():
                try:
                    files.extend([p for p in extra.glob("*.log") if p not in files])
                except OSError:
                    pass
        files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return files

    def version(self) -> str:
        return file_version(self.gui_exe())


class GenericAdapter(Adapter):
    def mode_text(self) -> str:
        if not self.available:
            return "未安装"
        return "通用 EXE" if self.exe_path() else "缺少 EXE"

    def gui_command(self) -> list[str]:
        exe = self.exe_path()
        if not exe:
            raise AdapterError("未找到可执行文件，请在设置中填写")
        return [exe]

    def tasks(self) -> list[TaskItem]:
        result: list[TaskItem] = []
        actions = self.project.extra.get("quick_actions", [])
        if isinstance(actions, list):
            for index, action in enumerate(actions):
                if not isinstance(action, dict):
                    continue
                name = str(action.get("name", f"动作 {index + 1}"))
                args = action.get("args", [])
                if isinstance(args, str):
                    args = args.split()
                result.append(TaskItem(
                    key=name,
                    name=name,
                    kind="custom",
                    hint=str(action.get("hint", " ".join(str(a) for a in args))),
                    arg_prompt=str(action.get("arg_prompt", "")),
                ))
        return result

    def task_command(self, task: TaskItem, arg: str = "") -> list[str]:
        exe = self.exe_path()
        if not exe:
            raise AdapterError("未找到可执行文件")
        command = [exe]
        actions = self.project.extra.get("quick_actions", [])
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, dict) and str(action.get("name", "")) == task.key:
                    args = action.get("args", [])
                    if isinstance(args, str):
                        args = args.split()
                    command.extend(str(a) for a in args)
                    break
        if arg.strip():
            command.extend(shlex.split(arg, posix=False))
        return command

    def log_files(self) -> list[Path]:
        if not self.root:
            return []
        folders = self.project.log_dirs or ["logs", "log"]
        files: list[Path] = []
        for name in folders:
            path = self.root / name
            if path.is_dir():
                try:
                    files.extend([p for p in path.rglob("*.log") if p.is_file()])
                except OSError:
                    continue
        files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return files


def make_adapter(project: Project) -> Adapter:
    kind = project.kind
    if kind == "okscript":
        return OkScriptAdapter(project)
    if kind == "maa":
        return MaaAdapter(project)
    return GenericAdapter(project)
