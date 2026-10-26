from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from eryou_panel.display_control import DisplayInfo
from eryou_panel.gui.pages import ConfigPage
from eryou_panel.models import Project


def _display(device: str, name: str, internal: bool, primary: bool, y: int) -> DisplayInfo:
    return DisplayInfo(device=device, monitor_id=f"MONITOR\\{name}", name=name,
                       vendor="AUO" if internal else "HKC", internal=internal,
                       primary=primary, x=0, y=y, width=1920, height=1080)


def test_config_page_display_card_emits_selected_display() -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(id="ok-nte", game="异环", name="ok-nte", repo="", game_kind="pc")
    page = ConfigPage()
    internal = _display("\\\\.\\DISPLAY1", "内置屏幕", True, False, 1080)
    external = _display("\\\\.\\DISPLAY2", "外接显示器", False, True, 0)
    selected: list[str] = []
    page.game_display_changed.connect(selected.append)
    try:
        page.set_display_settings(project, [internal, external], external.device, True)
        page.show()
        app.processEvents()
        assert page.display_card.isVisible()
        assert page.display_combo.count() == 2
        assert page.current_display_key() == external.device
        assert page.background_enabled() is True
        page.display_combo.setCurrentIndex(0)
        app.processEvents()
        assert selected[-1] == internal.device
    finally:
        page.deleteLater()
        app.processEvents()
