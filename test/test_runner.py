"""devkit.runner 执行引擎的端到端/行为测试（真实 shell、临时目录）。"""

from __future__ import annotations

import _common
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from devkit.errors import InteractiveInputError, RecipeError, StepError
from devkit.recipe import Recipe, Step
from devkit.runner import RecipeRunner
from _common import (
    FAIL_CMD,
    PY_SPACED,
    TEST_SYSTEM,
    dummy,
    echo_write,
    exit_code_command,
    make_runner,
    py_command,
    run_recipe,
    simple_recipe,
    slow_write,
    write_recipe,
)


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.workdir = self.tmp / "work"

    def run_raw(self, raw: dict, **kwargs) -> RecipeRunner:
        path = write_recipe(self.tmp, raw)
        return run_recipe(path, self.workdir, **kwargs)


class TestBasicExecution(RunnerTestCase):
    def test_shell_commands_run_in_declared_order(self):
        path = simple_recipe(self.tmp, steps={
            "first": {"run": [echo_write("first.txt", "one")]},
            "second": {"run": [echo_write("second.txt", "two")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertEqual(_common.read(self.workdir / "first.txt"), "one")
        self.assertEqual(_common.read(self.workdir / "second.txt"), "two")

    def test_commands_within_one_step_run_in_order(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"run": [echo_write("a.txt", "A"), echo_write("b.txt", "B")]},
        })
        run_recipe(path, self.workdir)
        self.assertTrue((self.workdir / "a.txt").exists())
        self.assertTrue((self.workdir / "b.txt").exists())

    def test_data_substitution(self):
        path = simple_recipe(
            self.tmp,
            steps={"s": {"run": [["echo", 0, ">", "out.txt"]]}},
            data=["hello-devkit"],
        )
        run_recipe(path, self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "hello-devkit")

    def test_data_index_out_of_range(self):
        path = simple_recipe(
            self.tmp,
            steps={"s": {"run": [["echo", 5, ">", "out.txt"]]}},
            data=["only-one"],
        )
        runner = make_runner(self.workdir)
        with self.assertRaisesRegex(RecipeError, "越界"):
            runner.run(path)

    def test_empty_fragment_after_subst_raises(self):
        path = simple_recipe(
            self.tmp,
            steps={"s": {"run": [[0]]}},
            data=[""],
        )
        runner = make_runner(self.workdir)
        with self.assertRaisesRegex(RecipeError, "不能为空"):
            runner.run(path)

    def test_nonzero_exit_raises_step_error_with_returncode(self):
        path = simple_recipe(self.tmp, steps={
            "boom": {"errskip": "crash", "run": [exit_code_command(7)]},
        })
        runner = make_runner(self.workdir)
        with self.assertRaises(StepError) as ctx:
            runner.run(path)
        self.assertEqual(ctx.exception.returncode, 7)

    def test_failing_command_not_found_is_step_error(self):
        path = simple_recipe(self.tmp, steps={
            "boom": {"errskip": "crash", "run": [FAIL_CMD]},
        })
        runner = make_runner(self.workdir)
        with self.assertRaises(StepError) as ctx:
            runner.run(path)
        self.assertNotEqual(ctx.exception.returncode, 0)


class TestJobControl(RunnerTestCase):
    def _two_jobs(self, *, second_system=None, second_skip=None):
        second: dict = {"steps": {"s2": {"run": [echo_write("second.txt", "2")]}}}
        if second_system:
            second["system"] = second_system
        if second_skip is not None:
            second["skip"] = second_skip
        return {
            "jobs": {
                "first": {"steps": {"s1": {"run": [echo_write("first.txt", "1")]}}},
                "second": second,
            }
        }

    def test_job_system_mismatch_skipped(self):
        runner = self.run_raw(self._two_jobs(second_system="linux_arm64"))
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "first.txt").exists())
        self.assertFalse((self.workdir / "second.txt").exists())

    def test_job_system_match_runs(self):
        runner = self.run_raw(self._two_jobs(second_system=TEST_SYSTEM))
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "second.txt").exists())

    def test_job_skip_skipped_in_normal_flow(self):
        runner = self.run_raw(self._two_jobs(second_skip=True))
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "second.txt").exists())

    def test_job_skip_overridden_by_next(self):
        raw = self._two_jobs(second_skip=True)
        raw["jobs"]["first"]["steps"]["s1"]["next"] = "second"
        runner = self.run_raw(raw)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "second.txt").exists())

    def test_job_failure_default_proceed_continues_and_flags_failed(self):
        raw = {
            "jobs": {
                "bad": {"steps": {"boom": {"run": [FAIL_CMD]}}},  # 默认 stop -> 作业失败
                "good": {"steps": {"ok": {"run": [echo_write("ok.txt", "1")]}}},
            }
        }
        runner = self.run_raw(raw)
        self.assertTrue(runner.failed)
        self.assertTrue((self.workdir / "ok.txt").exists())  # 后面的作业仍然执行

    def test_job_errskip_crash_aborts(self):
        raw = {
            "jobs": {
                "bad": {"errskip": "crash", "steps": {"boom": {"run": [FAIL_CMD]}}},
                "good": {"steps": {"ok": {"run": [echo_write("ok.txt", "1")]}}},
            }
        }
        runner = make_runner(self.workdir)
        path = write_recipe(self.tmp, raw)
        with self.assertRaises(StepError):
            runner.run(path)
        self.assertFalse((self.workdir / "ok.txt").exists())

    def test_job_filter_selects_only_requested(self):
        raw = self._two_jobs()
        path = write_recipe(self.tmp, raw)
        runner = make_runner(self.workdir, jobs=["second"])
        runner.run(path)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "first.txt").exists())
        self.assertTrue((self.workdir / "second.txt").exists())

    def test_job_linkup_runs_both_concurrently(self):
        raw = {
            "jobs": {
                "a": {"linkup": "b",
                      "steps": {"sa": {"run": [echo_write("a.txt", "a")]}}},
                "b": {"skip": True,
                      "steps": {"sb": {"run": [echo_write("b.txt", "b")]}}},
            }
        }
        runner = self.run_raw(raw)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "a.txt").exists())
        self.assertTrue((self.workdir / "b.txt").exists())  # linkup 指到时 skip 不拦

    def test_missing_next_job_raises(self):
        raw = {"jobs": {"main": {"steps": {"s": {
            "run": [echo_write("x.txt")], "next": "ghost-job"}}}}}
        runner = make_runner(self.workdir)
        path = write_recipe(self.tmp, raw)
        with self.assertRaisesRegex(RecipeError, "不存在"):
            runner.run(path)


class TestStepControl(RunnerTestCase):
    def test_step_skip(self):
        path = simple_recipe(self.tmp, steps={
            "s1": {"skip": True, "run": [echo_write("s1.txt")]},
            "s2": {"run": [echo_write("s2.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "s1.txt").exists())
        self.assertTrue((self.workdir / "s2.txt").exists())

    def test_jump_skips_intermediate(self):
        path = simple_recipe(self.tmp, steps={
            "s1": {"jump": "s3", "run": [echo_write("s1.txt")]},
            "s2": {"run": [echo_write("s2.txt")]},
            "s3": {"run": [echo_write("s3.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "s1.txt").exists())
        self.assertFalse((self.workdir / "s2.txt").exists())
        self.assertTrue((self.workdir / "s3.txt").exists())

    def test_redirect_skips_source_step(self):
        path = simple_recipe(self.tmp, steps={
            "s1": {"if": {"redirect": {"s1": "s3"}}, "run": [echo_write("s1.txt")]},
            "s2": {"run": [echo_write("s2.txt")]},
            "s3": {"run": [echo_write("s3.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "s1.txt").exists())  # 走到 s1 直接跳走
        self.assertFalse((self.workdir / "s2.txt").exists())
        self.assertTrue((self.workdir / "s3.txt").exists())

    def test_if_success_jump(self):
        path = simple_recipe(self.tmp, steps={
            "s1": {"if": {"success": "s3"}, "run": [echo_write("s1.txt")]},
            "s2": {"run": [echo_write("s2.txt")]},
            "s3": {"run": [echo_write("s3.txt")]},
        })
        run_recipe(path, self.workdir)
        self.assertTrue((self.workdir / "s1.txt").exists())
        self.assertFalse((self.workdir / "s2.txt").exists())
        self.assertTrue((self.workdir / "s3.txt").exists())

    def test_if_error_jump_with_proceed(self):
        path = simple_recipe(self.tmp, steps={
            "s1": {"errskip": "proceed", "if": {"error": "s3"}, "run": [FAIL_CMD]},
            "s2": {"run": [echo_write("s2.txt")]},
            "s3": {"run": [echo_write("s3.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertTrue(runner.failed)  # 被 errskip 处理的失败要体现在最终结果里
        self.assertFalse((self.workdir / "s2.txt").exists())
        self.assertTrue((self.workdir / "s3.txt").exists())

    def test_errskip_proceed_continues_next_step(self):
        path = simple_recipe(self.tmp, steps={
            "boom": {"errskip": "proceed", "run": [FAIL_CMD]},
            "after": {"run": [echo_write("after.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertTrue(runner.failed)
        self.assertTrue((self.workdir / "after.txt").exists())

    def test_errskip_stop_ends_job(self):
        raw = {
            "jobs": {
                "main": {"steps": {
                    "boom": {"errskip": "stop", "run": [FAIL_CMD]},
                    "never": {"run": [echo_write("never.txt")]},
                }},
                "next-job": {"steps": {"ok": {"run": [echo_write("ok.txt")]}}},
            }
        }
        runner = self.run_raw(raw)
        self.assertTrue(runner.failed)
        self.assertFalse((self.workdir / "never.txt").exists())
        self.assertTrue((self.workdir / "ok.txt").exists())

    def test_next_null_ends_current_job(self):
        raw = {
            "jobs": {
                "main": {"steps": {
                    "s1": {"next": None, "run": [echo_write("s1.txt")]},
                    "s2": {"run": [echo_write("s2.txt")]},
                }},
                "second": {"steps": {"s3": {"run": [echo_write("s3.txt")]}}},
            }
        }
        runner = self.run_raw(raw)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "s1.txt").exists())
        self.assertFalse((self.workdir / "s2.txt").exists())
        self.assertTrue((self.workdir / "s3.txt").exists())

    def test_next_switches_job(self):
        raw = {
            "jobs": {
                "first": {"steps": {"s1": {"next": "second",
                                           "run": [echo_write("s1.txt")]}}},
                "second": {"skip": True,
                           "steps": {"s2": {"run": [echo_write("s2.txt")]}}},
            }
        }
        runner = self.run_raw(raw)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "s1.txt").exists())
        self.assertTrue((self.workdir / "s2.txt").exists())  # skip 被显式 next 覆盖

    def test_missing_jump_target_raises(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"jump": "ghost", "run": [echo_write("x.txt")]},
        })
        runner = make_runner(self.workdir)
        with self.assertRaisesRegex(RecipeError, "不存在"):
            runner.run(path)

    def test_missing_if_target_raises(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"errskip": "proceed", "if": {"error": "ghost"},
                  "run": [FAIL_CMD]},
        })
        runner = make_runner(self.workdir)
        with self.assertRaisesRegex(RecipeError, "不存在"):
            runner.run(path)


class TestCall(RunnerTestCase):
    def test_call_binds_args_into_child_data(self):
        write_recipe(self.tmp, {
            "data": ["hi", 0],
            "jobs": {"print": {"steps": {"s": {
                "run": [["echo", 1, ">", "child.txt"]]}}}},
        }, "child.json")
        path = simple_recipe(self.tmp, steps={
            "s": {"call": {"child.json": ["hello-arg"]}},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        # 无 material 的被调用配方与父配方共用工作区
        self.assertEqual(_common.read(self.workdir / "child.txt"), "hello-arg")

    def test_call_directory_finds_recipe_json(self):
        sub = self.tmp / "sub"
        write_recipe(sub, {
            "jobs": {"print": {"steps": {"s": {
                "run": [["echo", "from-dir", ">", "dirchild.txt"]]}}}},
        }, "recipe.json")
        path = simple_recipe(self.tmp, steps={"s": {"call": {"sub": []}}})
        run_recipe(path, self.workdir)
        self.assertEqual(_common.read(self.workdir / "dirchild.txt"), "from-dir")

    def test_call_cycle_detected(self):
        path = write_recipe(self.tmp, {
            "jobs": {"main": {"steps": {"s": {"call": {"recipe.json": []}}}}},
        })
        runner = make_runner(self.workdir)
        with self.assertRaisesRegex(RecipeError, "循环"):
            runner.run(path)

    def test_child_with_material_gets_own_subdir(self):
        write_recipe(self.tmp, {
            "material": {"repo": "https://example.invalid/x.git"},
            "jobs": {"main": {"steps": {"s": {
                "run": [["echo", "kid", ">", "kid.txt"]]}}}},
        }, "kid.json")
        path = simple_recipe(self.tmp, steps={
            "s": {"call": {"kid.json": []}},
        })
        runner = run_recipe(path, self.workdir, dry_run=True)
        self.assertFalse(runner.failed)
        # 有 material 的子配方: 工作区下用配方名开了独立子目录
        kid_dir = self.workdir / "kid"
        self.assertTrue(kid_dir.exists())
        # dry-run 下没有真的克隆，父配方也没有 material
        self.assertFalse((kid_dir / "workspace").exists())
        self.assertFalse((self.workdir / "workspace").exists())


class TestLinkup(RunnerTestCase):
    def test_linked_steps_both_run(self):
        # 发起联动的步骤不能 skip（skip 会把发起者本身也跳过，联动就不触发）
        path = simple_recipe(self.tmp, steps={
            "slow": {"linkup": ["fast"], "run": [slow_write("slow.txt", 1)]},
            "fast": {"skip": True, "run": [echo_write("fast.txt", "f")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertTrue((self.workdir / "slow.txt").exists())
        self.assertTrue((self.workdir / "fast.txt").exists())

    def test_initiator_failure_proceed_keeps_linked_running(self):
        path = simple_recipe(self.tmp, steps={
            "boom": {"errskip": "proceed", "linkup": ["slow"], "run": [FAIL_CMD]},
            "slow": {"skip": True, "run": [slow_write("slow.txt", 1)]},
            "after": {"run": [echo_write("after.txt")]},
        })
        runner = run_recipe(path, self.workdir)
        self.assertTrue(runner.failed)  # 失败被 proceed 处理 -> 最终非 0
        self.assertTrue((self.workdir / "slow.txt").exists())  # 联动步骤跑完了
        self.assertTrue((self.workdir / "after.txt").exists())  # 作业继续

    @unittest.skipIf(PY_SPACED, "解释器路径含空格，shell 拼接不适用")
    def test_interactive_reader_in_linkup_aborts_group(self):
        reader = py_command("import sys,time;sys.stdin.readline();time.sleep(30)")
        path = simple_recipe(self.tmp, steps={
            "interactive": {"linkup": ["other"], "run": [reader]},
            "other": {"skip": True, "run": [slow_write("other.txt", 2)]},
        })
        runner = make_runner(self.workdir)
        with self.assertRaises(InteractiveInputError):
            runner.run(path)


class TestDryRun(RunnerTestCase):
    def test_no_side_effects(self):
        path = simple_recipe(self.tmp, steps={
            "s": {"run": [echo_write("x.txt")]},
        })
        runner = run_recipe(path, self.workdir, dry_run=True)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "x.txt").exists())

    def test_material_download_clone_skipped_without_network(self):
        path = write_recipe(self.tmp, {
            "material": {"repo": "https://example.invalid/x.git"},
            "download": ["https://example.invalid/tool.zip"],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("x.txt")]}}}},
        })
        runner = run_recipe(path, self.workdir, dry_run=True)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "workspace").exists())
        self.assertFalse((self.workdir / "tool.zip").exists())
        self.assertFalse((self.workdir / "x.txt").exists())


class TestDownloadUnzipInRecipe(RunnerTestCase):
    def test_filtered_download_skipped_and_local_unzip_runs(self):
        import zipfile

        archive = self.tmp / "assets.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("extracted.txt", "payload")
        path = write_recipe(self.tmp, {
            "download": [{"https://example.com/remote.zip": "linux_arm64"}],
            "unzip": ["assets.zip"],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("done.txt")]}}}},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "remote.zip").exists())  # 系统过滤掉，没触网
        self.assertTrue((self.workdir / "dl" / "extracted.txt").exists())
        self.assertTrue((self.workdir / "done.txt").exists())


class TestSpawnInternals(RunnerTestCase):
    def test_string_command_uses_shell(self):
        runner = make_runner(self.workdir)
        recipe_obj, step_obj = dummy()
        with mock.patch("devkit.runner.subprocess.run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=0)
            rc = runner._spawn("echo hi", cwd=self.workdir, recipe=recipe_obj,
                               step=step_obj, pretty="echo hi")
        self.assertEqual(rc, 0)
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], "echo hi")
        self.assertTrue(kwargs["shell"])  # run 里的命令一律走 shell

    def test_list_command_does_not_use_shell(self):
        runner = make_runner(self.workdir)
        recipe_obj, step_obj = dummy()
        with mock.patch("devkit.runner.subprocess.run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=0)
            runner._spawn(["ls", "-l"], cwd=self.workdir, recipe=recipe_obj,
                          step=step_obj, pretty="ls -l")
        args, kwargs = run_mock.call_args
        self.assertFalse(kwargs["shell"])

    def test_spawn_failure_raises_step_error(self):
        runner = make_runner(self.workdir)
        recipe_obj, step_obj = dummy()
        with mock.patch("devkit.runner.subprocess.run",
                        side_effect=OSError("no such file")):
            with self.assertRaises(StepError):
                runner._spawn("whatever", cwd=self.workdir, recipe=recipe_obj,
                              step=step_obj, pretty="whatever")

    def test_resolve_program(self):
        resolve = RecipeRunner._resolve_program
        absolute = str(Path("C:/x/y.exe")) if _common.IS_WIN else "/usr/bin/y"
        self.assertEqual(resolve(absolute, self.tmp), absolute)
        (self.tmp / "tool.sh").write_text("x", encoding="utf-8")
        self.assertEqual(resolve("tool.sh", self.tmp), str(self.tmp / "tool.sh"))
        self.assertEqual(resolve("not-here.bin", self.tmp), "not-here.bin")


if __name__ == "__main__":
    unittest.main()
