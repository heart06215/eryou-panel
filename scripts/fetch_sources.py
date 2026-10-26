"""把上游源码快照下载到 sources/<项目>，跳过图片/模型等大资源。

- 只写本项目的 sources/，不触碰已安装的开源项目目录。
- 代码快照用于功能审计和源码模式；运行资产另行处理。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "eryou-panel-source-snapshot/1.0"

PROJECTS = {
    "ok-nte": ("BnanZ0/ok-nte", "main"),
    "ok-ww-enhanced": ("zzc-tongji/ok-ww-enhanced", "main"),
    "MAA": ("MaaAssistantArknights/MaaAssistantArknights", "dev-v2"),
    "ok-script": ("ok-oldking/ok-script", "master"),
}

# 代码/配置/文档；跳过大体积图片、模型、音频。
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


def _get(url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", UA)
    return requests.get(url, headers=headers, timeout=(20, 90), **kwargs)


def _tree(repo: str, branch: str) -> tuple[str, list[dict]]:
    r = _get(f"{API}/repos/{repo}/git/trees/{branch}?recursive=1")
    r.raise_for_status()
    data = r.json()
    return str(data.get("sha", "")), list(data.get("tree", []))


def _tag(repo: str) -> str:
    try:
        r = _get(f"{API}/repos/{repo}/releases/latest")
        if r.status_code == 200:
            return str(r.json().get("tag_name", ""))
    except Exception:
        pass
    return ""


def _download(item: dict, repo: str, branch: str, destination: Path) -> tuple[str, int]:
    path = str(item.get("path", ""))
    size = int(item.get("size", 0) or 0)
    if not path or not item.get("type") == "blob":
        return path, 0
    if size and size > MAX_BYTES:
        return path, 0
    parts = {part.lower() for part in Path(path).parts[:-1]}
    if parts & SKIP_DIRS:
        return path, 0
    if Path(path).suffix.lower() not in CODE_EXT:
        return path, 0
    url = f"{RAW}/{repo}/{branch}/{requests.utils.quote(path)}"
    target = destination / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == size:
        return path, target.stat().st_size
    for attempt in range(3):
        try:
            r = _get(url)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            target.write_bytes(r.content)
            return path, len(r.content)
        except Exception:
            time.sleep(0.5 + attempt)
    return path, 0


def snapshot(name: str, repo: str, branch: str) -> dict:
    destination = SOURCES / name
    destination.mkdir(parents=True, exist_ok=True)
    sha, tree = _tree(repo, branch)
    candidates = [item for item in tree if item.get("type") == "blob"]
    written = 0
    total = 0
    skipped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_download, item, repo, branch, destination) for item in candidates]
        for future in concurrent.futures.as_completed(futures):
            _path, size = future.result()
            if size:
                written += 1
                total += size
            else:
                skipped += 1
    manifest = {
        "name": name,
        "repo": repo,
        "branch": branch,
        "latest_tag": _tag(repo),
        "tree_sha": sha,
        "files": len(candidates),
        "downloaded": written,
        "skipped": skipped,
        "bytes": total,
        "mode": "code snapshot (assets/binaries excluded)",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (destination / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("projects", nargs="*", choices=sorted(PROJECTS))
    args = parser.parse_args()
    wanted = args.projects or list(PROJECTS)
    SOURCES.mkdir(parents=True, exist_ok=True)
    results = []
    for name in wanted:
        repo, branch = PROJECTS[name]
        print(f"== {name} {repo}@{branch}")
        try:
            results.append(snapshot(name, repo, branch))
            print("  ok", results[-1])
        except Exception as exc:
            print("  failed", type(exc).__name__, exc)
    (SOURCES / "manifest.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
