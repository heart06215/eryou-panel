from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from eryou_panel.config import Config
from eryou_panel.gui.theme import apply_theme
from eryou_panel.gui.window import FloatingPanel
from eryou_panel.installer import InstallResult


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
    cfg.ui["compact"] = False
    cfg.save()
    return cfg


def test_gui_window(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = FloatingPanel(config, compact=False)
    try:
        window.show()
        app.processEvents()
        assert window.tabs.count() == 2
        assert not window.tasks.gui_card.isHidden()
        assert window.tasks.table.isHidden()
        assert "打开原项目" in window.tasks.gui_btn.text()
        assert not window._compact
        window.set_compact(True)
        assert window._compact
        window.set_compact(False)
        window._select_project("maa", force=True)
        assert window.selected_id == "maa"
        assert window.tasks.gui_btn.text() == "打开 MAA"
        assert window.overview.launch_game_btn.text() == "打开 MAA"
        assert window.tasks.table.isHidden()
        assert "安装情况" in window.side_install.text()
        assert "安装全部缺失" not in window.side_install.text()
    finally:
        window.hide()
        window.deleteLater()
        app.processEvents()


def test_install_dialog_worker(config: Config, tmp_path: Path, monkeypatch) -> None:
    from eryou_panel.gui.widgets import InstallDialog

    target = tmp_path / "installs" / "ok-nte"

    def fake_install(project, root, force=False, token="", progress=None, log=None):
        target.mkdir(parents=True, exist_ok=True)
        (target / "ok-nte.exe").write_bytes(b"MZ")
        if progress:
            progress(50, "fake")
        if log:
            log("fake install")
        return InstallResult(project_id=project.id, project_name=project.name,
                             path=str(target), tag="v-test", asset="fake.zip", size=1)

    monkeypatch.setattr("eryou_panel.gui.workers.install_project", fake_install)
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    dialog = InstallDialog([config.projects[0]], tmp_path / "installs", parent=None)
    try:
        dialog.start()
        deadline = time.time() + 10
        while time.time() < deadline and dialog._busy:
            app.processEvents()
            time.sleep(0.02)
        assert dialog.results, dialog.errors
        assert not dialog.errors
        assert target.joinpath("ok-nte.exe").is_file()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
