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
from eryou_panel.installer import InstallResult, install_name
from eryou_panel.ui.app import EryouPanelApp
from eryou_panel.ui.modals import HelpScreen, InstallScreen, ProjectSettingsScreen


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
        project.repo = ""  # 避免 UI 测试触发网络
    cfg.save()
    return cfg


@pytest.mark.asyncio
async def test_ui_navigation(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    app = EryouPanelApp(config, auto_scan=False)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        assert app.selected_id == "ok-nte"
        table = app.query_one("#tasks")
        assert table.row_count >= 5

        await pilot.press("t")
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-tasks"

        await pilot.press("2")
        await pilot.pause()
        assert app.selected_id == "ok-ww-enhanced"
        assert app.query_one("#tasks").row_count >= 4

        await pilot.press("l")
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-logs"

        await pilot.press("e")
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-config"

        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)

        app.push_screen(ProjectSettingsScreen(config.projects[0]))
        await pilot.pause()
        assert isinstance(app.screen, ProjectSettingsScreen)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_ui_settings_changes_directory(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    config.projects[0].directory = ""
    app = EryouPanelApp(config, auto_scan=False)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ProjectSettingsScreen)
        screen.query_one("#dir_input").value = str(FIX / "ok-ww")
        await pilot.pause()
        await pilot.click("#save")
        await pilot.pause()
        assert config.projects[0].directory.replace("/", "\\").endswith("tests\\fixtures\\ok-ww")
        assert config.projects[0].runner == "python"
        assert app.query_one("#tasks").row_count >= 4


def _fake_install(project, root, force=False, token="", progress=None, log=None):
    target = Path(root) / install_name(project)
    if target.exists() and not force:
        raise RuntimeError("exists")
    if target.exists():
        import shutil

        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / "ok-nte.exe").write_bytes(b"MZ")
    if progress:
        progress(50, "fake download")
    if log:
        log("fake install")
    return InstallResult(project_id=project.id, project_name=project.name,
                         path=str(target), tag="v-test", asset="fake.zip", size=1)


@pytest.mark.asyncio
async def test_ui_install_selected(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr("eryou_panel.ui.modals.install_project", _fake_install)
    config.install_root = str(tmp_path / "installs")
    config.projects[0].directory = ""
    app = EryouPanelApp(config, auto_scan=False)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause(0.6)
        assert isinstance(app.screen, InstallScreen)
        deadline = time.time() + 10
        while time.time() < deadline and not config.projects[0].directory:
            await asyncio.sleep(0.1)
            await pilot.pause(0.05)
        assert config.projects[0].directory.endswith("ok-nte"), config.projects[0].directory
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#tasks").row_count >= 20


@pytest.mark.asyncio
async def test_ui_install_all_binding(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr("eryou_panel.ui.modals.install_project", _fake_install)
    config.install_root = str(tmp_path / "installs")
    for project in config.projects:
        project.directory = ""
    app = EryouPanelApp(config, auto_scan=False)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("I")
        await pilot.pause(0.6)
        assert isinstance(app.screen, InstallScreen), app.screen
        deadline = time.time() + 10
        while time.time() < deadline and not all(p.directory for p in config.projects):
            await asyncio.sleep(0.1)
            await pilot.pause(0.05)
        assert all(p.directory for p in config.projects), [p.directory for p in config.projects]
        await pilot.press("escape")
        await pilot.pause()
