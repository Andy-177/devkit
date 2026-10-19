"""dkpymodule 壳工程测试：同步器、包元数据、src 布局可导入。"""

from __future__ import annotations

import _common  # noqa: F401
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from devkit._version import __version__

SHELL = _common.ROOT / "dkpymodule"
SRC = SHELL / "src" / "devkit"


def _load_sync():
    spec = importlib.util.spec_from_file_location("dkpymodule_sync", SHELL / "sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sync = _load_sync()
        cls.sync.sync()

    def test_source_files_copied(self):
        for name in ("cli.py", "runner.py", "recipe.py", "_version.py", "errors.py"):
            self.assertTrue((SRC / name).is_file(), name)

    def test_generated_shell_files(self):
        init_text = (SRC / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("_ShimLogger", init_text)
        self.assertIn("from ._version import __version__", init_text)
        self.assertIn("dkpymodule/sync.py", init_text)
        main_text = (SRC / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("from devkit.cli import main", main_text)

    def test_no_init_in_real_devkit(self):
        self.assertFalse((_common.ROOT / "devkit" / "__init__.py").exists())
        self.assertFalse((_common.ROOT / "devkit" / "__main__.py").exists())

    def test_idempotent(self):
        before = {p.name: p.read_bytes() for p in sorted(SRC.glob("*.py"))}
        self.sync.sync()
        after = {p.name: p.read_bytes() for p in sorted(SRC.glob("*.py"))}
        self.assertEqual(before, after)


class TestPyproject(unittest.TestCase):
    def test_metadata(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib 需要 3.11+")
        data = tomllib.loads((SHELL / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["name"], "devkit")
        self.assertIn("version", project.get("dynamic", []))
        self.assertEqual(project["scripts"]["devkit"], "devkit.cli:main")
        self.assertTrue(any("loguru" in dep for dep in project["dependencies"]))
        self.assertEqual(
            data["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "devkit._version.__version__",
        )
        self.assertEqual(data["tool"]["setuptools"]["package-dir"][""], "src")

    def test_setup_runs_sync(self):
        text = (SHELL / "setup.py").read_text(encoding="utf-8")
        self.assertIn("from sync import sync", text)
        self.assertIn("sync()", text)
        self.assertIn("setup()", text)


class TestSrcLayoutImportable(unittest.TestCase):
    """子进程用 PYTHONPATH=src、cwd=临时目录（无同名包遮蔽）验证壳副本。"""

    @classmethod
    def setUpClass(cls):
        _load_sync().sync()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONPATH": str(SHELL / "src")}
        with tempfile.TemporaryDirectory() as td:
            return subprocess.run(
                [sys.executable, *args], cwd=td, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60,
            )

    def test_import_devkit(self):
        proc = self._run("-c", "import devkit;print(devkit.__version__, devkit.__file__)")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(__version__, proc.stdout)
        self.assertIn("dkpymodule", proc.stdout + proc.stderr)

    def test_module_entry_version(self):
        proc = self._run("-m", "devkit", "--version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(__version__, proc.stdout)

    def test_module_entry_system(self):
        proc = self._run("-m", "devkit", "system")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout.strip() + proc.stderr.strip(), r"[a-z0-9]+_[a-z0-9]+")


if __name__ == "__main__":
    unittest.main()
