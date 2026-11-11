"""devkit.git ensure_clone 测试（本地仓库，不触网）。"""

from __future__ import annotations

import _common
import shutil
import tempfile
import unittest
from pathlib import Path

from devkit.errors import CloneError
from devkit.git import ensure_clone
from devkit.recipe import Clone

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


def make_superproject(tmp: Path, sub_origin: Path) -> Path:
    """造一个带 gitlink 子模块的仓库，返回其路径。"""
    from dulwich import porcelain
    from dulwich.index import IndexEntry

    super_origin = tmp / "super"
    porcelain.init(str(super_origin))
    porcelain.submodule_add(str(super_origin), sub_origin.as_uri(), path="sub")
    (super_origin / "readme.txt").write_text("super", encoding="utf-8")
    porcelain.add(str(super_origin), paths=[b".gitmodules", b"readme.txt"])
    with porcelain.open_repo_closing(str(sub_origin)) as sub_repo:
        sub_sha = sub_repo.head()
    with porcelain.open_repo_closing(str(super_origin)) as repo:
        index = repo.open_index()
        index[b"sub"] = IndexEntry(
            ctime=0, mtime=0, dev=0, ino=0, mode=0o160000,
            uid=0, gid=0, size=0, sha=sub_sha,
        )
        index.write()
    porcelain.commit(str(super_origin), message=b"init with submodule",
                     author=b"test <test@example.com>")
    return super_origin


@needs_dulwich
class TestEnsureClone(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_clone_local_repo(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "work" / "repo"
        result = ensure_clone(Clone(repo=str(origin)), target)
        self.assertEqual(result, target)
        self.assertTrue((target / ".git").exists())
        self.assertEqual((target / "hello.txt").read_text(encoding="utf-8"),
                         "hello from origin")
        # 克隆临时目录不残留
        self.assertFalse((target.parent / (target.name + ".devkit-clone")).exists())

    def test_second_clone_skipped(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "work" / "repo"
        first = ensure_clone(Clone(repo=str(origin)), target)
        (target / "marker.txt").write_text("keep", encoding="utf-8")
        second = ensure_clone(Clone(repo=str(origin)), target)
        self.assertEqual(first, second)
        self.assertEqual((target / "marker.txt").read_text(encoding="utf-8"), "keep")

    def test_target_occupied_by_file(self):
        target = self.tmp / "work"
        target.write_text("i am a file", encoding="utf-8")
        with self.assertRaisesRegex(CloneError, "同名文件"):
            ensure_clone(Clone(repo="unused"), target)

    def test_target_nonempty_not_git(self):
        target = self.tmp / "work"
        target.mkdir()
        (target / "junk.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(CloneError, "非空"):
            ensure_clone(Clone(repo="unused"), target)

    def test_clone_failure_cleans_staging(self):
        target = self.tmp / "work" / "repo"
        with self.assertRaisesRegex(CloneError, "失败"):
            ensure_clone(Clone(repo=str(self.tmp / "no-such-repo")), target)
        self.assertFalse(target.exists())
        self.assertFalse((target.parent / (target.name + ".devkit-clone")).exists())

    def test_empty_dir_target_is_replaced(self):
        origin = make_origin(self.tmp)
        target = self.tmp / "empty"
        target.mkdir()
        result = ensure_clone(Clone(repo=str(origin)), target)
        self.assertTrue((result / ".git").exists())

    def test_recursive_clones_submodules(self):
        sub_tmp = self.tmp / "subsrc"
        sub_tmp.mkdir()
        sub_origin = make_origin(sub_tmp)
        super_origin = make_superproject(self.tmp, sub_origin)
        target = self.tmp / "work" / "super"
        ensure_clone(Clone(repo=str(super_origin), recursive=True), target)
        self.assertEqual(
            (target / "sub" / "hello.txt").read_text(encoding="utf-8"),
            "hello from origin",
        )

    def test_non_recursive_leaves_submodules_absent(self):
        sub_tmp = self.tmp / "subsrc"
        sub_tmp.mkdir()
        sub_origin = make_origin(sub_tmp)
        super_origin = make_superproject(self.tmp, sub_origin)
        target = self.tmp / "work" / "super"
        ensure_clone(Clone(repo=str(super_origin)), target)
        self.assertFalse((target / "sub" / "hello.txt").exists())


if __name__ == "__main__":
    unittest.main()
