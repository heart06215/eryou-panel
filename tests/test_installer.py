from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eryou_panel.config import Config
from eryou_panel.installer import InstallError, find_app_root, install_project


def _make_zip(path: Path, prefix: str = "ok-nte") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{prefix}/ok-nte.exe", b"MZ")
        zf.writestr(f"{prefix}/data/file.txt", "hello")
    return path


def test_find_app_root(tmp_path: Path) -> None:
    extracted = tmp_path / "extract"
    (extracted / "pkg" / "nested").mkdir(parents=True)
    (extracted / "pkg" / "nested" / "ok-nte.exe").write_bytes(b"MZ")
    project = Config(tmp_path / "panel.json").projects[0]
    assert find_app_root(extracted, project) == extracted / "pkg" / "nested"


def test_install_worker_continues_after_failure(tmp_path: Path, monkeypatch) -> None:
    from eryou_panel.gui.workers import InstallWorker
    from eryou_panel.installer import InstallResult

    config = Config(tmp_path / "panel.json")
    projects = [config.projects[0], config.projects[1]]
    calls: list[str] = []

    def fake_install(project, root, force=False, token="", progress=None, log=None):
        calls.append(project.id)
        if project.id == "ok-nte":
            raise RuntimeError("模拟网络失败")
        target = tmp_path / "installs" / project.id
        target.mkdir(parents=True)
        (target / "main.exe").write_bytes(b"MZ")
        if progress:
            progress(50, "fake")
        return InstallResult(project_id=project.id, project_name=project.name,
                             path=str(target), tag="v-test", asset="fake.zip", size=1)

    monkeypatch.setattr("eryou_panel.gui.workers.install_project", fake_install)
    worker = InstallWorker(projects, tmp_path / "installs")
    summaries: list[tuple[int, int, list[str]]] = []
    done: list[str] = []
    worker.all_finished.connect(
        lambda success, total, errors: summaries.append((success, total, list(errors))))
    worker.one_done.connect(lambda result: done.append(result.project_id))
    worker.run()
    assert calls == ["ok-nte", "ok-ww-enhanced"]
    assert done == ["ok-ww-enhanced"]
    assert summaries and summaries[0][:2] == (1, 2)
    assert "ok-nte" in summaries[0][2][0] or "异环" in summaries[0][2][0]


def test_install_project_offline(tmp_path: Path, monkeypatch) -> None:
    project = Config(tmp_path / "panel.json").projects[0]
    archive = _make_zip(tmp_path / "pkg.zip")
    size = archive.stat().st_size

    def fake_release(repo, token="", timeout=25):
        return {
            "tag_name": "v-test",
            "assets": [{
                "name": "ok-nte-win-x86_64-v1.0-full.zip",
                "size": size,
                "browser_download_url": "https://example.invalid/pkg.zip",
            }],
        }

    def fake_download(asset, destination, token="", progress=None, log=None):
        shutil.copy2(archive, destination)
        if progress:
            progress(85, "下载完成")

    monkeypatch.setattr("eryou_panel.installer.latest_release", fake_release)
    monkeypatch.setattr("eryou_panel.installer.download_asset", fake_download)

    logs: list[str] = []
    root = tmp_path / "panel"
    result = install_project(project, root, progress=lambda p, m: None, log=logs.append)
    target = root / "ok-nte"
    assert Path(result.path) == target
    assert (target / "ok-nte.exe").is_file()
    assert (target / "data" / "file.txt").read_text(encoding="utf-8") == "hello"
    marker = json.loads((root / ".eryou-install" / "ok-nte.json").read_text(encoding="utf-8"))
    assert marker["tag"] == "v-test"
    assert any("已安装到" in line for line in logs)

    # 不带 force 时拒绝覆盖
    with pytest.raises(InstallError):
        install_project(project, root)
    # force 覆盖成功
    result = install_project(project, root, force=True)
    assert Path(result.path) == target
    assert (target / "ok-nte.exe").is_file()
