from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from eryou_panel.config import Config
from eryou_panel.ui.app import EryouPanelApp


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    cfg = Config(tmp_path / "panel.json")
    cfg.projects[0].directory = str(FIX / "ok-nte")
    cfg.projects[0].runner = "python"
    cfg.projects[1].directory = str(FIX / "ok-ww")
    cfg.projects[1].runner = "python"
    cfg.projects[2].directory = str(FIX / "maa")
    cfg.projects[2].exe = "MAA.exe"
    for project in cfg.projects:
        project.repo = ""
    cfg.save()
    return cfg


@pytest.mark.asyncio
async def test_ui_run_task(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    app = EryouPanelApp(config, auto_scan=False)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        table = app.query_one("#tasks")
        table.focus()
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause(0.3)
        runtime = app.runtimes["ok-nte"]
        deadline = time.time() + 20
        while time.time() < deadline and runtime.status == "running":
            await asyncio.sleep(0.15)
            await pilot.pause(0.05)
        await pilot.pause(0.2)
        assert runtime.exit_code == 0, runtime.last_error
        assert runtime.log_path
        text = Path(runtime.log_path).read_text(encoding="utf-8")
        assert "fixture main start" in text
        assert "src.tasks.LauncherTask.LauncherTask" in text
        assert app.query_one("#tabs").active == "tab-logs"
