from __future__ import annotations

from eryou_panel.config import Config
from eryou_panel.installer import choose_asset


def _asset(name: str) -> dict:
    return {"name": name, "size": 1, "browser_download_url": "https://example.invalid/" + name}


def test_representative_projects_are_in_default_catalog(tmp_path) -> None:
    cfg = Config(tmp_path / "panel.json")
    ids = {p.id for p in cfg.projects}
    assert {"better-genshin-impact", "march7th-assistant", "azur-lane-auto-script",
            "blue-archive-auto-script", "onmyoji-auto-script", "honkai-helper",
            "ok-gf2", "zenless-zone-zero-one-dragon",
            "fgo-fga", "uma-android-automation", "nikke-auto-script"} <= ids


def test_project_specific_release_asset_selection(tmp_path) -> None:
    cfg = Config(tmp_path / "panel.json")
    bgi = cfg.get("better-genshin-impact")
    assert bgi is not None
    release = {"assets": [
        _asset("BetterGI.Install.0.66.0.exe"),
        _asset("BetterGI_v0.66.0.7z"),
        _asset("source.zip"),
    ]}
    assert choose_asset(bgi, release)["name"] == "BetterGI_v0.66.0.7z"

    m7a = cfg.get("march7th-assistant")
    assert m7a is not None
    release = {"assets": [
        _asset("update.7z"),
        _asset("March7thAssistant_full.zip"),
        _asset("March7thAssistant_full.7z"),
    ]}
    assert choose_asset(m7a, release)["name"] == "March7thAssistant_full.7z"

    alas = cfg.get("azur-lane-auto-script")
    assert alas is not None
    release = {"assets": [
        _asset("AlasApp_0.4.13_fullcn.7z"),
        _asset("AlasApp_0.4.13_full.7z"),
    ]}
    assert choose_asset(alas, release)["name"] == "AlasApp_0.4.13_full.7z"

    baas = cfg.get("blue-archive-auto-script")
    assert baas is not None
    release = {"assets": [
        _asset("BlueArchiveAutoScript_v1.4.3_win_x86_64.exe"),
        _asset("BlueArchiveAutoScript_v1.4.3_win_x86_64_full_env.7z"),
    ]}
    assert choose_asset(baas, release)["name"].endswith("full_env.7z")

    oas = cfg.get("onmyoji-auto-script")
    assert oas is not None
    release = {"assets": [_asset("OnmyojiAutoScript-easy-install.rar")]}
    assert choose_asset(oas, release)["name"].endswith(".rar")

    honkai = cfg.get("honkai-helper")
    assert honkai is not None
    release = {"assets": [_asset("DaCapo.zip")]}
    assert choose_asset(honkai, release)["name"] == "DaCapo.zip"

    gf2 = cfg.get("ok-gf2")
    assert gf2 is not None
    release = {"assets": [
        _asset("ok-gf2-win32-online-setup-v1.2.91.exe"),
        _asset("ok-gf2-win32-v1.2.91.zip"),
    ]}
    assert choose_asset(gf2, release)["name"] == "ok-gf2-win32-v1.2.91.zip"

    zzz = cfg.get("zenless-zone-zero-one-dragon")
    assert zzz is not None
    release = {"assets": [
        _asset("ZenlessZoneZero-OneDragon-v2.5.6-WithRuntime-Full.zip"),
        _asset("ZenlessZoneZero-OneDragon-v2.5.6-Full.zip"),
    ]}
    assert choose_asset(zzz, release)["name"] == "ZenlessZoneZero-OneDragon-v2.5.6-Full.zip"
