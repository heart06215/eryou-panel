from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eryou_panel.config import Config
from eryou_panel.maa_support import find_adb, mumu_address, prepare_maa, start_mumu


def test_mumu_address_and_adb(tmp_path: Path) -> None:
    config = Config(tmp_path / "panel.json")
    project = config.projects[2]
    project.game_dir = str(tmp_path / "MuMu Player 12" / "nx_main")
    project.game_exe = str(tmp_path / "MuMu Player 12" / "nx_main" / "MuMuNxMain.exe")
    project.game_process = "MuMuNxMain.exe"
    adb = Path(project.game_dir) / "adb.exe"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"MZ")
    (tmp_path / "MuMu Player 12" / "vms" / "MuMuPlayer-12.0-0" / "configs").mkdir(parents=True)
    (tmp_path / "MuMu Player 12" / "vms" / "MuMuPlayer-12.0-0" / "configs" / "vm_config.json").write_text(
        json.dumps({"vm": {"nat": {"port_forward": {"adb": {"host_port": "16444"}}}}}),
        encoding="utf-8",
    )
    assert find_adb(project) == str(adb)
    assert mumu_address(project) == "127.0.0.1:16444"


def test_prepare_maa_configures_profile(tmp_path: Path, monkeypatch) -> None:
    config = Config(tmp_path / "panel.json")
    project = config.projects[2]
    maa_dir = tmp_path / "MAA"
    maa_dir.mkdir()
    (maa_dir / "MAA.exe").write_bytes(b"MZ")
    (maa_dir / "maa-cli.exe").write_bytes(b"MZ")
    project.directory = str(maa_dir)
    project.game_dir = str(tmp_path / "MuMu" / "nx_main")
    (tmp_path / "MuMu" / "nx_main").mkdir(parents=True)
    adb = tmp_path / "MuMu" / "nx_main" / "adb.exe"
    adb.write_bytes(b"MZ")
    project.game_adb = str(adb)
    project.extra["maa_address"] = "127.0.0.1:16384"

    monkeypatch.setattr("eryou_panel.adapters.data_dir", lambda: tmp_path / "panel-data")
    monkeypatch.setattr("eryou_panel.maa_support.data_dir", lambda: tmp_path / "panel-data")
    (tmp_path / "panel-data" / "maa-runtime").mkdir(parents=True)
    (tmp_path / "panel-data" / "maa-runtime" / "maa-cli.exe").write_bytes(b"MZ")
    monkeypatch.setattr("eryou_panel.maa_support.first_ready_device", lambda _adb, _preferred="": "127.0.0.1:16384")
    monkeypatch.setattr("eryou_panel.maa_support.start_emulator", lambda *args, **kwargs: False)

    progress: list[float] = []
    result = prepare_maa(project, tmp_path / "panel", launch_emulator=True,
                         progress=lambda value, _message: progress.append(value))
    assert result.address == "127.0.0.1:16384"
    assert Path(result.path) == maa_dir
    profile = (tmp_path / "panel-data" / "maa-runtime" / "config" / "profiles" / "default.toml").read_text(
        encoding="utf-8")
    assert "127.0.0.1:16384" in profile
    assert progress and progress[-1] == 100


def test_start_mumu_waits_for_adb(tmp_path: Path, monkeypatch) -> None:
    config = Config(tmp_path / "panel.json")
    project = config.projects[2]
    maa_dir = tmp_path / "MAA"
    maa_dir.mkdir()
    (maa_dir / "MAA.exe").write_bytes(b"MZ")
    project.directory = str(maa_dir)
    project.game_dir = str(tmp_path / "MuMu" / "nx_main")
    (tmp_path / "MuMu" / "nx_main").mkdir(parents=True)
    adb = tmp_path / "MuMu" / "nx_main" / "adb.exe"
    adb.write_bytes(b"MZ")
    project.game_adb = str(adb)
    project.extra["maa_address"] = "127.0.0.1:16444"

    monkeypatch.setattr("eryou_panel.adapters.data_dir", lambda: tmp_path / "panel-data")
    monkeypatch.setattr("eryou_panel.maa_support.first_ready_device", lambda _adb, _preferred="": "127.0.0.1:16444")
    monkeypatch.setattr("eryou_panel.maa_support.start_emulator", lambda *args, **kwargs: False)

    progress: list[float] = []
    result = start_mumu(project, progress=lambda value, _message: progress.append(value))
    assert result.address == "127.0.0.1:16444"
    assert result.adb == str(adb)
    assert progress[-1] == 100
    profile = (tmp_path / "panel-data" / "maa-runtime" / "config" / "profiles" / "default.toml").read_text(
        encoding="utf-8")
    assert "127.0.0.1:16444" in profile
