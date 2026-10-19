"""当前系统标识的检测。

devkit 用 ``<os>_<arch>`` 形式的字符串来描述系统，例如本机是 ``win_amd64``。
配方里的 ``system`` 字段、``download`` 里的系统过滤都使用这个标识。
"""

from __future__ import annotations

import platform
import sys

_OS_NAMES = {
    "win32": "win",
    "cygwin": "win",
    "msys": "win",
    "darwin": "macos",
}

_ARCH_NAMES = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "x64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "x86": "x86",
    "i386": "x86",
    "i686": "x86",
}


def _os_name() -> str:
    plat = sys.platform
    if plat in _OS_NAMES:
        return _OS_NAMES[plat]
    if plat.startswith("linux"):
        return "linux"
    return plat


def _arch_name() -> str:
    machine = platform.machine().lower()
    return _ARCH_NAMES.get(machine, machine or "unknown")


def detect_system() -> str:
    """返回形如 ``win_amd64`` 的系统标识。"""
    return f"{_os_name()}_{_arch_name()}"
