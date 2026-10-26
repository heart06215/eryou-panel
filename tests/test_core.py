from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from eryou_panel.adapters import MaaAdapter, OkScriptAdapter, make_adapter
from eryou_panel.config import Config
from eryou_panel.discovery import detect_kind, looks_like_install, probe
from eryou_panel.models import Runtime, TaskItem
from eryou_panel.services import HistoryStore, LogTailer, ProcessService
from eryou_panel.tools import apply_scan_results


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    cfg = Config(tmp_path / "panel.json")
    cfg.projects[0].directory = str(FIX / "ok-nte")
    cfg.projects[0].runner = "python"
    cfg.projects[1].directory = str(FIX / "ok-ww")
    cfg.projects[1].runner = "python"
    cfg.projects[2].directory = str(FIX / "maa")
    cfg.projects[2].exe = "MAA.exe"
    cfg.save()
    return cfg


def test_config_roundtrip(tmp_path: Path) -> None:
    cfg = Config(tmp_path / "panel.json")
    assert [p.id for p in cfg.projects] == ["ok-nte", "ok-ww-enhanced", "maa", "better-genshin-impact", "march7th-assistant", "azur-lane-auto-script", "blue-archive-auto-script", "onmyoji-auto-script", "honkai-helper", "ok-gf2", "zenless-zone-zero-one-dragon", "fgo-fga", "uma-android-automation"]
    cfg.projects[0].directory = r"D:\Games\ok-nte"
    cfg.save()
    again = Config(tmp_path / "panel.json")
    assert again.projects[0].directory == r"D:\Games\ok-nte"
    assert again.projects[0].game == "异环"


def test_ok_tasks(config: Config) -> None:
    adapter = make_adapter(config.projects[0])
    assert isinstance(adapter, OkScriptAdapter)
    tasks = adapter.tasks()
    keys = [t.key for t in tasks]
    assert "src.tasks.daily.DailyRoutineTask.DailyRoutineTask" in keys
    assert "ok.DiagnosisTask" in keys
    daily = next(t for t in tasks if t.key == "src.tasks.daily.DailyRoutineTask.DailyRoutineTask")
    assert daily.name == "日常流程"
    assert daily.kind == "onetime"
    assert any(t.kind == "trigger" for t in tasks)
    command = adapter.task_command(daily)
    assert command[-1] == "-h"
    assert "-t" in command
    assert adapter.runner() == "python"
    assert adapter.log_files(), "应能发现 logs/*.log"


def test_maa_tasks(config: Config) -> None:
    adapter = make_adapter(config.projects[2])
    assert isinstance(adapter, MaaAdapter)
    assert adapter.gui_exe().lower().endswith("maa.exe")
    assert adapter.cli().lower().endswith("maa-cli.exe")
    tasks = adapter.tasks()
    keys = {t.key for t in tasks}
    assert {"startup", "fight", "copilot"} <= keys
    assert "daily" in keys, "应读取 config/tasks 下的自定义任务"
    fight = next(t for t in tasks if t.key == "fight")
    assert fight.arg_prompt
    command = adapter.task_command(fight, "1-7")
    assert command[-1] == "1-7"
    assert command[0] == adapter.cli()


def test_probe() -> None:
    project = Config().projects[0]
    info = probe(project, str(FIX / "ok-nte"))
    assert info["kind"] == "okscript"
    assert info["runner"] == "python"
    assert detect_kind(str(FIX / "maa")) == "maa"
    info = probe(Config().projects[2], str(FIX / "maa"))
    assert info["kind"] == "maa"
    assert info["exe"].lower() == "maa.exe"


def test_looks_like_install_rejects_game_folder(tmp_path: Path) -> None:
    game = tmp_path / "Wuthering Waves"
    game.mkdir()
    (game / "Wuthering Waves.exe").write_bytes(b"MZ")
    assert not looks_like_install(Config().projects[1], str(game))
    assert looks_like_install(Config().projects[1], str(FIX / "ok-ww"))
    assert looks_like_install(Config().projects[2], str(FIX / "maa"))


def test_apply_scan_results(tmp_path: Path) -> None:
    cfg = Config(tmp_path / "panel.json")
    cfg.projects[0].directory = ""
    applied = apply_scan_results(cfg, {"ok-nte": str(FIX / "ok-nte")})
    assert applied == ["ok-nte"]
    assert cfg.projects[0].runner == "python"
    assert cfg.projects[0].directory == str(FIX / "ok-nte")
    # 已有有效目录不被覆盖
    applied = apply_scan_results(cfg, {"ok-nte": str(FIX / "ok-ww")})
    assert applied == []
    assert cfg.projects[0].directory == str(FIX / "ok-nte")


def test_log_tailer(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    log.write_text("one\ntwo\n", encoding="utf-8")
    tailer = LogTailer()
    assert tailer.reset(str(log), 10) == ["one", "two"]
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("three\nfour")
    lines, reset = tailer.poll()
    assert lines == ["three"]
    assert not reset
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(" continued\n")
    lines, reset = tailer.poll()
    assert lines == ["four continued"]


def test_process_service_runs_task(config: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eryou_panel.services.data_dir", lambda: tmp_path / "data")
    history = HistoryStore(tmp_path / "history.jsonl")
    service = ProcessService(history)
    project = config.projects[0]
    runtime = Runtime(project=project)
    adapter = make_adapter(project)
    task = next(t for t in adapter.tasks() if t.key == "src.tasks.daily.DailyRoutineTask.DailyRoutineTask")
    service.start_task(runtime, adapter, task)
    assert runtime.log_path
    deadline = time.time() + 15
    while time.time() < deadline and runtime.status == "running":
        time.sleep(0.1)
    assert runtime.status == "stopped"
    text = Path(runtime.log_path).read_text(encoding="utf-8")
    assert "fixture main start" in text
    assert "src.tasks.daily.DailyRoutineTask" in text
    assert "退出码: 0" in text
    assert history.recent(project.id)
