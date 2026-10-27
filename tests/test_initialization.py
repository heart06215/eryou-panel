from __future__ import annotations

import json
from pathlib import Path

from eryou_panel.adapters import make_adapter
from eryou_panel.models import Project


def test_okscript_launcher_requires_official_installation(tmp_path: Path) -> None:
    root = tmp_path / "ok-ww"
    root.mkdir()
    (root / "ok-ww-e.exe").write_bytes(b"MZ")
    app = root / "data" / "apps" / "ok-ww-e"
    app.mkdir(parents=True)
    (app / "app.json").write_text(json.dumps({"installed": False, "current_version": None}), encoding="utf-8")
    project = Project(id="ok-ww-enhanced", game="鸣潮", name="ok-ww-enhanced", repo="x/y",
                      kind="okscript", directory=str(root), exe="ok-ww-e.exe")
    adapter = make_adapter(project)
    assert adapter.initialized is False
    assert "需首次初始化" in adapter.mode_text()

    (app / "app.json").write_text(
        json.dumps({"installed": True, "current_version": "v1.0.0"}), encoding="utf-8")
    assert adapter.initialized is True


def test_maa_requires_core_files(tmp_path: Path) -> None:
    root = tmp_path / "MAA"
    root.mkdir()
    (root / "MAA.exe").write_bytes(b"MZ")
    (root / "MaaCore.dll").write_bytes(b"MZ")
    (root / "resource").mkdir()
    project = Project(id="maa", game="明日方舟", name="MAA", repo="x/y",
                      kind="maa", directory=str(root), exe="MAA.exe")
    adapter = make_adapter(project)
    assert adapter.initialized is True
    (root / "MaaCore.dll").unlink()
    assert adapter.initialized is False
