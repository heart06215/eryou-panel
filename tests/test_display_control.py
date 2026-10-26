from __future__ import annotations

from eryou_panel.display_control import (DisplayInfo, default_display,
                                         resolve_display)


def _display(device: str, name: str, internal: bool, primary: bool,
             x: int = 0, y: int = 0) -> DisplayInfo:
    return DisplayInfo(device=device, monitor_id=f"MONITOR\\{name}", name=name,
                       vendor="AUO" if internal else "HKC", internal=internal,
                       primary=primary, x=x, y=y, width=1920, height=1080)


def test_default_display_prefers_internal_panel() -> None:
    external = _display("\\\\.\\DISPLAY2", "外接显示器", False, True)
    internal = _display("\\\\.\\DISPLAY1", "内置屏幕", True, False, y=1080)
    assert default_display([external, internal]) is internal
    assert default_display([external]) is external


def test_resolve_display_by_device_and_monitor_id() -> None:
    internal = _display("\\\\.\\DISPLAY1", "内置屏幕", True, False, y=1080)
    external = _display("\\\\.\\DISPLAY2", "外接显示器", False, True)
    displays = [internal, external]
    assert resolve_display(internal.device, displays) is internal
    assert resolve_display(external.monitor_id, displays) is external
    assert resolve_display("primary", displays) is external
    assert resolve_display("missing", displays) is internal


def test_display_label_has_resolution_and_primary_marker() -> None:
    external = _display("\\\\.\\DISPLAY2", "外接显示器", False, True)
    assert "1920×1080" in external.label
    assert "主屏" in external.label
