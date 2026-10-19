"""壳工程的构建入口：同步源码后交给 setuptools。

PEP 517 后端（setuptools.build_meta）执行本文件，因此 pip/build 都会先同步；
从 sdist 构建且源码目录不存在时，sync() 会复用已打进 sdist 的 src/devkit。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync import sync  # noqa: E402

sync()

from setuptools import setup  # noqa: E402

setup()
