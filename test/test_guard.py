"""devkit.guard 并发 stdin 守卫测试。"""

from __future__ import annotations

import _common
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest

from devkit import guard
from devkit.guard import StdinGuard

#: POSIX 下必须让子进程自成进程组，否则 _kill_tree 的 killpg 会连测试本身一起杀掉
_POPEN_KW = {} if sys.platform == "win32" else {"start_new_session": True}


def spawn(code: str, stdin) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], stdin=stdin, **_POPEN_KW)


class TestGuarded(unittest.TestCase):
    def test_current_is_none_outside(self):
        self.assertIsNone(guard.current())

    def test_nesting_shares_one_guard(self):
        with guard.guarded() as outer:
            self.assertIs(guard.current(), outer)
            with guard.guarded() as inner:
                self.assertIs(inner, outer)
                self.assertIs(guard.current(), outer)
            self.assertIs(guard.current(), outer)
        self.assertIsNone(guard.current())

    def test_cleanup_after_leave(self):
        with guard.guarded() as g:
            tmp_dir = g._tmp
            self.assertTrue(tmp_dir.exists())
        self.assertFalse(tmp_dir.exists())

    def test_reentrant_after_exit(self):
        with guard.guarded():
            pass
        with guard.guarded() as g2:
            self.assertIsNotNone(g2)
        self.assertIsNone(guard.current())


class TestSentinelStdin(unittest.TestCase):
    def test_new_stdin_is_newlines(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            try:
                self.assertEqual(stream.read(), b"\n" * 64)
            finally:
                stream.close()

    def test_new_stdin_starts_at_offset_zero(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            try:
                self.assertEqual(os.lseek(stream.fileno(), 0, os.SEEK_CUR), 0)
            finally:
                stream.close()


class TestReadStdin(unittest.TestCase):
    def test_offset_advances_after_read(self):
        fd, name = tempfile.mkstemp(prefix="devkit-test-")
        try:
            os.write(fd, b"\n" * 8)
            os.lseek(fd, 0, os.SEEK_SET)
            stream = os.fdopen(fd, "rb", closefd=True)
            self.assertFalse(StdinGuard._read_stdin(stream))
            stream.readline()
            self.assertTrue(StdinGuard._read_stdin(stream))
        finally:
            stream.close()
            os.unlink(name)

    def test_bad_stream_returns_false(self):
        self.assertFalse(StdinGuard._read_stdin(io.BytesIO()))  # 无 fileno -> 异常 -> False


class TestWatchdog(unittest.TestCase):
    def test_non_reader_not_flagged(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            proc = spawn("import time;time.sleep(0.4)", stream)
            g.track(proc, stream, "sleeper")
            proc.wait(timeout=15)
            time.sleep(0.15)  # 给监视线程几个轮询窗口
            self.assertIsNone(g.hit)

    def test_reader_flagged_and_killed(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            proc = spawn(
                "import sys,time;sys.stdin.readline();time.sleep(30)", stream)
            g.track(proc, stream, "reader-cmd")
            deadline = time.monotonic() + 10
            while g.hit is None and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(g.hit, "reader-cmd")
            deadline = time.monotonic() + 10
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(proc.poll(), "读 stdin 的子进程应被杀掉")

    def test_kill_all_terminates_workers(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            proc = spawn("import time;time.sleep(60)", stream)
            g.track(proc, stream, "long-runner")
            g.kill_all()
            proc.wait(timeout=15)
            self.assertIsNotNone(proc.poll())
            self.assertIsNone(g.hit)  # kill_all 后不再按交互式报错

    def test_hit_kept_after_context_for_inspection(self):
        with guard.guarded() as g:
            stream = g.new_stdin()
            proc = spawn("import sys;sys.stdin.readline()", stream)
            g.track(proc, stream, "fast-reader")
            proc.wait(timeout=15)
            deadline = time.monotonic() + 5
            while g.hit is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(g.hit, "fast-reader")


if __name__ == "__main__":
    unittest.main()
