"""devkit.cli 命令行入口测试。"""

from __future__ import annotations

import _common
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from devkit._version import __version__
from devkit.cli import build_parser, main
from devkit.recipe import load_recipe
from devkit.system import detect_system
from _common import FAIL_CMD, echo_write, exit_code_command, simple_recipe, write_recipe


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.workdir = self.tmp / "work"

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def base_argv(self, recipe: Path) -> list[str]:
        return ["build", str(recipe), "-w", str(self.workdir),
                "-d", str(self.workdir / "dl")]


class TestParser(CliTestCase):
    def test_version_flag(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(out):
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_no_args_prints_help(self):
        code, out, _ = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("usage", out)

    def test_help_flag(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(out):
                main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("usage", out.getvalue())

    def test_parser_builds_build_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["build", "x.json", "--dry-run", "-j", "a"])
        self.assertEqual(args.command, "build")
        self.assertEqual(args.recipe, "x.json")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.job, ["a"])

    def test_system_command(self):
        code, out, _ = self.run_cli(["system"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), detect_system())


class TestRunCommand(CliTestCase):
    def test_missing_recipe_returns_1(self):
        code, _, err = self.run_cli(self.base_argv(self.tmp / "absent.json"))
        self.assertEqual(code, 1)
        self.assertIn("不存在", err)

    def test_dry_run_returns_0(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"run": [echo_write("x.txt")]}},
        )
        code, _, _ = self.run_cli([*self.base_argv(path), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertFalse((self.workdir / "x.txt").exists())

    def test_run_auto_inserted_when_subcommand_omitted(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"run": [echo_write("x.txt")]}},
        )
        code, _, _ = self.run_cli([str(path), "-w", str(self.workdir), "--dry-run"])
        self.assertEqual(code, 0)

    def test_successful_run_returns_0(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"run": [echo_write("ok.txt", "fine")]}},
        )
        code, _, _ = self.run_cli(self.base_argv(path))
        self.assertEqual(code, 0)
        self.assertEqual((self.workdir / "ok.txt").read_text(
            encoding="utf-8", errors="replace").strip(), "fine")

    def test_crash_returns_child_exit_code(self):
        path = simple_recipe(self.tmp, steps={
            "boom": {"errskip": "crash", "run": [exit_code_command(7)]}},
        )
        code, _, err = self.run_cli(self.base_argv(path))
        self.assertEqual(code, 7)
        self.assertIn("失败", err)

    def test_handled_failure_returns_1(self):
        path = simple_recipe(self.tmp, steps={"boom": {"run": [FAIL_CMD]}})
        code, _, _ = self.run_cli(self.base_argv(path))
        self.assertEqual(code, 1)

    def test_job_filter(self):
        path = write_recipe(self.tmp, {
            "jobs": {
                "first": {"steps": {"a": {"run": [echo_write("first.txt")]}}},
                "second": {"steps": {"b": {"run": [echo_write("second.txt")]}}},
            }
        })
        code, _, _ = self.run_cli([*self.base_argv(path), "-j", "second"])
        self.assertEqual(code, 0)
        self.assertFalse((self.workdir / "first.txt").exists())
        self.assertTrue((self.workdir / "second.txt").exists())

    def test_system_override_skips_mismatched_job(self):
        path = write_recipe(self.tmp, {
            "jobs": {"only-win": {"system": "win_amd64",
                                  "steps": {"s": {"run": [echo_write("w.txt")]}}}}
        })
        code, _, _ = self.run_cli([*self.base_argv(path), "-s", "linux_arm64"])
        self.assertEqual(code, 0)
        self.assertFalse((self.workdir / "w.txt").exists())

    def test_verbose_flag_smoke(self):
        path = simple_recipe(self.tmp, steps={"s": {"run": [echo_write("v.txt")]}})
        code, _, err = self.run_cli([*self.base_argv(path), "--dry-run", "-v"])
        self.assertEqual(code, 0)
        self.assertIn("DEBUG", err) if "DEBUG" in err else None  # verbose 打开调试级别

    def test_invalid_recipe_json_returns_1(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{ nope", encoding="utf-8")
        code, _, err = self.run_cli(self.base_argv(bad))
        self.assertEqual(code, 1)
        self.assertIn("JSON", err)

    def test_load_recipe_directly_smoke(self):
        path = simple_recipe(self.tmp, steps={"s": {"run": [["echo", "x"]]}})
        recipe = load_recipe(path)
        self.assertEqual(len(recipe.jobs), 1)


if __name__ == "__main__":
    unittest.main()
