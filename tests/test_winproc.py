from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from eryou_panel.config import Config
from eryou_panel.services import HistoryStore, ProcessService
from eryou_panel.winproc import kill_tree


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_external_process_detection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    project = Config(tmp_path / "panel.json").projects[0]
    project.directory = str(tmp_path / "tool")
    tool_dir = Path(project.directory)
    tool_dir.mkdir()
    fake = tool_dir / "ok-nte.exe"
    shutil.copy2(os.path.join(os.environ["SystemRoot"], "System32", "ping.exe"), fake)

    proc = subprocess.Popen(
        [str(fake), "-n", "10", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    try:
        service = ProcessService(HistoryStore(tmp_path / "history.jsonl"))
        info = None
        deadline = time.time() + 5
        while time.time() < deadline:
            info = service.find_external(project)
            if info is not None:
                break
            time.sleep(0.2)
        assert info is not None, "应识别到项目目录下的外部进程"
        assert info.path.lower().startswith(str(tool_dir).lower())
        cpu, mem, created = service.sampler.sample(info.pid)
        assert created > 0
        assert mem > 0
    finally:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
