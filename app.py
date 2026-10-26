"""二游面板入口。

用法：
    EryouPanel.exe                  # 启动悬浮窗
    EryouPanel.exe --tui            # 启动终端界面（可选）
    EryouPanel.exe --config D:\\path\\panel.json
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eryou-panel", description="二游面板：异环 / 鸣潮 / 明日方舟 自动化项目悬浮监控面板")
    parser.add_argument("--config", default="", help="指定 panel.json 路径")
    parser.add_argument("--no-scan", action="store_true", help="启动时不自动扫描安装目录")
    parser.add_argument("--tui", action="store_true", help="启动终端界面（默认是悬浮窗 GUI）")
    parser.add_argument("--scan", action="store_true", help="只扫描安装目录并写入 panel.json")
    parser.add_argument("--install", choices=["all", "ok-nte", "ok-ww-enhanced", "maa", "MAA"],
                        default="", help="命令行安装项目（all=全部），安装到面板目录")
    parser.add_argument("--root", default="", help="覆盖安装根目录（默认面板 EXE 所在目录）")
    parser.add_argument("--force", action="store_true", help="覆盖安装已存在的目录")
    parser.add_argument("--selftest", action="store_true", help="运行打包自检后退出")
    parser.add_argument("--version", action="store_true", help="显示版本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from eryou_panel.constants import APP_VERSION

    if args.version:
        print("二游面板 " + APP_VERSION)
        return 0

    if args.selftest:
        from eryou_panel.gui.selftest import run_selftest

        return run_selftest()

    from eryou_panel.config import Config

    config = Config(Path(args.config) if args.config else None)
    if config.first_run:
        # 首次运行落盘一份可编辑的默认配置
        config.save()

    if args.install:
        from eryou_panel.console import prepare_cli
        from eryou_panel.installer import cli_install

        prepare_cli()
        return cli_install(config, args.install, args.root, args.force)

    if args.scan:
        from eryou_panel.console import prepare_cli
        from eryou_panel.tools import scan_cli

        prepare_cli()
        return scan_cli(config)

    if args.tui:
        if getattr(sys, "frozen", False):
            print("此版本是 GUI 构建，不包含 --tui；请在源码环境运行 python app.py --tui")
            return 2
        from eryou_panel.console import prepare_console

        prepare_console()
        module = importlib.import_module("eryou_panel.ui.app")
        app = module.EryouPanelApp(config, auto_scan=not args.no_scan)
        app.run()
        return 0

    from eryou_panel.gui.app import run_gui

    return run_gui(config)


if __name__ == "__main__":
    sys.exit(main())
