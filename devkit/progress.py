"""下载/解压进度显示（纯标准库，不依赖第三方库）。

同一时刻只占一行：只有一个任务时显示它自己的进度条，多个任务并发时合并成
一行总进度，避免多行互相覆盖。非 tty（例如重定向到文件）时自动关闭；可以用
环境变量 ``DEVKIT_PROGRESS=1`` 强制打开、``DEVKIT_PROGRESS=0`` 强制关闭。
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from typing import IO, Any

_BAR_WIDTH = 22
_MIN_INTERVAL = 0.1
_OFF_VALUES = ("", "0", "false", "off", "no")


def _is_enabled(stream: IO[str]) -> bool:
    override = os.environ.get("DEVKIT_PROGRESS")
    if override is not None:
        return override.strip().lower() not in _OFF_VALUES
    return bool(getattr(stream, "isatty", lambda: False)())


def format_size(size: float) -> str:
    """把字节数格式化成人类可读的形式，例如 ``1.5 MB``。"""
    value = float(max(size, 0))
    if value < 1024:
        return f"{value:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def format_speed(speed: float) -> str:
    """把字节/秒格式化成人类可读的形式，例如 ``1.5 MB/s``。"""
    return f"{format_size(speed)}/s"


def format_eta(eta: float | None) -> str:
    """把剩余秒数格式化成 ``MM:SS`` / ``H:MM:SS``；未知时是 ``--:--``。"""
    if eta is None or eta < 0 or eta != eta:
        return "--:--"
    seconds = int(eta)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _bar(ratio: float) -> str:
    filled = int(round(min(max(ratio, 0.0), 1.0) * _BAR_WIDTH))
    return "#" * filled + "-" * (_BAR_WIDTH - filled)


def render_line(
    label: str,
    downloaded: float,
    total: float,
    speed: float,
    eta: float | None,
    *,
    unknown: bool = False,
) -> str:
    """渲染一行进度: 标签 [进度条] 百分比 已处理/总大小 速度 剩余时间。"""
    if unknown or total <= 0:
        return f"{label} [{_bar(0.0)}] {format_size(downloaded)} {format_speed(speed)}"
    ratio = min(downloaded / total, 1.0)
    return (
        f"{label} [{_bar(ratio)}] {ratio * 100:5.1f}% "
        f"{format_size(downloaded)}/{format_size(total)} "
        f"{format_speed(speed)} ETA {format_eta(eta)}"
    )


def estimate(downloaded: float, total: float, elapsed: float) -> tuple[float, float]:
    """按平均速度估算 ``(速度, 剩余秒数)``；算不出来时 ETA 为 -1。"""
    if elapsed <= 0 or downloaded <= 0:
        return 0.0, -1.0
    speed = downloaded / elapsed
    if total <= 0:
        return speed, -1.0
    return speed, max(total - downloaded, 0) / speed


class _Display:
    """全局单行进度显示。"""

    def __init__(self, stream: IO[str] | None = None) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = _is_enabled(self.stream)
        self._lock = threading.Lock()
        self._bars: dict[int, ProgressBar] = {}
        self._seq = 0
        self._last = 0.0
        self._painted = False

    # ---------------------------------------------------------------- 注册

    def register(self, bar: "ProgressBar") -> int:
        with self._lock:
            self._seq += 1
            self._bars[self._seq] = bar
            return self._seq

    def unregister(self, bar: "ProgressBar") -> None:
        with self._lock:
            self._bars.pop(bar.id, None)
            if self._bars:
                self._paint(force=True)
            else:
                self._erase()

    def update(self, bar: "ProgressBar", *, force: bool = False) -> None:
        if not self.enabled:
            return
        with self._lock:
            if bar.id in self._bars:
                self._paint(force=force)

    # ---------------------------------------------------------------- 绘制

    def _line(self) -> str:
        bars = list(self._bars.values())
        if len(bars) == 1:
            return bars[0].text()
        downloaded = sum(b.downloaded for b in bars)
        total = sum(b.total for b in bars)
        speed = sum(b.speed for b in bars)
        eta = (total - downloaded) / speed if speed > 0 else -1.0
        return render_line(f"{len(bars)} 个任务", downloaded, total, speed, eta)

    def _width(self) -> int:
        return max(shutil.get_terminal_size((80, 24)).columns - 1, 20)

    def _paint(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last < _MIN_INTERVAL:
            return
        self._last = now
        width = self._width()
        line = self._line()[:width]
        try:
            self.stream.write("\r" + line + " " * max(width - len(line), 0))
            self.stream.flush()
        except (OSError, ValueError):
            self.enabled = False
            return
        self._painted = True

    def _erase(self) -> None:
        if not self._painted:
            return
        try:
            self.stream.write("\r" + " " * self._width() + "\r")
            self.stream.flush()
        except (OSError, ValueError):
            self.enabled = False
        self._painted = False


_DISPLAY = _Display()


class ProgressBar:
    """一次下载/解压的进度条；实例本身可以直接当 littledl 的 ``progress_callback``。

    ``update`` 不传 ``speed``/``eta`` 时（例如解压）按平均速度自动估算。

    用法::

        with ProgressBar(label) as bar:
            ...  # 不断 bar.update(done, total)
    """

    def __init__(self, label: str, *, display: _Display | None = None) -> None:
        self.label = label
        self.downloaded = 0.0
        self.total = 0.0
        self.speed = 0.0
        self.eta: float | None = None
        self.unknown = False
        self._display = display if display is not None else _DISPLAY
        self._start = time.monotonic()
        self.id = self._display.register(self)

    def __call__(self, event: Any) -> None:
        self.update(
            event.downloaded,
            event.total,
            speed=event.speed,
            eta=event.eta,
            unknown=getattr(event, "unknown_size", False),
        )

    def update(
        self,
        downloaded: float,
        total: float,
        *,
        speed: float | None = None,
        eta: float | None = None,
        unknown: bool = False,
    ) -> None:
        self.downloaded = downloaded
        self.total = total
        if speed is None:
            speed, eta = estimate(downloaded, total, time.monotonic() - self._start)
        self.speed = speed
        self.eta = eta
        self.unknown = unknown
        self._display.update(self, force=total > 0 and downloaded >= total)

    def close(self) -> None:
        self._display.unregister(self)

    def text(self) -> str:
        return render_line(
            self.label,
            self.downloaded,
            self.total,
            self.speed,
            self.eta,
            unknown=self.unknown,
        )

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
