"""devkit.recipe 配方解析与校验测试。"""

from __future__ import annotations

import _common
import json
import tempfile
import unittest
from pathlib import Path

from devkit.errors import RecipeError
from devkit.recipe import (
    NEXT_JOB,
    DownloadSpec,
    EnvEntry,
    Job,
    Material,
    Recipe,
    Step,
    UnpackSpec,
    UnzipSpec,
    bind_call_args,
    load_recipe,
    resolve_recipe_path,
    resolve_step_env,
)
from _common import write_recipe


class TestResolveRecipePath(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_direct_file(self):
        path = write_recipe(self.tmp, {"data": []}, "my.json")
        self.assertEqual(resolve_recipe_path(path), path)
        self.assertEqual(resolve_recipe_path(str(path)), path)

    def test_directory_finds_recipe_json(self):
        write_recipe(self.tmp, {}, "recipe.json")
        self.assertEqual(resolve_recipe_path(self.tmp).name, "recipe.json")

    def test_directory_ignores_other_json_names(self):
        write_recipe(self.tmp, {}, "repice.json")
        with self.assertRaisesRegex(RecipeError, "没有找到"):
            resolve_recipe_path(self.tmp)

    def test_directory_without_recipe(self):
        with self.assertRaisesRegex(RecipeError, "没有找到"):
            resolve_recipe_path(self.tmp)

    def test_missing_file(self):
        with self.assertRaisesRegex(RecipeError, "不存在"):
            resolve_recipe_path(self.tmp / "nope.json")

    def test_path_is_file_not_dir(self):
        # 一个普通文件（非目录、是文件）直接返回
        f = self.tmp / "x.txt"
        f.write_text("hi", encoding="utf-8")
        self.assertEqual(resolve_recipe_path(f), f)


class TestLoadRecipe(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _load(self, raw: dict, name: str = "recipe.json") -> Recipe:
        return load_recipe(write_recipe(self.tmp, raw, name))

    def test_invalid_json(self):
        p = self.tmp / "recipe.json"
        p.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(RecipeError, "合法 JSON"):
            load_recipe(p)

    def test_top_level_must_be_object(self):
        p = self.tmp / "recipe.json"
        p.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaisesRegex(RecipeError, "顶层必须是 JSON 对象"):
            load_recipe(p)

    def test_missing_file(self):
        with self.assertRaises(RecipeError):
            load_recipe(self.tmp / "absent.json")

    def test_empty_recipe_defaults(self):
        recipe = self._load({})
        self.assertIsNone(recipe.material)
        self.assertEqual(recipe.downloads, ())
        self.assertEqual(recipe.unzips, ())
        self.assertEqual(recipe.data, ())
        self.assertEqual(recipe.jobs, ())
        self.assertEqual(recipe.dir, self.tmp)

    def test_unknown_top_key_tolerated(self):
        recipe = self._load({"whatever": 1})
        self.assertIsInstance(recipe, Recipe)

    def test_bom_tolerated(self):
        p = self.tmp / "recipe.json"
        p.write_text(json.dumps({"data": ["x"]}), encoding="utf-8-sig")
        self.assertEqual(load_recipe(p).data, ("x",))


class TestMaterial(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _material(self, raw):
        return load_recipe(write_recipe(self.tmp, {"material": raw})).material

    def test_full(self):
        m = self._material({"repo": "https://x/y.git", "branch": "dev", "depth": 1,
                            "commit": "abc"})
        self.assertEqual(m, Material("https://x/y.git", "dev", 1, "abc"))

    def test_defaults(self):
        m = self._material({"repo": " https://x/y.git "})
        self.assertEqual(m.repo, "https://x/y.git")
        self.assertIsNone(m.branch)
        self.assertIsNone(m.depth)
        self.assertIsNone(m.commit)

    def test_depth_zero_becomes_none(self):
        self.assertIsNone(self._material({"repo": "r", "depth": 0}).depth)

    def test_repo_required(self):
        for raw in ({}, {"repo": ""}, {"repo": "  "}, {"repo": 42}):
            with self.assertRaises(RecipeError):
                self._material(raw)

    def test_bad_branch_type(self):
        with self.assertRaisesRegex(RecipeError, "branch"):
            self._material({"repo": "r", "branch": 5})

    def test_empty_branch_string_is_none(self):
        self.assertIsNone(self._material({"repo": "r", "branch": ""}).branch)

    def test_bad_depth(self):
        for depth in (-1, "2", True, 1.5):
            with self.assertRaises(RecipeError):
                self._material({"repo": "r", "depth": depth})

    def test_material_must_be_object(self):
        p = write_recipe(self.tmp, {"material": "https://x/y.git"})
        with self.assertRaisesRegex(RecipeError, "必须是对象"):
            load_recipe(p)


class TestDownloadUnzip(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_string_form_no_filter(self):
        recipe = load_recipe(write_recipe(self.tmp, {"download": ["https://a/b.zip"]}))
        self.assertEqual(recipe.downloads, (DownloadSpec("https://a/b.zip", None),))

    def test_object_form_string_system(self):
        recipe = load_recipe(write_recipe(
            self.tmp, {"download": [{"url": "https://a/b.zip", "system": "win_amd64"}]}))
        self.assertEqual(recipe.downloads[0].systems, ("win_amd64",))

    def test_object_form_list_systems(self):
        recipe = load_recipe(write_recipe(
            self.tmp, {"download": [{"url": "https://a/b.zip", "system": ["linux_amd64", "macos_arm64"]}]}))
        self.assertEqual(recipe.downloads[0].systems, ("linux_amd64", "macos_arm64"))

    def test_empty_string_system_means_always(self):
        recipe = load_recipe(write_recipe(
            self.tmp, {"download": [{"url": "https://a/b.zip", "system": ""}]}))
        self.assertIsNone(recipe.downloads[0].systems)

    def test_empty_list_system_means_always(self):
        recipe = load_recipe(write_recipe(
            self.tmp, {"download": [{"url": "https://a/b.zip", "system": []}]}))
        self.assertIsNone(recipe.downloads[0].systems)

    def test_download_rejects_extract_and_old_key(self):
        for raw in (
            {"download": [{"https://a/b.zip": "win_amd64"}]},   # 旧式 键值对
            {"download": [{"url": "https://a/b.zip", "extract": True}]},  # download 无 extract
            {"download": [{"url": "https://a/b.zip", "x": 1}]},  # 未知字段
        ):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                load_recipe(write_recipe(self.tmp, raw))

    def test_unzip_parsing(self):
        recipe = load_recipe(write_recipe(self.tmp, {"unzip": [
            "a.zip",
            {"file": "b.zip", "system": "win_amd64"},
            {"file": "c.zip", "extract": True},
        ]}))
        self.assertEqual(recipe.unzips, (
            UnzipSpec("a.zip"),
            UnzipSpec("b.zip", ("win_amd64",)),
            UnzipSpec("c.zip", None, True),
        ))

    def test_unpack_parsing(self):
        recipe = load_recipe(write_recipe(self.tmp, {"unpack": [
            "https://a/b.zip",
            {"url": "https://a/c.zip", "system": ["win_amd64", "linux_amd64"], "extract": True},
        ]}))
        self.assertEqual(recipe.unpacks, (
            UnpackSpec("https://a/b.zip"),
            UnpackSpec("https://a/c.zip", ("win_amd64", "linux_amd64"), True),
        ))

    def test_unzip_rejects_old_key_and_unknown_fields(self):
        for raw in (
            {"unzip": [{"b.zip": "win_amd64"}]},        # 旧式 键值对
            {"unzip": [{"path": "b.zip"}]},             # 错字段名
            {"unzip": [{"file": "b.zip", "sys": "x"}]}, # 未知字段
            {"unzip": [{"system": "win_amd64"}]},       # 缺 file
        ):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                load_recipe(write_recipe(self.tmp, raw))

    def test_unpack_rejects_old_key_and_unknown_fields(self):
        for raw in (
            {"unpack": [{"https://a/b.zip": "win_amd64"}]},
            {"unpack": [{"file": "b.zip"}]},            # 用了 file 而非 url
            {"unpack": [{"url": "https://a/b.zip", "x": 1}]},
        ):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                load_recipe(write_recipe(self.tmp, raw))

    def test_bad_items(self):
        for raw in (
            {"download": "https://a/b.zip"},               # 非列表
            {"download": [""]},                            # 空链接
            {"download": [{}]},                            # 空对象
            {"download": [123]},                           # 非字符串
            {"download": [{"url": ""}]},                   # 空 url
            {"download": [{"url": "https://a/b.zip", "system": 5}]},  # 系统过滤类型错误
        ):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                load_recipe(write_recipe(self.tmp, raw))

    def test_matches(self):
        spec = DownloadSpec("u", ("win_amd64",))
        self.assertTrue(spec.matches("win_amd64"))
        self.assertFalse(spec.matches("linux_amd64"))
        self.assertTrue(DownloadSpec("u", None).matches("anything"))
        self.assertTrue(UnzipSpec("z", None).matches("anything"))
        self.assertFalse(UnzipSpec("z", ("linux_amd64",)).matches("win_amd64"))
        self.assertTrue(UnpackSpec("u", None).matches("anything"))
        self.assertFalse(UnpackSpec("u", ("linux_amd64",)).matches("win_amd64"))


class TestRunParsing(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _steps(self, steps: dict) -> dict[str, Step]:
        recipe = load_recipe(write_recipe(self.tmp, {"jobs": {"j": {"steps": steps}}}))
        return {s.name: s for s in recipe.jobs[0].steps}

    def test_double_list_required(self):
        steps = self._steps({"s": {"run": [["echo", "a"], ["echo", "b"]]}})
        self.assertEqual(steps["s"].run, (("echo", "a"), ("echo", "b")))

    def test_single_command_still_double_list(self):
        with self.assertRaisesRegex(RecipeError, "双层列表"):
            self._steps({"s": {"run": ["echo a"]}})

    def test_empty_command_rejected(self):
        with self.assertRaisesRegex(RecipeError, "不能为空"):
            self._steps({"s": {"run": [[]]}})

    def test_empty_fragment_rejected(self):
        with self.assertRaisesRegex(RecipeError, "片段不能为空"):
            self._steps({"s": {"run": [["echo", ""]]}})

    def test_bad_fragment_type(self):
        with self.assertRaisesRegex(RecipeError, "字符串或数字"):
            self._steps({"s": {"run": [["echo", ["x"]]]}})

    def test_run_null_is_empty(self):
        steps = self._steps({"s": {}})
        self.assertEqual(steps["s"].run, ())

    def test_numeric_fragments_kept(self):
        steps = self._steps({"s": {"run": [["tool", 0, 1, 2.5]]}})
        self.assertEqual(steps["s"].run, (("tool", 0, 1, 2.5),))


class TestStepControlFields(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _step(self, body: dict, extra: dict | None = None) -> Step:
        raw = {"jobs": {"j": {"steps": {"s": body}}}}
        if extra:
            raw["jobs"]["j"]["steps"].update(extra)
        recipe = load_recipe(write_recipe(self.tmp, raw))
        return recipe.jobs[0].steps[0]

    def test_defaults(self):
        s = self._step({"run": [["x"]]})
        self.assertFalse(s.skip)
        self.assertEqual(s.errskip, "stop")
        self.assertEqual(s.linkup, ())
        self.assertIsNone(s.jump)
        self.assertIsNone(s.next)
        self.assertIsNone(s.when.error)
        self.assertIsNone(s.when.success)
        self.assertEqual(s.when.jump, ())
        self.assertEqual(s.when.redirect, ())

    def test_all_fields(self):
        s = self._step({
            "run": [["x"]], "skip": True, "errskip": "proceed",
            "linkup": ["t"], "jump": "u", "next": "job-b",
            "if": {"error": "e", "success": "ok",
                   "jump": {"a": "b"}, "redirect": {"c": "d"}},
        }, extra={"t": {"run": [["y"]]}})
        self.assertTrue(s.skip)
        self.assertEqual(s.errskip, "proceed")
        self.assertEqual(s.linkup, ("t",))
        self.assertEqual(s.jump, "u")
        self.assertEqual(s.next, "job-b")
        self.assertEqual(s.when.error, "e")
        self.assertEqual(s.when.success, "ok")
        self.assertEqual(s.when.jump, (("a", "b"),))
        self.assertEqual(s.when.redirect, (("c", "d"),))

    def test_next_null_means_next_job(self):
        s = self._step({"run": [["x"]], "next": None})
        self.assertEqual(s.next, NEXT_JOB)

    def test_bad_errskip(self):
        with self.assertRaisesRegex(RecipeError, "crash/stop/proceed/skip"):
            self._step({"errskip": "boom"})

    def test_bad_skip(self):
        with self.assertRaisesRegex(RecipeError, "true/false"):
            self._step({"skip": "yes"})

    def test_linkup_self_rejected(self):
        with self.assertRaisesRegex(RecipeError, "不能联动自己"):
            self._step({"linkup": ["s"]})

    def test_linkup_duplicate_rejected(self):
        with self.assertRaisesRegex(RecipeError, "重复"):
            self._step({"linkup": ["t", "t"]}, extra={"t": {}})

    def test_linkup_must_be_names(self):
        with self.assertRaisesRegex(RecipeError, "步骤名列表"):
            self._step({"linkup": "t"})

    def test_jump_and_redirect_clash(self):
        with self.assertRaisesRegex(RecipeError, "跳转冲突"):
            self._step({"if": {"jump": {"a": "b"}, "redirect": {"a": "c"}}})

    def test_jump_map_must_be_strings(self):
        with self.assertRaises(RecipeError):
            self._step({"if": {"jump": {"a": 1}}})

    def test_bad_next(self):
        with self.assertRaises(RecipeError):
            self._step({"next": 5})


class TestCallParsing(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _call(self, raw):
        recipe = load_recipe(write_recipe(
            self.tmp, {"jobs": {"j": {"steps": {"s": {"call": raw}}}}}))
        return recipe.jobs[0].steps[0].call

    def test_args_list(self):
        self.assertEqual(self._call({"sub.json": ["a", 1]}), (("sub.json", ("a", 1)),))

    def test_args_null(self):
        self.assertEqual(self._call({"sub.json": None}), (("sub.json", ()),))

    def test_args_scalar_wrapped(self):
        self.assertEqual(self._call({"sub.json": "only"}), (("sub.json", ("only",)),))
        self.assertEqual(self._call({"sub.json": 5}), (("sub.json", (5,)),))

    def test_bad_call(self):
        for raw in ([], {"": []}, "sub.json", ["sub.json"]):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                self._call(raw)


class TestJobsParsing(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _jobs(self, raw: dict) -> tuple[Job, ...]:
        return load_recipe(write_recipe(self.tmp, {"jobs": raw})).jobs

    def test_defaults(self):
        (job,) = self._jobs({"a": {}})
        self.assertEqual(job.name, "a")
        self.assertIsNone(job.system)
        self.assertFalse(job.skip)
        self.assertEqual(job.linkup, ())
        self.assertEqual(job.errskip, "proceed")
        self.assertEqual(job.steps, ())

    def test_system_and_matches(self):
        (job,) = self._jobs({"a": {"system": "win_amd64"}})
        self.assertTrue(job.matches("win_amd64"))
        self.assertFalse(job.matches("linux_amd64"))
        (anyjob,) = self._jobs({"b": {}})
        self.assertTrue(anyjob.matches("whatever"))

    def test_bad_system_type(self):
        with self.assertRaisesRegex(RecipeError, "system"):
            self._jobs({"a": {"system": 5}})

    def test_linkup_string_and_list(self):
        jobs = self._jobs({"a": {"linkup": "b"}, "b": {}})
        self.assertEqual(jobs[0].linkup, ("b",))
        jobs2 = self._jobs({"a": {"linkup": ["b"]}, "b": {}})
        self.assertEqual(jobs2[0].linkup, ("b",))

    def test_linkup_missing_target(self):
        with self.assertRaisesRegex(RecipeError, "不存在"):
            self._jobs({"a": {"linkup": "ghost"}})

    def test_linkup_self(self):
        with self.assertRaisesRegex(RecipeError, "不能联动自己"):
            self._jobs({"a": {"linkup": "a"}})

    def test_bad_errskip(self):
        with self.assertRaisesRegex(RecipeError, "crash/proceed/skip"):
            self._jobs({"a": {"errskip": "stop"}})

    def test_bad_jobs_type(self):
        with self.assertRaisesRegex(RecipeError, "必须是对象"):
            load_recipe(write_recipe(self.tmp, {"jobs": []}))


class TestBindCallArgs(unittest.TestCase):
    def _recipe(self, data) -> Recipe:
        with tempfile.TemporaryDirectory() as td:
            return load_recipe(write_recipe(Path(td), {"data": data}))

    def test_top_level_returns_data(self):
        recipe = self._recipe(["hi", 0, 5])
        self.assertEqual(bind_call_args(recipe, None), ["hi", 0, 5])

    def test_placeholders_bound(self):
        recipe = self._recipe(["hi", 0, "x", 1])
        self.assertEqual(bind_call_args(recipe, ["A", "B"]), ["hi", "A", "x", "B"])

    def test_bool_is_not_placeholder(self):
        recipe = self._recipe([True, False])
        self.assertEqual(bind_call_args(recipe, ["A"]), [True, False])

    def test_out_of_range(self):
        recipe = self._recipe([0, 1])
        with self.assertRaisesRegex(RecipeError, "超出"):
            bind_call_args(recipe, ["only-one"])

    def test_no_args_needed(self):
        recipe = self._recipe(["plain"])
        self.assertEqual(bind_call_args(recipe, []), ["plain"])


class TestEnvBlock(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _load(self, raw: dict) -> Recipe:
        return load_recipe(write_recipe(self.tmp, raw))

    def test_global_env_parsing(self):
        recipe = self._load({"env": [
            {"key": "hello", "value": "world"},
            {"key": "world", "value": "hello;world", "system": "win_arm64"},
            {"key": "empty", "value": ""},
        ]})
        self.assertEqual(recipe.env, (
            EnvEntry("hello", "world"),
            EnvEntry("world", "hello;world", ("win_arm64",)),
            EnvEntry("empty", ""),
        ))

    def test_absent_env_is_empty_tuple(self):
        recipe = self._load({})
        self.assertEqual(recipe.env, ())
        self.assertIsNone(recipe.jobs[0].area if recipe.jobs else None)

    def test_job_and_step_env_and_area(self):
        recipe = self._load({"jobs": {"j": {
            "env": [{"key": "CC", "value": "clang"}],
            "area": [{"key": "ONLY", "value": "1"}],
            "steps": {"s": {
                "env": [{"key": "CC", "value": "gcc"}],
                "area": [{"key": "ISO", "value": "2"}],
            }},
        }}})
        job = recipe.jobs[0]
        self.assertEqual(job.env, (EnvEntry("CC", "clang"),))
        self.assertEqual(job.area, (EnvEntry("ONLY", "1"),))
        step = job.steps[0]
        self.assertEqual(step.env, (EnvEntry("CC", "gcc"),))
        self.assertEqual(step.area, (EnvEntry("ISO", "2"),))

    def test_bad_env_blocks(self):
        for raw in (
            {"env": "hello"},                                  # 非列表
            {"env": [{"key": "hi"}]},                           # 缺 value
            {"env": [{"value": "x"}]},                          # 缺 key
            {"env": [{"key": "", "value": "x"}]},               # 空 key
            {"env": [{"key": "k", "value": 5}]},                # value 非字符串
            {"env": [{"key": "k", "value": "v", "x": 1}]},      # 未知字段
            {"env": [{"key": "k", "value": "v", "system": 5}]}, # system 类型错误
            {"env": ["hi"]},                                    # 项非对象
        ):
            with self.subTest(raw=raw), self.assertRaises(RecipeError):
                self._load(raw)

    def test_area_absent_is_none(self):
        recipe = self._load({"jobs": {"j": {"steps": {"s": {}}}}})
        self.assertIsNone(recipe.jobs[0].area)
        self.assertIsNone(recipe.jobs[0].steps[0].area)


class TestResolveStepEnv(unittest.TestCase):
    def _recipe(self, raw: dict) -> Recipe:
        with tempfile.TemporaryDirectory() as td:
            return load_recipe(write_recipe(Path(td), raw))

    def _one(self, raw: dict, system: str = "win_amd64") -> dict[str, str]:
        raw.setdefault("jobs", {"j": {"steps": {"s": {}}}})
        recipe = self._recipe(raw)
        job = recipe.jobs[0]
        step = job.steps[0]
        return resolve_step_env(recipe, job, step, system)

    def test_global_only(self):
        env = self._one({"env": [{"key": "A", "value": "1"}], "jobs": {"j": {"steps": {"s": {}}}}})
        self.assertEqual(env, {"A": "1"})

    def test_job_overrides_and_merges(self):
        env = self._one({
            "env": [{"key": "A", "value": "1"}, {"key": "B", "value": "2"}],
            "jobs": {"j": {"env": [{"key": "B", "value": "22"}, {"key": "C", "value": "3"}],
                           "steps": {"s": {}}}},
        })
        self.assertEqual(env, {"A": "1", "B": "22", "C": "3"})

    def test_step_overrides_and_merges(self):
        env = self._one({
            "env": [{"key": "A", "value": "1"}],
            "jobs": {"j": {"env": [{"key": "A", "value": "11"}, {"key": "B", "value": "2"}],
                           "steps": {"s": {"env": [{"key": "B", "value": "22"},
                                                   {"key": "C", "value": "3"}]}}}},
        })
        self.assertEqual(env, {"A": "11", "B": "22", "C": "3"})

    def test_system_filter(self):
        raw = {"env": [{"key": "A", "value": "1", "system": "linux_amd64"},
                       {"key": "B", "value": "2"}]}
        self.assertEqual(self._one(raw, "win_amd64"), {"B": "2"})
        self.assertEqual(self._one(raw, "linux_amd64"), {"A": "1", "B": "2"})

    def test_later_duplicate_key_wins(self):
        env = self._one({"env": [{"key": "A", "value": "1"}, {"key": "A", "value": "2"}]})
        self.assertEqual(env, {"A": "2"})

    def test_job_area_ignores_global_env(self):
        env = self._one({
            "env": [{"key": "A", "value": "1"}],
            "jobs": {"j": {"area": [{"key": "ONLY", "value": "x"}], "steps": {"s": {}}}},
        })
        self.assertEqual(env, {"ONLY": "x"})

    def test_lower_step_env_modifies_inherited_job_area(self):
        env = self._one({
            "env": [{"key": "A", "value": "1"}],
            "jobs": {"j": {"area": [{"key": "ONLY", "value": "x"}],
                           "steps": {"s": {"env": [{"key": "ONLY", "value": "y"},
                                                   {"key": "NEW", "value": "z"}]}}}},
        })
        self.assertEqual(env, {"ONLY": "y", "NEW": "z"})

    def test_step_area_ignores_job_area(self):
        env = self._one({
            "jobs": {"j": {"area": [{"key": "JOB", "value": "1"}],
                           "steps": {"s": {"area": [{"key": "STEP", "value": "2"}]}}}},
        })
        self.assertEqual(env, {"STEP": "2"})

    def test_step_area_ignores_job_env(self):
        env = self._one({
            "jobs": {"j": {"env": [{"key": "JOB", "value": "1"}],
                           "steps": {"s": {"area": [{"key": "STEP", "value": "2"}]}}}},
        })
        self.assertEqual(env, {"STEP": "2"})

    def test_area_wins_over_same_block_env(self):
        env = self._one({
            "jobs": {"j": {"env": [{"key": "E", "value": "1"}],
                           "area": [{"key": "A", "value": "2"}],
                           "steps": {"s": {}}}},
        })
        self.assertEqual(env, {"A": "2"})

    def test_step_area_wins_over_step_env(self):
        env = self._one({
            "jobs": {"j": {"steps": {"s": {
                "env": [{"key": "E", "value": "1"}],
                "area": [{"key": "A", "value": "2"}],
            }}}},
        })
        self.assertEqual(env, {"A": "2"})

    def test_empty_area_is_empty_env(self):
        env = self._one({
            "env": [{"key": "A", "value": "1"}],
            "jobs": {"j": {"area": [], "steps": {"s": {}}}},
        })
        self.assertEqual(env, {})

    def test_global_area_ignores_inherited(self):
        raw = {
            "area": [{"key": "ONLY", "value": "x"}],
            "jobs": {"j": {"steps": {"s": {}}}},
        }
        recipe = self._recipe(raw)
        job, step = recipe.jobs[0], recipe.jobs[0].steps[0]
        env = resolve_step_env(recipe, job, step, "win_amd64", inherited={"FROM": "parent"})
        self.assertEqual(env, {"ONLY": "x"})

    def test_empty_global_area_still_resets_inherited(self):
        raw = {"area": [], "jobs": {"j": {"steps": {"s": {}}}}}
        recipe = self._recipe(raw)
        job, step = recipe.jobs[0], recipe.jobs[0].steps[0]
        env = resolve_step_env(recipe, job, step, "win_amd64", inherited={"FROM": "parent"})
        self.assertEqual(env, {})

    def test_inherited_env_merges_with_recipe_env(self):
        raw = {"env": [{"key": "B", "value": "2"}], "jobs": {"j": {"steps": {"s": {}}}}}
        recipe = self._recipe(raw)
        job, step = recipe.jobs[0], recipe.jobs[0].steps[0]
        env = resolve_step_env(recipe, job, step, "win_amd64",
                               inherited={"A": "1", "B": "old"})
        self.assertEqual(env, {"A": "1", "B": "2"})

    def test_lower_env_modifies_inherited_area(self):
        raw = {"jobs": {"j": {"steps": {"s": {}}}}}
        recipe = self._recipe(raw)
        job, step = recipe.jobs[0], recipe.jobs[0].steps[0]
        env = resolve_step_env(recipe, job, step, "win_amd64",
                               inherited={"AREA_KEY": "from-area"})
        self.assertEqual(env, {"AREA_KEY": "from-area"})


if __name__ == "__main__":
    unittest.main()
