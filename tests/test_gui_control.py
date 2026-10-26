from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eryou_panel.gui_control import _find_task_card, minimize_windows_for_pid, queue_tasks_in_gui


class _Info:
    def __init__(self, control_type: str) -> None:
        self.control_type = control_type


class _Control:
    def __init__(self, control_type: str, text: str) -> None:
        self.element_info = _Info(control_type)
        self._text = text

    def window_text(self) -> str:
        return self._text


class _Window:
    def __init__(self, controls: list[_Control]) -> None:
        self._controls = controls

    def descendants(self):
        return self._controls


def test_empty_pid_is_safe() -> None:
    assert minimize_windows_for_pid(0) == 0
    with pytest.raises(RuntimeError):
        queue_tasks_in_gui(0, ["测试任务"])


def test_task_card_uses_document_order_not_screen_geometry() -> None:
    # 模拟最小化后 rectangle() 失效的 Qt 任务列表：标题 -> Start -> 标题 -> Start。
    first = _Control("Button", "Start")
    second = _Control("Button", "Start")
    window = _Window([
        _Control("Text", "日常任务"),
        first,
        _Control("Text", "自动钓鱼"),
        second,
    ])
    assert _find_task_card(window, ["日常任务"]) is first
    assert _find_task_card(window, ["自动钓鱼"]) is second
