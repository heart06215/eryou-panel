from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eryou_panel.config import Config
from eryou_panel.games import (PC_SPECS, _find_in_dir, actual_game_exe, apply_hit,
                              game_workdir, GameHit)


def test_find_native_game_launcher(tmp_path: Path) -> None:
    nte = tmp_path / "Neverness To Everness"
    nte.mkdir()
    (nte / "NTELauncher.exe").write_bytes(b"MZ")
    (nte / "Client").mkdir()
    (nte / "Client" / "HTGame.exe").write_bytes(b"MZ")
    found = _find_in_dir(str(nte), PC_SPECS["ok-nte"])
    assert found is not None
    game_dir, launch, processes = found
    assert game_dir == str(nte)
    assert launch.endswith("NTELauncher.exe")
    assert "HTGame.exe" in processes


def test_find_wuthering_launcher(tmp_path: Path) -> None:
    root = tmp_path / "Wuthering Waves"
    game = root / "Wuthering Waves Game"
    game.mkdir(parents=True)
    (root / "launcher.exe").write_bytes(b"MZ")
    (game / "Wuthering Waves.exe").write_bytes(b"MZ")
    found = _find_in_dir(str(root), PC_SPECS["ok-ww-enhanced"])
    assert found is not None
    game_dir, launch, processes = found
    assert game_dir == str(root)
    assert launch.endswith("launcher.exe")
    assert any("Wuthering" in name for name in processes)


def test_apply_hit(tmp_path: Path) -> None:
    project = Config(tmp_path / "panel.json").projects[0]
    hit = GameHit(project_id=project.id, kind="pc", launch_exe=r"D:\Games\NTE\NTELauncher.exe",
                  game_dir=r"D:\Games\NTE", processes=["HTGame.exe"], detail="已找到")
    assert apply_hit(project, hit)
    assert project.game_exe.endswith("NTELauncher.exe")
    assert project.game_process == "HTGame.exe"
    assert not apply_hit(project, hit)


def test_actual_game_exe_and_workdir(tmp_path: Path) -> None:
    config = Config(tmp_path / "panel.json")
    nte = config.projects[0]
    nte_root = tmp_path / "Neverness To Everness"
    nte_game = nte_root / "Client" / "WindowsNoEditor" / "HT" / "Binaries" / "Win64" / "HTGame.exe"
    nte_game.parent.mkdir(parents=True)
    nte_game.write_bytes(b"MZ")
    nte.game_dir = str(nte_root)
    nte.game_exe = str(nte_root / "NTELauncher.exe")
    assert actual_game_exe(nte) == str(nte_game)
    assert game_workdir(nte, str(nte_game)) == str(nte_root)

    ww = config.projects[1]
    ww_root = tmp_path / "Wuthering Waves"
    ww_game_dir = ww_root / "Wuthering Waves Game"
    ww_game = ww_game_dir / "Client" / "Binaries" / "Win64" / "Client-Win64-Shipping.exe"
    ww_game.parent.mkdir(parents=True)
    ww_game.write_bytes(b"MZ")
    ww.game_dir = str(ww_root)
    ww.game_exe = str(ww_root / "launcher.exe")
    assert actual_game_exe(ww) == str(ww_game)
    assert game_workdir(ww, str(ww_game)) == str(ww_game_dir)
