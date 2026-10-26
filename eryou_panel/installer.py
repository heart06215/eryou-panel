"""项目安装器：从 GitHub Release 下载并解压到面板目录，绝不写到其它位置。

设计约束：
- 安装根目录固定由 Config.install_root 决定（默认面板 EXE 所在目录）。
- 所有临时文件都放在 <root>/.eryou-install-tmp 下，结束后删除。
- 目标目录固定为 <root>/<install_name>，例如 ok-nte / ok-ww-enhanced / MAA。
- 支持 --force 覆盖：旧目录先备份，失败自动回滚。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config
from .discovery import EXE_HINTS, probe
from .models import Project
from .util import data_dir, projects_dir

Progress = Callable[[float, str], None]
Log = Callable[[str], None]

INSTALL_NAMES = {
    "ok-nte": "ok-nte",
    "ok-ww-enhanced": "ok-ww-enhanced",
    "maa": "MAA",
    "better-genshin-impact": "BetterGI",
    "march7th-assistant": "March7thAssistant",
    "azur-lane-auto-script": "AzurLaneAutoScript",
    "blue-archive-auto-script": "BlueArchiveAutoScript",
    "onmyoji-auto-script": "OnmyojiAutoScript",
    "honkai-helper": "HonkaiHelper",
    "ok-gf2": "ok-gf2",
    "zenless-zone-zero-one-dragon": "ZenlessZoneZero-OneDragon",
}
USER_AGENT = "eryou-panel-installer/1.0"


class InstallError(Exception):
    pass


@dataclass
class InstallResult:
    project_id: str
    project_name: str
    path: str
    tag: str = ""
    asset: str = ""
    size: int = 0
    message: str = ""


@dataclass
class InstallOutcome:
    results: list[InstallResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def install_name(project: Project) -> str:
    return INSTALL_NAMES.get(project.id, project.id or "project")


def latest_release(repo: str, token: str = "", timeout: int = 25) -> dict:
    import requests

    # 自动更新每次都重新向 GitHub 请求最新 Release，不读取任何本地版本缓存，
    # 也不依赖第三方付费更新服务。
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                headers=headers, timeout=timeout)
    except Exception as exc:
        raise InstallError(f"无法访问 GitHub：{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise InstallError(f"获取 Release 失败：HTTP {response.status_code}（仓库 {repo}）")
    data = response.json()
    if not isinstance(data, dict):
        raise InstallError("Release 数据格式异常")
    return data


def choose_asset(project: Project, release: dict) -> dict:
    """从 Release 资产中挑选适合 Windows 的完整压缩包。"""
    assets = [a for a in release.get("assets", []) if a.get("name") and a.get("browser_download_url")]
    preferred = [str(item).lower() for item in project.extra.get("asset_patterns", [])]
    excluded = [str(item).lower() for item in project.extra.get("asset_exclude", [])]
    if preferred:
        candidates = []
        for asset in assets:
            name = str(asset["name"])
            low = name.lower()
            if not low.endswith((".zip", ".7z", ".rar")):
                continue
            if any(item in low for item in excluded):
                continue
            if all(item in low for item in preferred):
                candidates.append(asset)
        if candidates:
            return candidates[0]
    ranked: list[tuple[float, dict]] = []
    for asset in assets:
        name = str(asset["name"])
        low = name.lower()
        if not low.endswith((".zip", ".7z", ".rar")):
            continue
        if any(bad in low for bad in ("linux", "macos", "android", "arm64", "aarch64",
                                       "debug", "symbol", "source")):
            continue
        if not any(tag in low for tag in ("win", "x86_64", "win32", "x64")):
            continue
        score = 0.0
        if "win" in low:
            score += 10
        if "x64" in low or "x86_64" in low:
            score += 8
        if "win32" in low:
            score += 6
        if "full" in low:
            score += 4
        if "body" in low:
            score += 1
        if "setup" in low or "installer" in low:
            score -= 30
        score -= len(low) * 0.01
        ranked.append((score, asset))
    if not ranked and project.extra.get("asset_any"):
        candidates = []
        for asset in assets:
            name = str(asset["name"])
            low = name.lower()
            if not low.endswith((".zip", ".7z", ".rar")):
                continue
            if any(bad in low for bad in ("linux", "macos", "android", "debug", "symbol", "source")):
                continue
            candidates.append(asset)
        if candidates:
            return max(candidates, key=lambda item: int(item.get("size") or 0))
    if not ranked:
        raise InstallError("该 Release 没有可用的 Windows 压缩包，请到 Releases 页面手动下载")
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _fmt_bytes(value: float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _download_sources(url: str) -> list[str]:
    """GitHub Release 直连在部分网络下会超时；优先走公共加速，失败再回源。"""
    sources = [
        "https://gh-proxy.com/" + url,
        "https://ghfast.top/" + url,
        url,
    ]
    unique: list[str] = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    return unique


def _total_from_headers(headers, fallback: int) -> int:
    content_range = headers.get("content-range") or headers.get("Content-Range") or ""
    if "/" in content_range:
        try:
            return int(content_range.rsplit("/", 1)[1])
        except (TypeError, ValueError):
            pass
    try:
        return int(headers.get("content-length") or headers.get("Content-Length") or fallback or 0)
    except (TypeError, ValueError):
        return fallback


def download_asset(asset: dict, destination: Path, token: str = "",
                   progress: Progress | None = None, log: Log | None = None) -> None:
    import requests

    url = str(asset["browser_download_url"])
    expected = int(asset.get("size") or 0)
    if log:
        log(f"下载 {asset['name']}（{_fmt_bytes(expected)}）")
    part = destination.with_name(destination.name + ".part")
    sources = _download_sources(url)
    last_error = ""
    try:
        for attempt in range(9):
            source = sources[attempt % len(sources)]
            have = part.stat().st_size if part.exists() else 0
            if expected and have >= expected:
                break
            headers = {"User-Agent": USER_AGENT}
            try:
                host = source.split("/")[2].lower()
            except IndexError:
                host = ""
            if token and host in ("github.com", "api.github.com", "objects.githubusercontent.com"):
                headers["Authorization"] = "Bearer " + token
            if have:
                headers["Range"] = f"bytes={have}-"
            if log and attempt:
                log(f"切换下载线路（{attempt + 1}/9）…")
            try:
                with requests.get(source, headers=headers, stream=True,
                                  timeout=(20, 60), allow_redirects=True) as response:
                    if response.status_code == 416 and have:
                        break
                    if response.status_code not in (200, 206):
                        raise InstallError(f"HTTP {response.status_code}")
                    content_type = (response.headers.get("content-type") or "").lower()
                    if "text/html" in content_type:
                        try:
                            part.unlink()
                        except OSError:
                            pass
                        raise InstallError("下载源返回网页而不是安装包")
                    total = _total_from_headers(response.headers, expected)
                    if response.status_code == 200 and have:
                        # 该线路不支持 Range，从头开始
                        have = 0
                    mode = "ab" if have else "wb"
                    done = have
                    last_report = 0.0
                    with open(part, mode) as fh:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            if progress:
                                now = time.time()
                                if now - last_report > 0.5 or (total and done >= total):
                                    last_report = now
                                    percent = (done / total * 100.0) if total else 0.0
                                    progress(percent * 0.85,
                                             f"下载中 {_fmt_bytes(done)} / {_fmt_bytes(total)}")
                    if total and done < total:
                        raise InstallError(f"连接提前结束（{_fmt_bytes(done)} / {_fmt_bytes(total)}）")
                    if not total and not done:
                        raise InstallError("没有收到数据")
                    if expected and done >= expected:
                        break
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if log:
                    log(f"线路失败：{last_error}")
                time.sleep(min(3.0, 0.8 + attempt * 0.4))
                continue
        else:
            raise InstallError("下载失败（已尝试直连与镜像）：" + last_error)
        if not part.exists() or part.stat().st_size == 0:
            raise InstallError("下载失败：没有生成安装包")
        if expected and part.stat().st_size < expected:
            raise InstallError("下载文件不完整，请重试")
        os.replace(part, destination)
    finally:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
    if progress:
        progress(85, "下载完成")


def verify_checksum(release: dict, asset: dict, file: Path, token: str = "",
                    log: Log | None = None) -> None:
    """如果 Release 提供 sha256 清单且包含该文件，则校验。"""
    checksum_asset = next(
        (a for a in release.get("assets", []) if "sha256" in str(a.get("name", "")).lower()), None
    )
    if checksum_asset is None:
        return
    import requests

    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = requests.get(checksum_asset["browser_download_url"], headers=headers, timeout=20)
        if response.status_code != 200:
            return
        expected = ""
        for line in response.text.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[-1].lstrip("*").lower() == str(asset["name"]).lower():
                expected = parts[0].lower()
                break
        if not expected:
            return
        digest = hashlib.sha256()
        with open(file, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected:
            raise InstallError("SHA256 校验失败，安装包可能损坏，已停止")
        if log:
            log("SHA256 校验通过")
    except InstallError:
        raise
    except Exception:
        if log:
            log("校验和清单不可用，跳过校验")


def safe_extract_zip(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as zf:
            bad = zf.testzip()
            if bad:
                raise InstallError(f"压缩包损坏：{bad}")
            dest = destination.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or "../" in name or "/../" in name:
                    raise InstallError("压缩包包含非法路径，已拒绝解压")
                target = (dest / name).resolve()
                if target != dest and dest not in target.parents:
                    raise InstallError("压缩包包含路径穿越，已拒绝解压")
                zf.extract(info, destination)
    except zipfile.BadZipFile as exc:
        raise InstallError("不是有效的 ZIP 文件") from exc


def extract_archive(archive: Path, destination: Path, log: Log | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if log:
        log("解压安装包…")
    if archive.suffix.lower() == ".zip":
        safe_extract_zip(archive, destination)
        return
    if archive.suffix.lower() in (".7z", ".rar"):
        tar = shutil.which("tar") or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe")
        try:
            subprocess.run([tar, "-xf", str(archive), "-C", str(destination)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000)
            return
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError("7z 解压失败，请手动解压或在系统安装 tar/7-Zip") from exc
    raise InstallError("不支持的压缩格式：" + archive.suffix)


def _hints(project: Project) -> list[str]:
    hints = [h.lower() for h in project.extra.get("exe_hints", [])]
    hints.extend(h.lower() for h in EXE_HINTS.get(project.id, ()))
    return hints


def _looks_like_main(name: str, project: Project) -> bool:
    low = name.lower()
    if not low.endswith(".exe"):
        return False
    if any(bad in low for bad in ("unins", "uninstall", "update", "updater", "setup", "crash")):
        return False
    if project.id == "maa":
        return low == "maa.exe"
    tokens = ("ok-nte", "oknte", "ok-ww", "okww")
    return any(token in low for token in tokens)


def find_app_root(extracted: Path, project: Project) -> Path:
    """在解压目录中定位包含主程序的根目录。"""
    hints = _hints(project)
    source_ok = project.kind == "okscript"
    best: tuple[int, Path] | None = None
    for current, dirnames, filenames in os.walk(extracted):
        path = Path(current)
        try:
            depth = len(path.relative_to(extracted).parts)
        except ValueError:
            depth = 0
        if depth > 4:
            dirnames[:] = []
            continue
        names = {name.lower() for name in filenames}
        hit = any(hint in names for hint in hints)
        if not hit:
            hit = any(_looks_like_main(name, project) for name in filenames)
        if not hit and source_ok:
            hit = "main.py" in names and (("config.py" in names) or (path / "src" / "config.py").is_file())
        if not hit and project.extra.get("allow_any_exe"):
            hit = any(
                name.lower().endswith(".exe")
                and not any(bad in name.lower() for bad in ("unins", "uninstall", "update", "updater", "setup", "crash"))
                for name in filenames
            )
        if hit:
            if best is None or depth < best[0]:
                best = (depth, path)
        if best is not None and depth >= best[0]:
            dirnames[:] = []
    if best is None:
        raise InstallError("安装包里没有找到主程序（EXE / main.py）")
    return best[1]



def _preserve_paths(project: Project) -> list[str]:
    """更新时保留的用户数据；不保留旧程序/缓存/资源。"""
    if project.id == "ok-nte":
        return [
            "data/app_config.json",
            "data/config",
            "data/configs",
            "data/custom_chars",
            "data/gift_configs",
            "data/ok_tasks",
            "logs",
        ]
    if project.id == "ok-ww-enhanced":
        return ["config", "configs", "data", "logs"]
    if project.id == "maa":
        return ["config", "logs"]
    return ["config", "configs", "data", "logs"]


def _copy_preserved(source: Path, destination: Path, paths: list[str],
                    log: Log | None = None) -> None:
    for relative in paths:
        src = source / relative
        if not src.exists():
            continue
        dst = destination / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        if log:
            log(f"保留用户数据：{relative}")


def _restore_preserved(source: Path, target: Path, log: Log | None = None) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        dst = target / relative
        if item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)
    if log:
        log("用户配置已恢复到新版本")

def _write_marker(target: Path, project: Project, release: dict, asset: dict, source: Path) -> None:
    data = {
        "id": project.id,
        "name": project.name,
        "repo": project.repo,
        "tag": release.get("tag_name", ""),
        "asset": asset.get("name", ""),
        "url": asset.get("browser_download_url", ""),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        marker_dir = target.parent / ".eryou-install"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"{project.id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def install_maa_cli(target: Path, work: Path, token: str = "", log: Log | None = None) -> None:
    """把官方 maa-cli 放到面板自己的 maa-runtime 目录，不修改 MAA 安装目录。"""
    release = latest_release("MaaAssistantArknights/maa-cli", token)
    asset = next(
        (a for a in release.get("assets", [])
         if "x86_64-pc-windows-msvc" in str(a.get("name", "")).lower()
         and str(a.get("name", "")).lower().endswith(".zip")
         and "winget" not in str(a.get("name", "")).lower()),
        None,
    )
    if asset is None:
        raise InstallError("maa-cli Release 中没有找到 x86_64 Windows 压缩包")
    temp = work / "maa-cli"
    temp.mkdir(parents=True, exist_ok=True)
    archive = temp / str(asset["name"])
    download_asset(asset, archive, token, None, log)
    extract_archive(archive, temp / "extract", log)
    exe = next((p for p in (temp / "extract").rglob("maa.exe") if p.is_file()), None)
    if exe is None:
        raise InstallError("maa-cli 压缩包中没有 maa.exe")
    shutil.copy2(exe, target / "maa-cli.exe")
    if log:
        log(f"maa-cli 已安装：{target / 'maa-cli.exe'}")


def install_project(project: Project, root: Path, force: bool = False, token: str = "",
                    progress: Progress | None = None, log: Log | None = None) -> InstallResult:
    root = Path(root).expanduser().resolve()
    if not project.repo:
        raise InstallError(f"{project.name} 未配置 GitHub 仓库")
    name = install_name(project)
    if not re.match(r"^[A-Za-z0-9._-]+$", name):
        raise InstallError("安装目录名不合法：" + name)
    target = root / name
    root.mkdir(parents=True, exist_ok=True)

    def report(percent: float, message: str) -> None:
        if progress:
            progress(max(0.0, min(100.0, percent)), message)

    def say(message: str) -> None:
        if log:
            log(message)

    tmp_root = root / ".eryou-install-tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    work = tmp_root / f"{name}-{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    moved = False
    try:
        report(0, "获取最新 Release…")
        release = latest_release(project.repo, token)
        tag = str(release.get("tag_name", ""))
        asset = choose_asset(project, release)
        size = int(asset.get("size") or 0)
        say(f"最新版本 {tag} · {asset['name']}")
        report(1, f"准备安装 {project.game} / {project.name}")
        if size:
            free = shutil.disk_usage(root).free
            if free < size * 2.5:
                raise InstallError(f"磁盘空间不足：需要约 {_fmt_bytes(size * 2.5)}，剩余 {_fmt_bytes(free)}")
        archive = work / str(asset["name"])
        download_asset(asset, archive, token, progress, log)
        verify_checksum(release, asset, archive, token, log)
        report(86, "解压中…")
        extract_root = work / "extract"
        extract_archive(archive, extract_root, log)
        report(94, "定位主程序…")
        app_root = find_app_root(extract_root, project)
        say(f"主程序目录：{app_root.relative_to(extract_root) or '.'}")
        report(96, "写入固定安装目录…")
        preserve_root = work / "preserve"
        if target.exists():
            if not force:
                raise InstallError(f"{target} 已存在。使用 --force 覆盖安装，或先在设置中改名/删除")
            report(96, "保留用户配置…")
            _copy_preserved(target, preserve_root, _preserve_paths(project), log)
            backup = tmp_root / f"{name}.backup-{int(time.time())}"
            shutil.move(str(target), str(backup))
            say("已备份旧版本，准备覆盖")
        try:
            if app_root.resolve() == extract_root.resolve():
                target.mkdir(parents=True, exist_ok=True)
                for child in app_root.iterdir():
                    shutil.move(str(child), str(target / child.name))
            else:
                shutil.move(str(app_root), str(target))
            moved = True
        except Exception:
            if target.exists() and moved:
                shutil.rmtree(target, ignore_errors=True)
            if backup is not None and backup.exists() and not target.exists():
                shutil.move(str(backup), str(target))
            raise
        _restore_preserved(preserve_root, target, log)
        _write_marker(target, project, release, asset, app_root)
        if project.id == "maa":
            try:
                runtime = projects_dir() / "maa-runtime"
                runtime.mkdir(parents=True, exist_ok=True)
                install_maa_cli(runtime, work, token, log)
            except Exception as exc:
                say(f"maa-cli 安装失败（可稍后重试）：{exc}")
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        report(100, "安装完成")
        say(f"已安装到 {target}")
        return InstallResult(
            project_id=project.id, project_name=project.name, path=str(target),
            tag=tag, asset=str(asset["name"]), size=size,
            message=f"{tag} · {_fmt_bytes(size)}",
        )
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(f"安装失败：{type(exc).__name__}: {exc}") from exc
    finally:
        # 成功时保留目标，清理临时与失败备份；失败时已尝试回滚
        if backup is not None and backup.exists() and target.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        try:
            if tmp_root.exists() and not any(tmp_root.iterdir()):
                tmp_root.rmdir()
        except OSError:
            pass


def cli_install(config: Config, target: str, root_override: str = "", force: bool = False) -> int:
    projects = list(config.projects)
    if target.lower() != "all":
        wanted = target.lower()
        projects = [p for p in projects if p.id.lower() == wanted or p.name.lower() == wanted
                    or install_name(p).lower() == wanted]
        if not projects:
            print("未知项目：" + target)
            return 2

    root = Path(root_override).expanduser().resolve() if root_override else config.install_root_path()
    root.mkdir(parents=True, exist_ok=True)
    if root_override:
        config.install_root = str(root)
        config.save()
    print(f"安装根目录：{root}")
    failures = 0
    for project in projects:
        if project.directory and Path(project.directory).is_dir() and not force:
            print(f"[跳过] {project.game} / {project.name} 已安装：{project.directory}")
            continue
        print(f"\n=== {project.game} / {project.name} ===")
        last = {"pct": -10.0}

        def progress(pct: float, message: str) -> None:
            if pct - last["pct"] >= 2 or pct >= 100:
                last["pct"] = pct
                print(f"[{pct:5.1f}%] {message}", flush=True)

        try:
            result = install_project(project, root, force=force, token=config.github_token,
                                     progress=progress, log=lambda msg: print("  " + msg, flush=True))
        except InstallError as exc:
            failures += 1
            print(f"[失败] {exc}")
            continue
        project.directory = result.path
        info = probe(project, result.path)
        project.kind = info.get("kind", project.kind)
        project.exe = info.get("exe", "")
        project.runner = info.get("runner", "exe")
        config.save()
        print(f"[完成] {result.tag} → {result.path}")
    print("\n全部完成。" if failures == 0 else f"\n完成，{failures} 个项目失败。")
    return 0 if failures == 0 else 1
