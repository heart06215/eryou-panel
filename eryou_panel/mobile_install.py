"""Android 手机端项目：下载 APK 并安装到 MuMu。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import maa_support
from .installer import download_asset, extract_archive, latest_release
from .models import Project

Log = object


def _suffix_ok(name: str) -> bool:
    low = name.lower()
    return low.endswith((".apk", ".zip", ".7z", ".rar"))


def select_mobile_asset(project: Project, release: dict) -> dict:
    assets = [a for a in release.get("assets", []) if a.get("name") and a.get("browser_download_url")]
    patterns = [str(x).lower() for x in project.extra.get("asset_patterns", [])]
    candidates = []
    for asset in assets:
        name = str(asset["name"])
        low = name.lower()
        if not _suffix_ok(low):
            continue
        if any(x in low for x in ("debug", "symbol", "source", "universal")):
            continue
        if patterns and not all(x in low for x in patterns):
            continue
        candidates.append(asset)
    if not candidates:
        candidates = [a for a in assets if _suffix_ok(str(a.get("name", "")))]
    if not candidates:
        raise RuntimeError("该 Release 没有 APK/压缩包")
    # 模拟器优先 x86_64；否则大包优先。
    candidates.sort(key=lambda a: (0 if "x86_64" in str(a["name"]).lower() else 1,
                                   -int(a.get("size") or 0)))
    return candidates[0]


def prepare_apk(project: Project, token: str = "", log=None):
    release = latest_release(project.repo, token)
    asset = select_mobile_asset(project, release)
    temp = Path(tempfile.mkdtemp(prefix="eryou-mobile-"))
    archive = temp / str(asset["name"])
    if log:
        log(f"下载手机端 APK：{asset['name']}")
    download_asset(asset, archive, token, None, log)
    suffix = archive.suffix.lower()
    if suffix == ".apk":
        return archive, release.get("tag_name", ""), temp
    extract = temp / "extract"
    extract_archive(archive, extract, log)
    apks = sorted(extract.rglob("*.apk"))
    if not apks:
        shutil.rmtree(temp, ignore_errors=True)
        raise RuntimeError("安装包内没有找到 APK")
    apks.sort(key=lambda p: (0 if "x86" in p.name.lower() or "x86_64" in p.name.lower() else 1))
    return apks[0], release.get("tag_name", ""), temp


def ensure_mumu(project: Project, log=None):
    adb = maa_support.find_adb(project)
    address = maa_support.mumu_address(project)
    ok = maa_support.start_mumu(project, address, timeout=120, log=log)
    if not ok:
        raise RuntimeError("MuMu 启动失败")
    ready = maa_support.first_ready_device(adb, address)
    if not ready:
        raise RuntimeError("MuMu ADB 未就绪")
    return adb, ready


def install_apk(project: Project, apk: Path, log=None) -> str:
    adb, address = ensure_mumu(project, log)
    if log:
        log(f"安装 {apk.name} 到 MuMu {address}")
    result = subprocess.run(
        [adb, "-s", address, "install", "-r", "-d", str(apk)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, creationflags=0x08000000,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Success" not in output:
        raise RuntimeError(output.strip()[-500:] or f"adb install 退出码 {result.returncode}")
    return output.strip().splitlines()[-1] if output.strip() else "Success"
