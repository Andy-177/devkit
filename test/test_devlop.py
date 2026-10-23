"""devlop 命令与交互终端测试。"""

from __future__ import annotations

import _common
import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from devkit import _color, devlop
from devkit.cli import build_parser, main
from devkit.devlop import DevlopShell, join_input_lines, prompt_text
from _common import FAIL_CMD, PY_SPACED, echo_write, make_runner, write_recipe

CHERRY = ("#e09bff", "#8a52bc", "#6b73ad", "#cc87eb", "#6b73ad", "#6b73ad")


def make_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


class DevlopTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.workdir = self.tmp / "work"

    def setup_shell(self, raw: dict) -> DevlopShell:
        path = write_recipe(self.tmp, raw)
        runner = make_runner(self.workdir)
        recipe, cwd = runner.prepare_devlop(path)
        return DevlopShell(runner, recipe, cwd, env=runner.devlop_env(recipe),
                           workdir=runner.workdir)


class TestPrompt(DevlopTestCase):
    def test_prompt_contains_user_host_and_absolute_path(self):
        text = prompt_text(self.tmp, user="u", host="h")
        self.assertEqual(text, f"devlop u@h {self.tmp.resolve()} $\n└─> ")

    def test_continuation_prompt(self):
        self.assertEqual(devlop.CONTINUATION, " ╰> ")

    def test_join_input_lines(self):
        self.assertEqual(join_input_lines("cd\n/?"), "cd /?")
        self.assertEqual(join_input_lines("cd\n/?\n"), "cd /?")
        self.assertEqual(join_input_lines("  \n\n"), "")
        self.assertEqual(join_input_lines("echo\n\n hi "), "echo hi")


class TestColor(DevlopTestCase):
    def test_normalize_accepts_six_color_tuple(self):
        self.assertEqual(devlop.normalize_shell_colors(CHERRY), CHERRY)

    def test_normalize_strips_color_whitespace(self):
        padded = tuple(f" {color} " for color in CHERRY)
        self.assertEqual(devlop.normalize_shell_colors(padded), CHERRY)

    def test_normalize_falls_back_to_white(self):
        white = devlop.DEFAULT_COLORS
        self.assertEqual(len(white), 6)
        self.assertEqual(set(white), {"#ffffff"})

        for value in (
            list(CHERRY),
            CHERRY[:5],
            CHERRY + ("#ffffff",),
            ("#ffffff", "#ffffff", "#ffffff", "#ffffff", "#ffffff", 1),
            ("#ffffff", "#ffffff", "#ffffff", "#ffffff", "#ffffff", ""),
            ("#ffffff", "#ffffff", "#ffffff", "#ffffff", "#ffffff", "not-a-color"),
            "ffffff",
            None,
        ):
            with self.subTest(value=value):
                self.assertEqual(devlop.normalize_shell_colors(value), white)

    def test_load_reads_user_function(self):
        self.assertEqual(devlop.load_shell_colors(lambda: CHERRY), CHERRY)

    def test_load_falls_back_when_function_raises(self):
        def boom():
            raise RuntimeError("boom")

        self.assertEqual(devlop.load_shell_colors(boom), devlop.DEFAULT_COLORS)

    def test_load_reads_color_file(self):
        self.assertEqual(
            devlop.load_shell_colors(),
            devlop.normalize_shell_colors(_color.devlop_shell_color()),
        )

    def test_prompt_fragments_use_all_six_colors(self):
        fragments = devlop.prompt_fragments(
            self.tmp, user="u", host="h", colors=CHERRY
        )
        self.assertIn(("fg:#e09bff", "devlop"), fragments)
        self.assertIn(("fg:#8a52bc", "u@h"), fragments)
        self.assertIn(("fg:#6b73ad", str(self.tmp.resolve())), fragments)
        self.assertIn(("fg:#cc87eb", "$"), fragments)
        self.assertIn(("fg:#6b73ad", "└─>"), fragments)
        self.assertIn(("fg:#6b73ad", "╰>"), devlop.continuation_fragments(CHERRY))

    def test_shell_loads_colors(self):
        shell = self.setup_shell({})
        self.assertEqual(shell.colors, devlop.load_shell_colors())


class TestPrepare(DevlopTestCase):
    def test_prepare_runs_non_job_parts_only(self):
        make_zip(self.tmp / "assets.zip", {"hello.txt": "hi"})
        path = write_recipe(self.tmp, {
            "unzip": ["assets.zip"],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("job.txt")]}}}},
        })
        runner = make_runner(self.workdir)
        recipe, cwd = runner.prepare_devlop(path)

        self.assertEqual(cwd, self.workdir)
        self.assertEqual(recipe.path, path)
        self.assertTrue((self.workdir / "dl" / "assets" / "hello.txt").is_file())
        self.assertFalse((self.workdir / "job.txt").exists())

    def test_env_is_recipe_level_only(self):
        path = write_recipe(self.tmp, {
            "env": [{"key": "DK_TEST", "value": "recipe"}],
            "jobs": {"main": {
                "env": [{"key": "DK_TEST", "value": "job"}],
                "steps": {"s": {
                    "env": [{"key": "DK_TEST", "value": "step"}],
                    "run": [echo_write("job.txt")],
                }},
            }},
        })
        runner = make_runner(self.workdir)
        recipe, _ = runner.prepare_devlop(path)
        self.assertEqual(runner.devlop_env(recipe), {"DK_TEST": "recipe"})

    def test_area_replaces_recipe_env(self):
        path = write_recipe(self.tmp, {
            "env": [{"key": "A", "value": "1"}],
            "area": [{"key": "B", "value": "2"}],
        })
        runner = make_runner(self.workdir)
        recipe, _ = runner.prepare_devlop(path)
        self.assertEqual(runner.devlop_env(recipe), {"B": "2"})


class TestShell(DevlopTestCase):
    def test_default_cwd_is_workspace(self):
        shell = self.setup_shell({})
        self.assertEqual(shell.cwd, self.workdir.resolve())

    def test_cd_and_cd_dash_and_cd_home(self):
        shell = self.setup_shell({})
        sub = self.workdir / "sub"
        sub.mkdir()

        self.assertTrue(shell.execute_line("cd sub"))
        self.assertEqual(shell.cwd, sub.resolve())
        self.assertIn(str(sub.resolve()), shell.prompt)

        shell.execute_line("cd -")
        self.assertEqual(shell.cwd, self.workdir.resolve())

        shell.execute_line("cd sub")
        shell.execute_line("cd")
        self.assertEqual(shell.cwd, self.workdir.resolve())

    def test_cd_quoted_directory(self):
        shell = self.setup_shell({})
        sub = self.workdir / "sub dir"
        sub.mkdir()
        shell.execute_line('cd "sub dir"')
        self.assertEqual(shell.cwd, sub.resolve())

    def test_cd_missing_directory_keeps_cwd(self):
        shell = self.setup_shell({})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(shell.execute_line("cd nope"))
        self.assertEqual(shell.cwd, self.workdir.resolve())
        self.assertIn("目录不存在", err.getvalue())

    def test_exit_commands(self):
        shell = self.setup_shell({})
        self.assertFalse(shell.execute_line("exit"))
        self.assertFalse(shell.execute_line("quit"))
        self.assertFalse(shell.execute_line("logout"))

    def test_run_command_executes_in_cwd(self):
        shell = self.setup_shell({})
        sub = self.workdir / "sub"
        sub.mkdir()
        shell.execute_line("cd sub")
        shell.execute_line("echo hi > out.txt")
        self.assertEqual((sub / "out.txt").read_text(encoding="utf-8").strip(), "hi")

    def test_command_failure_does_not_exit_terminal(self):
        shell = self.setup_shell({})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(shell.execute_line(FAIL_CMD[0]))
        self.assertTrue(err.getvalue())

    def test_multiline_input_is_joined(self):
        shell = self.setup_shell({})
        shell.execute_line("echo\nhi > out.txt")
        self.assertEqual((self.workdir / "out.txt").read_text(encoding="utf-8").strip(), "hi")

    @unittest.skipIf(PY_SPACED, "sys.executable 含空格，无法安全拼进 shell 命令")
    def test_global_env_applied_but_job_step_env_is_not(self):
        script = self.workdir / "dump_env.py"
        self.workdir.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path('env.txt').write_text(os.environ.get('DK_TEST', ''), encoding='utf-8')\n",
            encoding="utf-8",
        )
        shell = self.setup_shell({
            "env": [{"key": "DK_TEST", "value": "recipe"}],
            "jobs": {"main": {
                "env": [{"key": "DK_TEST", "value": "job"}],
                "steps": {"s": {
                    "env": [{"key": "DK_TEST", "value": "step"}],
                    "run": [echo_write("job.txt")],
                }},
            }},
        })
        shell.execute_line(f"{sys.executable} dump_env.py")
        self.assertEqual((self.workdir / "env.txt").read_text(encoding="utf-8"), "recipe")

    def test_run_expression_applied_when_enabled(self):
        shell = self.setup_shell({"expression": ["run"]})
        shell.execute_line("echo ?!{{1 + 1}} > n.txt")
        self.assertEqual((self.workdir / "n.txt").read_text(encoding="utf-8").strip(), "2")

    def test_run_loop_executes_multiline_and_exits_on_ctrl_d(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        shell = self.setup_shell({})
        with create_pipe_input() as pipe:
            pipe.send_text("echo\nhi > out.txt\r")
            pipe.send_text("\x04")
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                shell.run(input=pipe, output=DummyOutput())
        self.assertEqual((self.workdir / "out.txt").read_text(encoding="utf-8").strip(), "hi")


class TestCli(DevlopTestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_parser_builds_devlop_subcommand(self):
        args = build_parser().parse_args(["devlop", "x.json", "--dry-run"])
        self.assertEqual(args.command, "devlop")
        self.assertEqual(args.recipe, "x.json")
        self.assertTrue(args.dry_run)
        self.assertFalse(hasattr(args, "job"))

    def test_help_lists_devlop(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(out):
                main(["--help"])
        self.assertIn("devlop", out.getvalue())

    def test_cli_dispatches_to_devlop(self):
        with mock.patch("devkit.devlop.run_devlop", return_value=0) as run:
            code, _, _ = self.run_cli([
                "devlop", "x.json", "-w", str(self.workdir),
                "-d", str(self.workdir / "dl"),
            ])
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[1], "x.json")

    def test_missing_recipe_returns_1_without_entering_terminal(self):
        code, _, err = self.run_cli([
            "devlop", str(self.tmp / "absent.json"),
            "-w", str(self.workdir), "-d", str(self.workdir / "dl"),
        ])
        self.assertEqual(code, 1)
        self.assertIn("不存在", err)


if __name__ == "__main__":
    unittest.main()
