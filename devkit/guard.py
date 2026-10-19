"""并发运行时的 stdin 守卫。

并发运行（步骤的 ``linkup`` 联动）时不能把终端交给子进程（会互相抢输入、把整个
构建卡死），所以守卫给每个并发子进程的 stdin 是一个装着空行的临时文件（读到的是
空行，读完了是 EOF，不会卡住），并盯着这个文件的读取位置：只要哪个子进程读了
stdin，就说明它是交互式程序——立刻把这批并发子进程全部杀掉并报错，逼开发者把
交互式程序单独串行执行；不并发（没有 linkup）时子进程照旧继承真正的终端，交互正常。

用法::

    with guarded():
        ...   # 期间启动的子进程都要用 StdinGuard 的 stdin（见 RecipeRunner._spawn）
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

#: 轮询间隔（秒），越小杀得越快
_POLL = 0.05
#: 哨兵内容: 全是空行，交互式程序读到空行/EOF 不会卡死
_SENTINEL = b"\n" * 64


def _kill_tree(proc: subprocess.Popen) -> None:
    """连子孙进程一起杀掉（bat 会再开 cmd，不杀树会留孤儿）。"""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


class StdinGuard:
    """一批并发子进程共用的 stdin 守卫。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tmp = Path(tempfile.mkdtemp(prefix="devkit-stdin-"))
        self._workers: list[tuple[subprocess.Popen, IO[bytes], str]] = []
        self._hit: str | None = None
        self._aborted = False
        self._depth = 0
        self._stop = threading.Event()
        self._monitor = threading.Thread(
            target=self._watch, name="devkit-stdin-guard", daemon=True
        )

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self._stop.clear()
        self._monitor.start()

    def enter(self) -> None:
        self._depth += 1

    def leave(self) -> None:
        self._depth -= 1
        if self._depth > 0:
            return
        self._stop.set()
        self._monitor.join(timeout=1.0)
        with self._lock:
            for _, stream, _ in self._workers:
                stream.close()
            self._workers.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------ 子进程侧

    def new_stdin(self) -> IO[bytes]:
        """给即将启动的子进程准备一个哨兵 stdin。"""
        fd, name = tempfile.mkstemp(prefix="stdin-", dir=self._tmp)
        os.write(fd, _SENTINEL)
        os.lseek(fd, 0, os.SEEK_SET)
        return os.fdopen(fd, "rb", closefd=True)

    def track(self, proc: subprocess.Popen, stream: IO[bytes], command: str) -> None:
        """登记子进程（stream 交给守卫保管，守卫结束再关）。"""
        with self._lock:
            self._workers.append((proc, stream, command))

    @property
    def hit(self) -> str | None:
        """读了 stdin 的指令（没读到就是 None）。"""
        with self._lock:
            return self._hit

    def kill_all(self) -> None:
        """杀掉这一批还在跑的子进程（联动组里有一个失败时用）。

        杀掉之后就不再因为 stdin 报"交互式程序"了：组已经因为失败收尾。
        """
        with self._lock:
            self._aborted = True
            workers = list(self._workers)
        for proc, _, _ in workers:
            _kill_tree(proc)

    # ---------------------------------------------------------------- 监视

    def _watch(self) -> None:
        while not self._stop.is_set():
            self._check()
            self._stop.wait(_POLL)

    def _check(self) -> None:
        if self._aborted:
            return
        with self._lock:
            workers = list(self._workers)
        for proc, stream, command in workers:
            if self._hit is not None:
                _kill_tree(proc)  # 已经在收尾了，还在跑的也一起杀掉
                continue
            if not self._read_stdin(stream):
                continue
            with self._lock:
                if self._hit is None:
                    self._hit = command
            _kill_tree(proc)

    @staticmethod
    def _read_stdin(stream: IO[bytes]) -> bool:
        """子进程是不是读了 stdin（共享的文件读取位置前进了）。"""
        try:
            return os.lseek(stream.fileno(), 0, os.SEEK_CUR) > 0
        except (OSError, ValueError):
            return False


_GUARD: StdinGuard | None = None
_GUARD_LOCK = threading.Lock()


def current() -> StdinGuard | None:
    """当前是否处于并发运行（是的话，子进程要用它的 stdin）。"""
    with _GUARD_LOCK:
        return _GUARD


@contextmanager
def guarded() -> Iterator[StdinGuard]:
    """进入并发运行: 打开 stdin 守卫（可以嵌套，最外层结束后清理）。"""
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is None:
            _GUARD = StdinGuard()
            _GUARD.start()
        guard = _GUARD
        guard.enter()
    try:
        yield guard
    finally:
        with _GUARD_LOCK:
            guard.leave()
            if guard._depth <= 0:
                _GUARD = None
