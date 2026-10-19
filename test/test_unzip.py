"""devkit.unzip 解压与 zip 路径解析测试。"""

from __future__ import annotations

import _common
import tempfile
import unittest
import zipfile
from pathlib import Path

from devkit.errors import UnzipError
from devkit.recipe import UnzipSpec
from devkit.unzip import resolve_zip, unzip_one


def make_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


class TestResolveZip(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_absolute_existing(self):
        z = make_zip(self.tmp / "a.zip", {"x.txt": "x"})
        self.assertEqual(resolve_zip(str(z), [self.tmp]), z)

    def test_absolute_missing_strict(self):
        with self.assertRaisesRegex(UnzipError, "找不到"):
            resolve_zip(str(self.tmp / "nope.zip"), [self.tmp])

    def test_absolute_missing_non_strict(self):
        p = self.tmp / "nope.zip"
        self.assertEqual(resolve_zip(str(p), [self.tmp], strict=False), p)

    def test_relative_search_order(self):
        first = self.tmp / "a"
        second = self.tmp / "b"
        first.mkdir()
        second.mkdir()
        make_zip(second / "tool.zip", {"x": "in-second"})
        make_zip(first / "tool.zip", {"x": "in-first"})
        bases = [first, second]
        self.assertEqual(resolve_zip("tool.zip", bases), first / "tool.zip")
        (first / "tool.zip").unlink()
        self.assertEqual(resolve_zip("tool.zip", bases), second / "tool.zip")

    def test_relative_missing_strict_lists_tried(self):
        with self.assertRaisesRegex(UnzipError, "已尝试"):
            resolve_zip("tool.zip", [self.tmp / "a", self.tmp / "b"])

    def test_relative_missing_non_strict_prefers_first_base(self):
        got = resolve_zip("tool.zip", [self.tmp / "a"], strict=False)
        self.assertEqual(got, self.tmp / "a" / "tool.zip")


class TestUnzipOne(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.dest = self.tmp / "out"

    def test_extracts_files_and_dirs(self):
        z = make_zip(self.tmp / "tool.zip",
                     {"hello.txt": "hi", "sub/dir/file.txt": "deep"})
        result = unzip_one(UnzipSpec("tool.zip"), [self.tmp], self.dest, "win_amd64")
        self.assertEqual(result, self.dest)
        self.assertEqual((self.dest / "hello.txt").read_text(encoding="utf-8"), "hi")
        self.assertEqual((self.dest / "sub" / "dir" / "file.txt")
                         .read_text(encoding="utf-8"), "deep")

    def test_system_filter_mismatch_skips(self):
        z = make_zip(self.tmp / "tool.zip", {"x": "y"})
        result = unzip_one(UnzipSpec("tool.zip", ("linux_amd64",)),
                           [self.tmp], self.dest, "win_amd64")
        self.assertIsNone(result)
        self.assertFalse(self.dest.exists())

    def test_dry_run_missing_zip_ok(self):
        result = unzip_one(UnzipSpec("absent.zip"), [self.tmp], self.dest,
                           "win_amd64", dry_run=True)
        self.assertIsNone(result)
        self.assertFalse(self.dest.exists())

    def test_dry_run_existing_zip_not_extracted(self):
        make_zip(self.tmp / "tool.zip", {"x": "y"})
        result = unzip_one(UnzipSpec("tool.zip"), [self.tmp], self.dest,
                           "win_amd64", dry_run=True)
        self.assertIsNone(result)
        self.assertFalse((self.dest / "x").exists())

    def test_missing_zip_strict_raises(self):
        with self.assertRaises(UnzipError):
            unzip_one(UnzipSpec("absent.zip"), [self.tmp], self.dest, "win_amd64")

    def test_bad_zip_raises(self):
        (self.tmp / "broken.zip").write_bytes(b"this is not a zip")
        with self.assertRaisesRegex(UnzipError, "不是合法的 zip"):
            unzip_one(UnzipSpec("broken.zip"), [self.tmp], self.dest, "win_amd64")

    def test_zip_slip_rejected(self):
        z = make_zip(self.tmp / "evil.zip", {"../evil.txt": "pwned"})
        with self.assertRaisesRegex(UnzipError, "之外"):
            unzip_one(UnzipSpec("evil.zip"), [self.tmp], self.dest, "win_amd64")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_absolute_zip_path(self):
        z = make_zip(self.tmp / "abs.zip", {"a.txt": "A"})
        result = unzip_one(UnzipSpec(str(z)), [self.tmp / "nonexistent"], self.dest,
                           "win_amd64")
        self.assertEqual(result, self.dest)
        self.assertEqual((self.dest / "a.txt").read_text(encoding="utf-8"), "A")


if __name__ == "__main__":
    unittest.main()
