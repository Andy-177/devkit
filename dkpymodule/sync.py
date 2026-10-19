"""devkit 源码 -> dkpymodule/src/devkit 同步器（壳工程的唯一胶水）。

源码唯一真相在仓库根的 ``devkit/`` 目录（它永远不依赖本工程）；本脚本做两件事：

1. 把 ``../devkit/*.py`` 复制进 ``src/devkit/``（先清旧 .py，防改名残留）；
2. 生成壳专属文件：``__init__.py``（loguru 降级垫片 + 版本导出）与
   ``__main__.py``（``python -m devkit`` 入口）。

构建（pip install / python -m build）时由 setup.py 自动调用；源码目录不存在但
src/devkit 已有内容时跳过复制（sdist 二次构建场景），壳文件照常重写。
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 源码目录（独立的 devkit 本体）
SOURCE = HERE.parent / "devkit"
#: 同步目标（src 布局）
TARGET = HERE / "src" / "devkit"

_INIT_TEMPLATE = '''"""devkit Python 模块版包入口。

本文件由 dkpymodule/sync.py 生成，不是 devkit 本体的一部分。
devkit 本体（仓库根 devkit/）不依赖本文件。
"""

from __future__ import annotations

import sys
import types

# 没装 loguru 时降级为零依赖 logger（与 devkit/cli.py 的垫片一致）
try:  # pragma: no cover
    from loguru import logger as _logger  # noqa: F401
except Exception:
    import logging as _logging

    class _ShimLogger:
        _LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")

        def __init__(self) -> None:
            self._log = _logging.getLogger("devkit")
            if not self._log.handlers:
                _h = _logging.StreamHandler(sys.stderr)
                _h.setFormatter(_logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
                self._log.addHandler(_h)
                self._log.setLevel(_logging.INFO)

        def remove(self, *a, **k) -> None:
            self._log.handlers.clear()

        def add(self, sink=None, level: str = "INFO", format: str = "", colorize: bool = True,
                backtrace: bool = False, diagnose: bool = False, **k) -> int:
            self._log.setLevel(getattr(_logging, str(level).upper(), _logging.INFO))
            return 0

        def opt(self, *a, **k):
            return self

        def bind(self, *a, **k):
            return self

        def _emit(self, level: str, message: str, *args, **kwargs) -> None:
            msg = str(message)
            if kwargs or args:
                try:
                    msg = msg.format(*args, **kwargs)
                except Exception:
                    pass
            self._log.log(getattr(_logging, level, _logging.INFO), msg)

        def __getattr__(self, name: str):
            if name.upper() in self._LEVELS:
                lvl = name.upper()

                def _w(message, *a, **k) -> None:
                    self._emit(lvl, message, *a, **k)

                return _w
            raise AttributeError(name)

    _mod = types.ModuleType("loguru")
    _mod.logger = _ShimLogger()
    sys.modules.setdefault("loguru", _mod)

from ._version import __version__

__all__ = ["__version__"]
'''

_MAIN_TEMPLATE = '''"""``python -m devkit`` 入口（由 dkpymodule 生成）。"""

from __future__ import annotations

import sys

from devkit.cli import main

if __name__ == "__main__":
    sys.exit(main())
'''


def sync() -> Path:
    """执行同步，返回 src/devkit 路径。"""
    if SOURCE.is_dir():
        TARGET.mkdir(parents=True, exist_ok=True)
        for old in TARGET.glob("*.py"):
            old.unlink()
        for src_file in sorted(SOURCE.glob("*.py")):
            shutil.copy2(src_file, TARGET / src_file.name)
    elif not any(TARGET.glob("*.py")):
        raise SystemExit(f"devkit 源码目录不存在且无现成副本: {SOURCE}")

    (TARGET / "__init__.py").write_text(_INIT_TEMPLATE, encoding="utf-8")
    (TARGET / "__main__.py").write_text(_MAIN_TEMPLATE, encoding="utf-8")
    return TARGET


if __name__ == "__main__":
    target = sync()
    print(f"已同步 -> {target}")
