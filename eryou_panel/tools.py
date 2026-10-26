"""扫描结果的落盘应用，供 TUI 与 --scan 共用。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import Config
from .discovery import probe, scan

Out = Callable[[str], None]


def apply_scan_results(config: Config, found: dict[str, str]) -> list[str]:
    """只填补空目录/失效目录，绝不覆盖用户已配置且有效的路径。"""
    applied: list[str] = []
    for project in config.projects:
        directory = found.get(project.id, "")
        if not directory:
            continue
        if project.directory and Path(project.directory).is_dir():
            continue
        info = probe(project, directory)
        project.directory = directory
        project.kind = info.get("kind", project.kind)
        project.exe = info.get("exe", "")
        project.runner = info.get("runner", "exe")
        applied.append(project.id)
    if applied:
        config.save()
    return applied


def scan_cli(config: Config, out: Out = print) -> int:
    out("正在扫描本机安装目录（注册表 + 常见路径）…")
    found = scan(config.projects, progress=out)
    if not found:
        out("未找到已安装的项目。")
        out("请从 GitHub Releases 下载解压后，运行 EryouPanel.exe 并按 p 手动指定目录。")
        return 1
    applied = apply_scan_results(config, found)
    for project in config.projects:
        directory = found.get(project.id)
        if not directory:
            continue
        mark = "已应用" if project.id in applied else "保留现有配置"
        out(f"{project.game:6} {project.name:18} {directory}  [{mark}]")
    out(f"配置已写入：{config.path}")
    return 0
