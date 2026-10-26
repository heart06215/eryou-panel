from __future__ import annotations

import json
from pathlib import Path

from eryou_panel.installer import _copy_preserved, _preserve_paths, _restore_preserved
from eryou_panel.models import Project
from eryou_panel.updates import installed_tag, is_newer, normalize_tag


def test_version_compare_handles_v_prefix_and_trailing_zero() -> None:
    assert normalize_tag("v6.20.1.0") == "6.20.1"
    assert is_newer("v1.4.18", "1.4.16")
    assert not is_newer("v6.20.1", "6.20.1.0")
    assert is_newer("v3.7.4-rc.0", "1.2.3.0")


def test_installed_tag_reads_install_marker(tmp_path: Path) -> None:
    project = Project(id="ok-nte", game="异环", name="ok-nte", repo="x/y",
                      directory=str(tmp_path / "ok-nte"))
    marker = tmp_path / ".eryou-install" / "ok-nte.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"tag": "v1.4.18"}), encoding="utf-8")
    assert installed_tag(project) == "v1.4.18"


def test_preserve_user_data_helpers(tmp_path: Path) -> None:
    old = tmp_path / "old"
    (old / "data" / "config").mkdir(parents=True)
    (old / "data" / "config" / "user.json").write_text("user", encoding="utf-8")
    preserve = tmp_path / "preserve"
    project = Project(id="ok-nte", game="异环", name="ok-nte", repo="x/y")
    _copy_preserved(old, preserve, _preserve_paths(project))
    new = tmp_path / "new"
    new.mkdir()
    _restore_preserved(preserve, new)
    assert (new / "data" / "config" / "user.json").read_text(encoding="utf-8") == "user"
