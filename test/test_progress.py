"""devkit.progress 进度显示测试（纯标准库）。"""

from __future__ import annotations

import _common  # noqa: F401
import io
import math
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from devkit.progress import (
    _Display,
    ProgressBar,
    estimate,
    format_eta,
    format_size,
    format_speed,
    render_line,
)


class TestFormatters(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(1023), "1023 B")
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(1536), "1.5 KB")
        self.assertEqual(format_size(1024 ** 2), "1.0 MB")
        self.assertEqual(format_size(5 * 1024 ** 3), "5.0 GB")
        self.assertEqual(format_size(1024 ** 4), "1.0 TB")
        self.assertEqual(format_size(-5), "0 B")

    def test_format_speed(self):
        self.assertEqual(format_speed(2048), "2.0 KB/s")
        self.assertEqual(format_speed(0), "0 B/s")

    def test_format_eta(self):
        self.assertEqual(format_eta(None), "--:--")
        self.assertEqual(format_eta(-1), "--:--")
        self.assertEqual(format_eta(float("nan")), "--:--")
        self.assertEqual(format_eta(0), "00:00")
        self.assertEqual(format_eta(65), "01:05")
        self.assertEqual(format_eta(59 * 60 + 59), "59:59")
        self.assertEqual(format_eta(3725), "1:02:05")


class TestEstimate(unittest.TestCase):
    def test_normal(self):
        speed, eta = estimate(50, 100, 2.0)
        self.assertEqual(speed, 25.0)
        self.assertEqual(eta, 2.0)

    def test_no_elapsed(self):
        self.assertEqual(estimate(50, 100, 0), (0.0, -1.0))
        self.assertEqual(estimate(50, 100, -1), (0.0, -1.0))

    def test_no_downloaded(self):
        self.assertEqual(estimate(0, 100, 1), (0.0, -1.0))

    def test_unknown_total(self):
        speed, eta = estimate(50, 0, 1)
        self.assertEqual(speed, 50.0)
        self.assertEqual(eta, -1.0)

    def test_finished(self):
        speed, eta = estimate(100, 100, 1)
        self.assertEqual(eta, 0.0)


class TestRenderLine(unittest.TestCase):
    def test_known_total(self):
        line = render_line("tool.zip", 50, 100, 25.0, 2.0)
        self.assertTrue(line.startswith("tool.zip ["))
        self.assertIn("50.0%", line)
        self.assertIn("ETA 00:02", line)
        self.assertEqual(line.count("#"), 11)  # 22 格的一半

    def test_unknown_total(self):
        line = render_line("tool.zip", 50, 0, 25.0, -1.0, unknown=True)
        self.assertIn("tool.zip", line)
        self.assertNotIn("%", line)
        self.assertNotIn("ETA", line)

    def test_bar_width(self):
        line = render_line("x", 0, 10, 1.0, 1.0)
        self.assertIn("[" + "-" * 22 + "]", line)
        line = render_line("x", 10, 10, 1.0, 0.0)
        self.assertIn("[" + "#" * 22 + "]", line)


class TestProgressBar(unittest.TestCase):
    def _disabled_display(self) -> _Display:
        return _Display(stream=io.StringIO())  # 非 tty 且无环境变量 -> 关闭

    def test_update_and_text(self):
        bar = ProgressBar("a.zip", display=self._disabled_display())
        bar.update(50, 100, speed=25.0, eta=2.0)
        text = bar.text()
        self.assertIn("a.zip", text)
        self.assertIn("50.0%", text)
        self.assertEqual(bar.downloaded, 50)
        self.assertEqual(bar.speed, 25.0)
        bar.close()
        bar.close()  # 幂等

    def test_update_estimates_speed(self):
        bar = ProgressBar("b.zip", display=self._disabled_display())
        bar.update(50, 100)
        self.assertGreater(bar.speed, 0)
        self.assertGreaterEqual(bar.eta, 0)
        bar.close()

    def test_callable_callback(self):
        bar = ProgressBar("c.zip", display=self._disabled_display())
        event = SimpleNamespace(downloaded=10, total=100, speed=1.5, eta=90.0,
                                unknown_size=False)
        bar(event)
        self.assertEqual(bar.downloaded, 10)
        self.assertEqual(bar.speed, 1.5)
        self.assertEqual(bar.eta, 90.0)
        bar.close()

    def test_unknown_flag(self):
        bar = ProgressBar("d.zip", display=self._disabled_display())
        bar.update(10, 0, unknown=True)
        self.assertTrue(bar.unknown)
        self.assertNotIn("%", bar.text())
        bar.close()

    def test_disabled_display_never_writes(self):
        sink = io.StringIO()
        bar = ProgressBar("e.zip", display=_Display(stream=sink))
        bar.update(50, 100, speed=1, eta=1)
        bar.close()
        self.assertEqual(sink.getvalue(), "")


class TestDisplay(unittest.TestCase):
    def test_enabled_by_env_even_on_non_tty(self):
        with mock.patch.dict(os.environ, {"DEVKIT_PROGRESS": "1"}):
            display = _Display(stream=io.StringIO())
        self.assertTrue(display.enabled)

    def test_disabled_by_env(self):
        class FakeTty(io.StringIO):
            def isatty(self) -> bool:
                return True

        with mock.patch.dict(os.environ, {"DEVKIT_PROGRESS": "0"}):
            display = _Display(stream=FakeTty())
        self.assertFalse(display.enabled)
        with mock.patch.dict(os.environ, {"DEVKIT_PROGRESS": ""}):
            display = _Display(stream=FakeTty())
        self.assertFalse(display.enabled)

    def test_combined_multi_task_line(self):
        with mock.patch.dict(os.environ, {"DEVKIT_PROGRESS": "1"}):
            sink = io.StringIO()
            display = _Display(stream=sink)
            one = ProgressBar("one.zip", display=display)
            two = ProgressBar("two.zip", display=display)
            one.update(50, 100, speed=10, eta=5)
            two.update(25, 100, speed=10, eta=7.5)
            self.assertEqual(len(display._bars), 2)
            line = display._line()
            self.assertTrue(line.startswith("2 个任务"))
            self.assertIn("75", line)  # 已下载合计 75
            one.close()
            self.assertEqual(len(display._bars), 1)
            two.close()
            self.assertEqual(len(display._bars), 0)

    def test_painting_and_erase_roundtrip(self):
        with mock.patch.dict(os.environ, {"DEVKIT_PROGRESS": "1"}):
            sink = io.StringIO()
            display = _Display(stream=sink)
            bar = ProgressBar("p.zip", display=display)
            bar.update(100, 100, speed=1, eta=0)  # force 触发绘制
            self.assertIn("p.zip", sink.getvalue())
            bar.close()  # 注销最后一个 -> 擦除
            content = sink.getvalue()
            self.assertTrue(content.endswith("\r"))  # 擦除行以回车收尾

    def test_bar_registered_under_display(self):
        display = _Display(stream=io.StringIO())
        bar = ProgressBar("q.zip", display=display)
        self.assertIn(bar.id, display._bars)
        bar.close()
        self.assertNotIn(bar.id, display._bars)


if __name__ == "__main__":
    unittest.main()
