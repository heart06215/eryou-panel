"""GUI 打包自检：离屏创建悬浮窗并截图，验证 Qt/qfluentwidgets 资源。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _write_fixture(base: Path, project: str) -> Path:
    """在临时目录里造一个最小项目，避免依赖仓库里的 tests/。"""
    root = base / project
    root.mkdir(parents=True, exist_ok=True)
    (root / "ok-nte.exe").write_bytes(b"MZ")
    (root / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "config.py").write_text(
        'config = {"onetime_tasks": [\n'
        '  ["src.tasks.LauncherTask", "LauncherTask"],\n'
        '  ["src.tasks.daily.DailyRoutineTask", "DailyRoutineTask"],\n'
        '  ["src.tasks.FishingTask", "FishingTask"],\n'
        '], "trigger_tasks": [["src.tasks.trigger.AutoCombatTask", "AutoCombatTask"]]}\n',
        encoding="utf-8",
    )
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "ok-nte.log").write_text("fixture log\n", encoding="utf-8")
    return root


def run_selftest() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from ..config import Config
        from .theme import apply_theme
        from .window import FloatingPanel

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from ..util import resource_path

            assert resource_path("assets/panel.png").is_file(), "托盘图标资源未打包"
            fixture = _write_fixture(root / "fixtures", "ok-nte")
            config = Config(root / "panel.json")
            config.projects[0].directory = str(fixture)
            config.projects[0].runner = "exe"
            config.projects[1].directory = ""
            config.projects[2].directory = ""
            for project in config.projects:
                project.repo = ""
            config.install_root = str(root / "installs")
            config.ui["compact"] = False
            config.save()

            app = QApplication.instance() or QApplication([])
            apply_theme(app)
            window = FloatingPanel(config, compact=False)
            window.show()
            app.processEvents()
            rows = len(window.tasks.tasks)
            assert not window.tasks.gui_card.isHidden(), "原项目操作卡未显示"
            assert window.tasks.table.isHidden(), "任务表应隐藏"
            assert "打开原项目" in window.tasks.gui_btn.text()
            assert window.tabs.count() == 2
            png = root / "gui.png"
            window.grab().save(str(png))
            assert png.is_file() and png.stat().st_size > 1000
            shot = os.environ.get("ERYOU_SELFTEST_SHOT", "")
            if shot:
                Path(shot).parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(shot)
            window.set_compact(True)
            app.processEvents()
            compact = root / "compact.png"
            window.grab().save(str(compact))
            assert compact.is_file() and compact.stat().st_size > 1000
            if shot:
                window.grab().save(str(Path(shot).with_name(Path(shot).stem + "-compact.png")))
            window.hide()
            window.deleteLater()
            print(f"SELFTEST OK · GUI · upstream_tasks={rows} · screenshot={png.stat().st_size}B"
                  + (f" · {shot}" if shot else ""))
            return 0
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"SELFTEST FAILED: {type(exc).__name__}: {exc}")
        return 1
