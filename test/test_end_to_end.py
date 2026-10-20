"""独立运行的子进程级冒烟测试（真实 python 解释器 + 真实脚本）。"""

from __future__ import annotations

import _common
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _common import ROOT

CLI = ROOT / "devkit" / "cli.py"
REPO_RECIPE = ROOT / "example" / "recipe.json"
HELLO_RECIPE = ROOT / "example" / "hello.json"


def run_cli(*args: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class TestStandaloneInvocation(unittest.TestCase):
    def test_version_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("--version", cwd=Path(td))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("devkit", proc.stdout)
        self.assertIn("0.1.0", proc.stdout)

    def test_no_args_prints_help(self):
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli(cwd=Path(td))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage", proc.stdout)

    def test_system_prints_identifier(self):
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("system", cwd=Path(td))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout.strip(), r"^[a-z0-9]+_[a-z0-9]+$")

    def test_missing_recipe_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("build", str(Path(td) / "nope.json"), cwd=Path(td))
        self.assertEqual(proc.returncode, 1)


class TestRepoRecipes(unittest.TestCase):
    def _require(self, path: Path) -> None:
        if not path.is_file():
            self.skipTest(f"缺少示例配方 {path.relative_to(ROOT)}")

    def test_repo_recipe_dry_run(self):
        """example/recipe.json: dry-run 全流程（克隆/下载/步骤/call 全部演练）。"""
        self._require(REPO_RECIPE)
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("build", str(REPO_RECIPE), "--dry-run",
                           "-w", td, "-d", td, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("配方", proc.stderr)
        self.assertIn("全部完成", proc.stderr)

    def test_hello_recipe_real_execution(self):
        """example/hello.json 真实执行（纯 echo，无网络），参数经 call data 绑定。"""
        self._require(HELLO_RECIPE)
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("build", str(HELLO_RECIPE), "-w", td, "-d", td, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hi", proc.stdout)      # data[0] = "hi"
        self.assertIn("0", proc.stdout)       # data[1] 占位符（顶层配方里是普通数据）
        self.assertIn("全部完成", proc.stderr)

    def test_dry_run_creates_nothing_in_cwd(self):
        self._require(REPO_RECIPE)
        with tempfile.TemporaryDirectory() as td:
            before = set(Path(td).iterdir())
            run_cli("build", str(REPO_RECIPE), "--dry-run", "-w", td, "-d", td, cwd=ROOT)
            after = set(Path(td).iterdir())
        self.assertEqual(before, after)


class TestRecipeSuitesOnDisk(unittest.TestCase):
    """test/fixtures 里的配方也能被加载执行（若存在）。"""

    def test_fixtures_if_present(self):
        fixtures = Path(__file__).resolve().parent / "fixtures"
        if not fixtures.is_dir():
            self.skipTest("无 fixtures 目录")
        for recipe_file in sorted(fixtures.rglob("*.json")):
            with self.subTest(recipe=recipe_file.name):
                with tempfile.TemporaryDirectory() as td:
                    proc = run_cli("build", str(recipe_file), "--dry-run",
                                   "-w", td, "-d", td, cwd=ROOT)
                self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
