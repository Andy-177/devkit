"""devkit.dl 下载的跳过/命名逻辑测试（不触网）。"""

from __future__ import annotations

import _common
import tempfile
import unittest
from pathlib import Path

from devkit.dl import download_one, filename_for
from devkit.recipe import DownloadSpec


class TestFilenameFor(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(filename_for("https://example.com/a/b/tool.zip"), "tool.zip")

    def test_query_and_fragment_ignored(self):
        self.assertEqual(filename_for("https://example.com/tool.zip?v=2#top"), "tool.zip")

    def test_percent_encoded(self):
        self.assertEqual(filename_for("https://example.com/my%20tool.zip"), "my tool.zip")

    def test_trailing_slash(self):
        self.assertEqual(filename_for("https://example.com/"), "download.bin")

    def test_no_path(self):
        self.assertEqual(filename_for("https://example.com"), "download.bin")


class TestDownloadOne(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.dest = self.tmp / "dl"

    def test_system_filter_mismatch_skips(self):
        spec = DownloadSpec("https://example.com/tool.zip", ("linux_amd64",))
        self.assertIsNone(download_one(spec, self.dest, "win_amd64"))
        self.assertFalse(self.dest.exists())

    def test_dry_run_returns_none_without_download(self):
        spec = DownloadSpec("https://example.com/tool.zip")
        self.assertIsNone(download_one(spec, self.dest, "win_amd64", dry_run=True))
        self.assertFalse((self.dest / "tool.zip").exists())

    def test_existing_file_skipped_no_network(self):
        self.dest.mkdir(parents=True)
        existing = self.dest / "tool.zip"
        existing.write_bytes(b"cached")
        spec = DownloadSpec("https://example.com/tool.zip")
        result = download_one(spec, self.dest, "win_amd64")
        self.assertEqual(result, existing)
        self.assertEqual(existing.read_bytes(), b"cached")  # 没有被重新下载


if __name__ == "__main__":
    unittest.main()
