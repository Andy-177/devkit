"""devkit.git ensure_clone 测试（本地仓库，不触网）。"""

from __future__ import annotations

import _common
import shutil
import tempfile
import unittest
from pathlib import Path

from devkit.errors import CloneError
from devkit.git import ensure_clone
from devkit.recipe import Material

try:
    import dulwich  # noqa: F401
    HAS_DULWICH = True
except Exception:
    HAS_DULWICH = False

needs_dulwich = unittest.skipUnless(HAS_DULWICH, "未安装 dulwich（只有 venv 里有）")


def make_origin(tmp: Path) -> Path:
    """用 dulwich 造一个本地提交过的仓库，返回其路径。"""
    from dulwich import porcelain

    origin = tmp / "origin"
    porcelain.init(str(origin))
    (origin / "hello.txt").write_text("hello from origin", encoding="utf-8")
    porcelain.add(str(origin), paths=[b"hello.txt"])
    porcelain.commit(str(origin), message=b"init",
                     author=b"test <test@example.com>")
    return origin


@needs_dulwich
class TestEnsureClone(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_clone_local_repo(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "work" / "repo"
        result = ensure_clone(Material(repo=str(origin)), target)
        self.assertEqual(result, target)
        self.assertTrue((target / ".git").exists())
        self.assertEqual((target / "hello.txt").read_text(encoding="utf-8"),
                         "hello from origin")
        # 克隆临时目录不残留
        self.assertFalse((target.parent / (target.name + ".devkit-clone")).exists())

    def test_second_clone_skipped(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "work" / "repo"
        first = ensure_clone(Material(repo=str(origin)), target)
        (target / "marker.txt").write_text("keep", encoding="utf-8")
        second = ensure_clone(Material(repo=str(origin)), target)
        self.assertEqual(first, second)
        self.assertEqual((target / "marker.txt").read_text(encoding="utf-8"), "keep")

    def test_target_occupied_by_file(self):
        target = self.tmp / "work"
        target.write_text("i am a file", encoding="utf-8")
        with self.assertRaisesRegex(CloneError, "同名文件"):
            ensure_clone(Material(repo="unused"), target)

    def test_target_nonempty_not_git(self):
        target = self.tmp / "work"
        target.mkdir()
        (target / "junk.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(CloneError, "非空"):
            ensure_clone(Material(repo="unused"), target)

    def test_clone_failure_cleans_staging(self):
        target = self.tmp / "work" / "repo"
        with self.assertRaisesRegex(CloneError, "失败"):
            ensure_clone(Material(repo=str(self.tmp / "no-such-repo")), target)
        self.assertFalse(target.exists())
        self.assertFalse((target.parent / (target.name + ".devkit-clone")).exists())

    def test_empty_dir_target_is_replaced(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "empty"
        target.mkdir()
        result = ensure_clone(Material(repo=str(origin)), target)
        self.assertTrue((result / ".git").exists())


if __name__ == "__main__":
    unittest.main()
