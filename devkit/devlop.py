"""Compatibility shim: ``devlop`` is a legacy misspelling of ``develop``.

Prefer :mod:`devkit.develop`.
"""

from __future__ import annotations

from devkit.develop import *  # noqa: F401,F403
from devkit.develop import (
    CONTINUATION,
    DEFAULT_COLORS,
    DevelopShell,
    DevlopShell,
    continuation_fragments,
    join_input_lines,
    load_shell_colors,
    normalize_shell_colors,
    prompt_fragments,
    prompt_text,
    run_develop,
    run_devlop,
)

__all__ = [
    "CONTINUATION",
    "DEFAULT_COLORS",
    "DevelopShell",
    "DevlopShell",
    "continuation_fragments",
    "join_input_lines",
    "load_shell_colors",
    "normalize_shell_colors",
    "prompt_fragments",
    "prompt_text",
    "run_develop",
    "run_devlop",
]
