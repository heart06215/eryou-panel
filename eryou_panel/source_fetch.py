"""把上游源码快照下载到 sources/<项目>，供面板的一键源码下载使用。

只写 sources/；图片、模型、OCR 资源不下载，避免和已安装项目互相污染。
"""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from typing import Callable

import requests

from .util import app_dir, projects_dir

Progress = Callable[[float, str], None]
Log = Callable[[str], None]
Cancel = Callable[[], bool]

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "eryou-panel-source-fetcher/1.0"

SOURCE_PROJECTS: dict[str, tuple[str, str]] = {
    "ok-nte": ("BnanZ0/ok-nte", "main"),
    "ok-ww-enhanced": ("zzc-tongji/ok-ww-enhanced", "main"),
    "MAA": ("MaaAssistantArknights/MaaAssistantArknights", "dev-v2"),
    "ok-script": ("ok-oldking/ok-script", "master"),
}

CODE_EXT = {
    ".py", ".pyi", ".cs", ".cpp", ".c", ".h", ".hpp", ".rs", ".go",
    ".toml", ".yaml", ".yml", ".ini", ".cfg", ".txt", ".md",
    ".ps1", ".bat", ".cmd", ".sh", ".html", ".css", ".js", ".ts",
    ".qss", ".qrc", ".ui", ".proto", ".cmake", ".gradle",
    ".xml", ".props", ".targets", ".vcxproj", ".sln", ".filters",
    ".json",
}
SKIP_DIRS = {
    "assets", "icons", "i18n", "resource", "resources", "images", "image",
    "docs", "documentation", "node_modules", ".git", "dist", "build",
    "test", "tests", "fixtures", "PaddleOCR", "PaddleCharOCR", "onnx",
    "Arknights-Tile-Pos", "copilot", "minitouch", "Res", "Python",
}
MAX_BYTES = 2 * 1024 * 1024

_session = threading.local()


def _session_for_thread() -> requests.Session:
    session = getattr(_session, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        _session.session = session
    return session


def source_root() -> Path:
    return projects_dir() / "sources"


def _report(progress: Progress | None, value: float, message: str) -> None:
    if progress:
        progress(max(0.0, min(100.0, value)), message)


def _log(log: Log | None, message: str) -> None:
    if log:
        log(message)


def _tree(repo: str, branch: str) -> tuple[str, list[dict]]:
    response = _session_for_thread().get(
        f"{API}/repos/{repo}/git/trees/{branch}?recursive=1", timeout=(20, 90))
    response.raise_for_status()
    data = response.json()
    return str(data.get("sha", "")), list(data.get("tree", []))


def _latest_tag(repo: str) -> str:
    try:
        response = _session_for_thread().get(
            f"{API}/repos/{repo}/releases/latest", timeout=20)
        if response.status_code == 200:
            return str(response.json().get("tag_name", ""))
    except Exception:
        pass
    return ""


def _candidate(item: dict) -> bool:
    if item.get("type") != "blob":
        return False
    path = str(item.get("path", ""))
    if not path:
        return False
    if int(item.get("size", 0) or 0) > MAX_BYTES:
        return False
    parts = {part.lower() for part in Path(path).parts[:-1]}
    if parts & SKIP_DIRS:
        return False
    return Path(path).suffix.lower() in CODE_EXT


def _download(item: dict, repo: str, branch: str, destination: Path,
              cancel: Cancel | None) -> tuple[str, int]:
    path = str(item.get("path", ""))
    size = int(item.get("size", 0) or 0)
    target = destination / path
    if target.is_file() and (not size or target.stat().st_size == size):
        return path, target.stat().st_size
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{RAW}/{repo}/{branch}/{requests.utils.quote(path)}"
    urls = (
        url,
        "https://ghfast.top/" + url,
        "https://gh-proxy.com/" + url,
    )
    for attempt in range(6):
        if cancel and cancel():
            return path, 0
        try:
            response = _session_for_thread().get(urls[attempt % len(urls)], timeout=(20, 90))
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            target.write_bytes(response.content)
            return path, len(response.content)
        except Exception:
            time.sleep(0.4 + attempt * 0.5)
    return path, 0


def fetch_source(name: str, progress: Progress | None = None,
                 log: Log | None = None, cancel: Cancel | None = None) -> dict:
    if name not in SOURCE_PROJECTS:
        raise ValueError("未知源码项目：" + name)
    repo, branch = SOURCE_PROJECTS[name]
    destination = source_root() / name
    destination.mkdir(parents=True, exist_ok=True)
    _report(progress, 0, f"读取 {name} 源码清单…")
    sha, tree = _tree(repo, branch)
    items = [item for item in tree if _candidate(item)]
    total = max(1, len(items))
    _log(log, f"{name}: {len(items)} 个源码文件")
    written = skipped = total_bytes = 0
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_download, item, repo, branch, destination, cancel): item
            for item in items
        }
        for future in concurrent.futures.as_completed(futures):
            if cancel and cancel():
                for pending in futures:
                    pending.cancel()
                raise RuntimeError("已取消源码下载")
            path, size = future.result()
            done += 1
            if size:
                written += 1
                total_bytes += size
            else:
                skipped += 1
            _report(progress, done / total * 94,
                    f"{name} {done}/{total} · {Path(path).name}")
    manifest = {
        "name": name,
        "repo": repo,
        "branch": branch,
        "latest_tag": _latest_tag(repo),
        "tree_sha": sha,
        "files": len(tree),
        "downloaded": written,
        "skipped": skipped,
        "bytes": total_bytes,
        "mode": "code snapshot (assets/binaries excluded)",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (destination / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _report(progress, 97, f"{name} 源码快照完成")
    return manifest


def fetch_sources(names: list[str], progress: Progress | None = None,
                  log: Log | None = None, cancel: Cancel | None = None) -> list[dict]:
    results: list[dict] = []
    for index, name in enumerate(names):
        def project_progress(value: float, message: str, idx=index) -> None:
            overall = (idx + max(0.0, min(100.0, value)) / 100.0) / max(1, len(names)) * 100.0
            _report(progress, overall, f"({idx + 1}/{len(names)}) {message}")
        results.append(fetch_source(name, project_progress, log, cancel))
    source_root().mkdir(parents=True, exist_ok=True)
    (source_root() / "manifest.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _report(progress, 100, "全部源码快照完成")
    return results


def source_status() -> dict[str, dict]:
    result: dict[str, dict] = {}
    root = source_root()
    for name in SOURCE_PROJECTS:
        folder = root / name
        manifest = folder / "SOURCE_MANIFEST.json"
        meta: dict = {"exists": folder.is_dir(), "files": 0, "bytes": 0, "tag": ""}
        if manifest.is_file():
            try:
                meta.update(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        if folder.is_dir():
            files = [p for p in folder.rglob("*") if p.is_file()]
            meta["files"] = len(files)
            meta["bytes"] = sum(p.stat().st_size for p in files)
        result[name] = meta
    return result
