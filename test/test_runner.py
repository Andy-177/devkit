"""devkit.runner 执行引擎的端到端/行为测试（真实 shell、临时目录）。"""

from __future__ import annotations

import _common
import os
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

    def dump_env(self, name: str, var: str) -> list[str]:
        """一条命令: 把单个环境变量写进 name 文件。"""
        code = f"import os;open('{name}','w').write(os.environ.get('{var}',''))"
        return py_command(code)

    def dump_vars(self, name: str, variables: list[str], *, sleep: int = 0) -> list[str]:
        """一条命令: 把一组环境变量以 ``KEY=VALUE`` 写进 name，可选先睡 sleep 秒。"""
        keys = ",".join(f"'{v}'" for v in variables)
        sleep_stmt = f"time.sleep({sleep});" if sleep else ""
        mods = "os,time" if sleep else "os"
        code = (
            f"import {mods};{sleep_stmt}open('{name}','w').write("
            f"'\\n'.join(k+'='+os.environ.get(k,'') for k in [{keys}]))"
        )
        return py_command(code)

    def read_vars(self, name: str) -> dict[str, str]:
        """读回 ``KEY=VALUE`` 文件为字典。"""
        result: dict[str, str] = {}
        for line in _common.read(self.workdir / name).splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                result[key] = value
        return result


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
            "download": [{"url": "https://example.com/remote.zip", "system": "linux_arm64"}],
            "unzip": ["assets.zip"],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("done.txt")]}}}},
        })
        runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        self.assertFalse((self.workdir / "remote.zip").exists())  # 系统过滤掉，没触网
        # 缺省 extract=false: 解压到与压缩包同名的子目录
        self.assertTrue((self.workdir / "dl" / "assets" / "extracted.txt").exists())
        self.assertTrue((self.workdir / "done.txt").exists())

    def test_unzip_extract_true_flattens(self):
        import zipfile

        archive = self.tmp / "assets.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("flat.txt", "payload")
        path = write_recipe(self.tmp, {
            "unzip": [{"file": "assets.zip", "extract": True}],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("done.txt")]}}}},
        })
        run_recipe(path, self.workdir)
        self.assertTrue((self.workdir / "dl" / "flat.txt").exists())

    def test_unpack_downloads_then_extracts(self):
        import zipfile

        source = self.tmp / "bundle.zip"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("inner.txt", "payload")

        def fake_download(spec, dest, system, *, dry_run=False):
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / "bundle.zip"
            target.write_bytes(source.read_bytes())
            return target

        path = write_recipe(self.tmp, {
            "unpack": [{"url": "https://example.com/bundle.zip", "extract": True}],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("done.txt")]}}}},
        })
        with mock.patch("devkit.unpack.download_one", side_effect=fake_download):
            runner = run_recipe(path, self.workdir)
        self.assertFalse(runner.failed)
        dl = self.workdir / "dl"
        self.assertTrue((dl / ".dkcache" / "bundle.zip").exists())  # 缓存
        self.assertTrue((dl / "inner.txt").exists())               # extract=true 直接铺开

    def test_unpack_default_nests_into_same_named_subdir(self):
        import zipfile

        source = self.tmp / "bundle.zip"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("inner.txt", "payload")

        def fake_download(spec, dest, system, *, dry_run=False):
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / "bundle.zip"
            target.write_bytes(source.read_bytes())
            return target

        path = write_recipe(self.tmp, {
            "unpack": ["https://example.com/bundle.zip"],
            "jobs": {"main": {"steps": {"s": {"run": [echo_write("done.txt")]}}}},
        })
        with mock.patch("devkit.unpack.download_one", side_effect=fake_download):
            run_recipe(path, self.workdir)
        dl = self.workdir / "dl"
        self.assertTrue((dl / "bundle" / "inner.txt").exists())


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


class TestEnvExecution(RunnerTestCase):
    def _dump_env(self, name: str, var: str) -> list[str]:
        code = f"import os;open('{name}','w').write(os.environ.get('{var}',''))"
        return py_command(code)

    def test_global_env_reaches_subprocess(self):
        raw = {
            "env": [{"key": "DK_GLOBAL", "value": "g-value"}],
            "jobs": {"main": {"steps": {"s": {
                "run": [self._dump_env("out.txt", "DK_GLOBAL")]}}}},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "g-value")

    def test_step_env_overrides_job_and_global(self):
        raw = {
            "env": [{"key": "DK_X", "value": "global"}],
            "jobs": {"main": {
                "env": [{"key": "DK_X", "value": "job"}],
                "steps": {"s": {
                    "run": [self._dump_env("out.txt", "DK_X")],
                    "env": [{"key": "DK_X", "value": "step"}],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "step")

    def test_job_env_overrides_global(self):
        raw = {
            "env": [{"key": "DK_X", "value": "global"}],
            "jobs": {"main": {
                "env": [{"key": "DK_X", "value": "job"}],
                "steps": {"s": {"run": [self._dump_env("out.txt", "DK_X")]}},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "job")

    def test_job_area_ignores_global_env(self):
        raw = {
            "env": [{"key": "DK_X", "value": "global"}],
            "jobs": {"main": {
                "area": [{"key": "DK_Y", "value": "area"}],
                "steps": {"s": {"run": [
                    self._dump_env("x.txt", "DK_X"),
                    self._dump_env("y.txt", "DK_Y"),
                ]}},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "x.txt"), "")
        self.assertEqual(_common.read(self.workdir / "y.txt"), "area")

    def test_existing_process_env_kept(self):
        sentinel = "DK_PARENT_SENTINEL"
        raw = {
            "env": [{"key": "DK_NEW", "value": "v"}],
            "jobs": {"main": {"steps": {"s": {
                "run": [self._dump_env("out.txt", sentinel)]}}}},
        }
        with mock.patch.dict(os.environ, {sentinel: "kept"}, clear=False):
            run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "kept")

    def test_child_recipe_inherits_parent_step_env(self):
        write_recipe(
            self.tmp,
            {"jobs": {"main": {"steps": {"child": {
                "run": [self._dump_env("child.txt", "DK_FROM_PARENT")]}}}}},
            name="child.json",
        )
        raw = {
            "env": [{"key": "DK_FROM_PARENT", "value": "inherited"}],
            "jobs": {"main": {"steps": {"parent": {
                "call": {"child.json": []}}}}},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "child.txt"), "inherited")

    def test_child_global_area_rejects_inherited_env(self):
        write_recipe(
            self.tmp,
            {"area": [{"key": "DK_CHILD", "value": "own"}],
             "jobs": {"main": {"steps": {"child": {"run": [
                 self._dump_env("inherited.txt", "DK_FROM_PARENT"),
                 self._dump_env("own.txt", "DK_CHILD"),
             ]}}}}},
            name="child.json",
        )
        raw = {
            "env": [{"key": "DK_FROM_PARENT", "value": "inherited"}],
            "jobs": {"main": {"steps": {"parent": {
                "call": {"child.json": []}}}}},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "inherited.txt"), "")
        self.assertEqual(_common.read(self.workdir / "own.txt"), "own")

    def test_step_area_ignores_job_area_in_subprocess(self):
        raw = {
            "jobs": {"main": {
                "area": [{"key": "DK_LAYER", "value": "job"}],
                "steps": {"s": {
                    "run": [self._dump_env("out.txt", "DK_LAYER")],
                    "area": [{"key": "DK_LAYER", "value": "step"}],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "step")

    def test_system_filtered_env_missing_in_subprocess(self):
        raw = {
            "env": [{"key": "DK_OTHER", "value": "v", "system": "never-matches"}],
            "jobs": {"main": {"steps": {"s": {
                "run": [self._dump_env("out.txt", "DK_OTHER")]}}}},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertEqual(_common.read(self.workdir / "out.txt"), "")


class TestComplexEnv(RunnerTestCase):
    """复杂分层环境: 全局/作业/步骤 env 与 area 混用，含 system 过滤。"""

    VARIABLES = ["DK_GLOBAL", "DK_JOB", "DK_STEP", "DK_AREA", "DK_KEEP", "DK_OVERRIDE"]

    def test_triple_layer_merge_and_override(self):
        raw = {
            "env": [
                {"key": "DK_GLOBAL", "value": "g"},
                {"key": "DK_OVERRIDE", "value": "from-global"},
                {"key": "DK_KEEP", "value": "global-only"},
            ],
            "jobs": {"main": {
                "env": [
                    {"key": "DK_JOB", "value": "j"},
                    {"key": "DK_OVERRIDE", "value": "from-job"},
                ],
                "steps": {"s": {
                    "env": [
                        {"key": "DK_STEP", "value": "s"},
                        {"key": "DK_OVERRIDE", "value": "from-step"},
                    ],
                    "run": [self.dump_vars("out.txt", self.VARIABLES)],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        env = self.read_vars("out.txt")
        self.assertEqual(env["DK_GLOBAL"], "g")
        self.assertEqual(env["DK_JOB"], "j")
        self.assertEqual(env["DK_STEP"], "s")
        self.assertEqual(env["DK_KEEP"], "global-only")
        self.assertEqual(env["DK_OVERRIDE"], "from-step")
        self.assertEqual(env["DK_AREA"], "")

    def test_job_area_drops_global_but_step_env_modifies_it(self):
        raw = {
            "env": [{"key": "DK_GLOBAL", "value": "g"}],
            "jobs": {"main": {
                "area": [{"key": "DK_AREA", "value": "job-area"}],
                "steps": {"s": {
                    "env": [{"key": "DK_AREA", "value": "step-patch"},
                            {"key": "DK_STEP", "value": "s"}],
                    "run": [self.dump_vars("out.txt", self.VARIABLES)],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        env = self.read_vars("out.txt")
        self.assertEqual(env["DK_GLOBAL"], "")           # 作业 area 抹掉全局 env
        self.assertEqual(env["DK_AREA"], "step-patch")   # 下层 env 修改继承下来的 area
        self.assertEqual(env["DK_STEP"], "s")

    def test_step_area_resets_all_upper_layers(self):
        raw = {
            "env": [{"key": "DK_GLOBAL", "value": "g"}, {"key": "DK_KEEP", "value": "k"}],
            "jobs": {"main": {
                "env": [{"key": "DK_JOB", "value": "j"}],
                "steps": {"s": {
                    "area": [{"key": "DK_AREA", "value": "step-area"}],
                    "run": [self.dump_vars("out.txt", self.VARIABLES)],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        env = self.read_vars("out.txt")
        self.assertEqual(env["DK_AREA"], "step-area")
        for key in ("DK_GLOBAL", "DK_JOB", "DK_KEEP", "DK_STEP", "DK_OVERRIDE"):
            self.assertEqual(env[key], "", f"{key} 应被 step.area 抹掉")

    def test_system_filtered_layers(self):
        raw = {
            "env": [
                {"key": "DK_GLOBAL", "value": "g"},
                {"key": "DK_KEEP", "value": "other", "system": "linux_amd64"},
            ],
            "jobs": {"main": {
                "env": [{"key": "DK_JOB", "value": "j", "system": TEST_SYSTEM}],
                "steps": {"s": {
                    "run": [self.dump_vars("out.txt", self.VARIABLES)],
                }},
            }},
        }
        run_recipe(write_recipe(self.tmp, raw), self.workdir)
        env = self.read_vars("out.txt")
        self.assertEqual(env["DK_GLOBAL"], "g")
        self.assertEqual(env["DK_JOB"], "j")
        self.assertEqual(env["DK_KEEP"], "")  # 只匹配 linux，不应出现


class TestEnvUnderLinkup(RunnerTestCase):
    """联动（并发）下各成员的环境互不串扰。"""

    def test_linked_steps_keep_isolated_env(self):
        # 发起者先睡后 dump，被联动者与它并发；两者读到的分层环境应各归各。
        raw = {
            "env": [{"key": "DK_SHARED", "value": "global"},
                    {"key": "DK_GLOBAL_ONLY", "value": "g"}],
            "jobs": {"main": {"steps": {
                "initiator": {
                    "linkup": ["linked"],
                    "run": [self.dump_vars("init.txt", ["DK_SHARED", "DK_GLOBAL_ONLY", "DK_ONLY"], sleep=1)],
                    "env": [{"key": "DK_SHARED", "value": "initiator"},
                            {"key": "DK_ONLY", "value": "initiator-only"}],
                },
                "linked": {
                    "skip": True,
                    "run": [self.dump_vars("linked.txt", ["DK_SHARED", "DK_GLOBAL_ONLY", "DK_ONLY"], sleep=1)],
                    "env": [{"key": "DK_SHARED", "value": "linked"}],
                },
            }}},
        }
        runner = run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertFalse(runner.failed)
        init = self.read_vars(self.workdir / "init.txt")
        link = self.read_vars(self.workdir / "linked.txt")
        self.assertEqual(init["DK_SHARED"], "initiator")
        self.assertEqual(init["DK_GLOBAL_ONLY"], "g")
        self.assertEqual(init["DK_ONLY"], "initiator-only")
        self.assertEqual(link["DK_SHARED"], "linked")   # 不被发起者覆盖
        self.assertEqual(link["DK_GLOBAL_ONLY"], "g")
        self.assertEqual(link["DK_ONLY"], "")           # 拿不到发起者的私有 env

    def test_linked_jobs_keep_isolated_env(self):
        raw = {
            "env": [{"key": "DK_SHARED", "value": "global"}],
            "jobs": {
                "a": {
                    "linkup": "b",
                    "env": [{"key": "DK_SHARED", "value": "job-a"},
                            {"key": "DK_ONLY", "value": "a-only"}],
                    "steps": {"s": {"run": [
                        self.dump_vars("a.txt", ["DK_SHARED", "DK_ONLY"], sleep=1)]}},
                },
                "b": {
                    "skip": True,
                    "env": [{"key": "DK_SHARED", "value": "job-b"}],
                    "steps": {"s": {"run": [
                        self.dump_vars("b.txt", ["DK_SHARED", "DK_ONLY"], sleep=1)]}},
                },
            },
        }
        runner = run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertFalse(runner.failed)
        a = self.read_vars(self.workdir / "a.txt")
        b = self.read_vars(self.workdir / "b.txt")
        self.assertEqual(a["DK_SHARED"], "job-a")
        self.assertEqual(a["DK_ONLY"], "a-only")
        self.assertEqual(b["DK_SHARED"], "job-b")
        self.assertEqual(b["DK_ONLY"], "")

    def test_linked_step_inherits_env_into_called_child_recipe(self):
        write_recipe(
            self.tmp,
            {"jobs": {"main": {"steps": {"child": {
                "run": [self.dump_vars("child.txt", ["DK_PARENT"])]}}}}},
            name="child.json",
        )
        raw = {
            "env": [{"key": "DK_PARENT", "value": "from-global"}],
            "jobs": {"main": {"steps": {
                "initiator": {
                    "linkup": ["other"],
                    "env": [{"key": "DK_PARENT", "value": "from-initiator"}],
                    "run": [echo_write("i.txt")],
                    "call": {"child.json": []},
                },
                "other": {
                    "skip": True,
                    "run": [slow_write("other.txt", 1)],
                },
            }}},
        }
        runner = run_recipe(write_recipe(self.tmp, raw), self.workdir)
        self.assertFalse(runner.failed)
        child = self.read_vars(self.workdir / "child.txt")
        self.assertEqual(child["DK_PARENT"], "from-initiator")


if __name__ == "__main__":
    unittest.main()
