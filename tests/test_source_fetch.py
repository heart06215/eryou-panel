from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eryou_panel import source_fetch


def test_source_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(source_fetch, "projects_dir", lambda: tmp_path / "projects")
    for name in ("ok-nte", "ok-ww-enhanced"):
        folder = tmp_path / "projects" / "sources" / name
        folder.mkdir(parents=True)
        (folder / "main.py").write_text("print('x')", encoding="utf-8")
        (folder / "SOURCE_MANIFEST.json").write_text(
            json.dumps({"name": name, "mode": "code snapshot", "downloaded": 3}),
            encoding="utf-8",
        )
    status = source_fetch.source_status()
    assert status["ok-nte"]["exists"] is True
    assert status["ok-nte"]["files"] >= 2
    assert status["MAA"]["exists"] is False
    assert "ok-script" in status
