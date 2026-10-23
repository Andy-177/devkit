"""devkit 命令行入口。

用法::

    devkit build recipe.json               # 执行配方
    devkit run recipe.json                 # build 的别名，等价
    devkit build bon/recipe.json -w build  # 指定工作目录
    devkit build recipe.json -j build-windows --dry-run
    devkit system                        # 打印当前系统标识
"""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path
from typing import Sequence

# 直接运行本文件时，把项目根加入 sys.path，使 `python devkit/cli.py` 可用
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from loguru import logger
except Exception:  # 无第三方依赖降级
    import logging as _logging

    class _Shim:
        def remove(self, *a, **k): _logging.getLogger("devkit").handlers.clear()
        def add(self, sink=None, level="INFO", format="", colorize=True, backtrace=False, diagnose=False, **k):
            lg = _logging.getLogger("devkit")
            if not lg.handlers:
                h = _logging.StreamHandler(sys.stderr)
                h.setFormatter(_logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
                lg.addHandler(h)
            lg.setLevel(getattr(_logging, str(level).upper(), _logging.INFO)); return 0
        def opt(self, *a, **k): return self
        def bind(self, *a, **k): return self
        def _fmt(self, msg, args, kwargs):
            try: return msg.format(*args, **kwargs) if (args or kwargs) else str(msg)
            except Exception: return str(msg)
        def __getattr__(self, name):
            if name.upper() in ("TRACE","DEBUG","INFO","SUCCESS","WARNING","ERROR","CRITICAL"):
                lvl = name.upper() if name.upper() in ("INFO","WARNING","ERROR","CRITICAL") else ("DEBUG" if name.upper()=="DEBUG" else ("ERROR" if name.upper() in ("ERROR","CRITICAL") else "INFO"))
                def _w(msg, *a, **k): _logging.getLogger("devkit").log(getattr(_logging, lvl, 20), self._fmt(msg, a, k))
                return _w
            raise AttributeError(name)

    _m = types.ModuleType("loguru"); _m.logger = _Shim(); sys.modules.setdefault("loguru", _m)
    logger = _m.logger

from devkit._version import __version__
from devkit.errors import DevkitError, StepError
from devkit.runner import DEFAULT_DOWNLOAD_DIR, DEFAULT_WORKDIR, RecipeRunner
from devkit.system import detect_system


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devkit",
        description="配方(recipe.json)驱动的构建工作流工具",
    )
    parser.add_argument("--version", action="version", version=f"devkit {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser("build", aliases=["run"], help="执行一个配方（run 是别名）")
    _add_run_arguments(build)

    system = subparsers.add_parser("system", help="打印当前检测到的系统标识")
    system.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")

    console = subparsers.add_parser("console", help="启动 devkit 表达式 REPL")
    console.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")

    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("recipe", help="配方文件或目录路径，如 recipe.json")
    parser.add_argument(
        "-w",
        "--workdir",
        default=str(DEFAULT_WORKDIR),
        help=f"工作区目录，build 指令在这里执行，克隆落在其 workspace 子目录 (默认: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "-d",
        "--download-dir",
        default=str(DEFAULT_DOWNLOAD_DIR),
        help=f"下载目录，所有下载的文件都放这里 (默认: {DEFAULT_DOWNLOAD_DIR})",
    )
    parser.add_argument("-s", "--system", default=None, help="覆盖自动检测的系统标识")
    parser.add_argument(
        "-j", "--job", action="append", default=None, help="只执行指定名称的工作流，可重复"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印要做的事，不真正执行")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
        colorize=True,
        backtrace=False,
        diagnose=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 允许省略 build 子命令: devkit recipe.json
    known = {"build", "run", "system", "-h", "--help", "--version", "console"}
    if argv and argv[0] not in known and not argv[0].startswith("-"):
        argv.insert(0, "build")

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    _setup_logging(getattr(args, "verbose", False))

    if args.command == "system":
        logger.info("当前系统: {}", detect_system())
        print(detect_system())
        return 0
    if args.command == "console":
        import code
        import importlib

        from devkit.expr import get_builtin_namespace

        ns = get_builtin_namespace(None, detect_system())
        for module_name in (
            "cli",
            "recipe",
            "runner",
            "expr",
            "system",
            "git",
            "dl",
            "unzip",
            "unpack",
            "errors",
            "guard",
        ):
            try:
                ns[module_name] = importlib.import_module(f"devkit.{module_name}")
            except ImportError:
                pass
        import devkit

        ns["devkit"] = devkit
        banner = "devkit console (type help() for help, exit() or Ctrl-D to exit)"
        code.interact(banner=banner, local=ns)
        return 0

    try:
        runner = RecipeRunner(
            workdir=Path(args.workdir),
            system=args.system,
            download_dir=Path(args.download_dir),
            dry_run=args.dry_run,
            jobs=args.job,
        )
        logger.info(
            "devkit {} | 系统: {} | 工作区: {} | 下载目录: {}",
            __version__,
            runner.system,
            runner.workdir,
            runner.download_dir,
        )
        if args.dry_run:
            logger.info("dry-run 模式: 不会执行克隆/下载/指令")
        runner.run(args.recipe)
    except StepError as exc:
        logger.error("执行失败: {}", exc)
        return exc.returncode or 1
    except DevkitError as exc:
        logger.error("{}", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("已中断")
        return 130

    if runner.failed:
        logger.error("构建结束，但有步骤/作业失败（已按 errskip 策略继续或跳过）")
        return 1
    logger.info("全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
