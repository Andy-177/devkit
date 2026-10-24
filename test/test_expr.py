"""表达式系统 (?!{{...}}) 的单元与端到端测试。

覆盖: 替换语义/类型保持、builtins、上下文函数、命名空间并发隔离、
expression 继承与白名单、各字段运行时求值、以及求值失败必须强制终止构建。
"""

from __future__ import annotations

import _common
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from devkit.errors import ExpressionError, RecipeError
from devkit.expr import (
    eval_expression,
    get_builtin_namespace,
    get_effective_expression,
    has_expression,
    should_eval_expression,
    substitute_expression,
    substitute_in_value,
)
from devkit.recipe import is_expression_enabled, load_recipe
from _common import (
    FAIL_CMD,
    TEST_SYSTEM,
    make_runner,
    py_command,
    run_recipe,
    write_recipe,
)


def _ns(path: Path | None = None, system: str = TEST_SYSTEM) -> dict:
    return get_builtin_namespace(path, system)


class TestSubstituteExpression(unittest.TestCase):
    def test_plain_string_unchanged(self):
        self.assertEqual(substitute_expression("hello", _ns()), "hello")

    def test_whole_expression_keeps_int_type(self):
        self.assertEqual(substitute_expression("?!{{1 + 2}}", _ns()), 3)
        self.assertIsInstance(substitute_expression("?!{{1 + 2}}", _ns()), int)

    def test_whole_expression_keeps_bool_and_none(self):
        self.assertIs(substitute_expression("?!{{1 == 1}}", _ns()), True)
        self.assertIsNone(substitute_expression("?!{{None}}", _ns()))

    def test_whole_expression_keeps_containers(self):
        self.assertEqual(substitute_expression("?!{{[1, 2]}}", _ns()), [1, 2])
        self.assertEqual(substitute_expression("?!{{(1, 2)}}", _ns()), (1, 2))
        self.assertEqual(substitute_expression("?!{{ {'a': 1} }}", _ns()), {"a": 1})

    def test_partial_expression_is_stringified(self):
        self.assertEqual(substitute_expression("a-?!{{1 + 2}}-b", _ns()), "a-3-b")

    def test_multiple_expressions_are_all_replaced(self):
        self.assertEqual(
            substitute_expression("x=?!{{1}} y=?!{{[1, 2]}}", _ns()),
            "x=1 y=[1, 2]",
        )

    def test_multiline_expression(self):
        self.assertEqual(substitute_expression("?!{{(\n1 +\n2\n)}}", _ns()), 3)

    def test_nested_braces_in_expression(self):
        self.assertEqual(substitute_expression("?!{{{1: 2}[1]}}", _ns()), 2)
        self.assertEqual(
            substitute_expression("?!{{ {'a': {'b': 1}} }}", _ns()),
            {"a": {"b": 1}},
        )

    def test_braces_inside_string_literals(self):
        self.assertEqual(substitute_expression("?!{{ '}' }}", _ns()), "}")
        self.assertEqual(substitute_expression('?!{{ "}}" }}', _ns()), "}}")
        self.assertEqual(substitute_expression("?!{{ 'a{b}c' + '!' }}", _ns()), "a{b}c!")

    def test_braces_inside_triple_quoted_string(self):
        self.assertEqual(substitute_expression('?!{{ """a}b""" }}', _ns()), "a}b")

    def test_unclosed_expression_is_literal(self):
        self.assertEqual(substitute_expression("?!{{1 + 2", _ns()), "?!{{1 + 2")
        self.assertEqual(substitute_expression("?!{{}}", _ns()), "?!{{}}")
        self.assertEqual(substitute_expression("?!{{1 + 2} }", _ns()), "?!{{1 + 2} }")

    def test_has_expression(self):
        self.assertTrue(has_expression("?!{{1}}"))
        self.assertTrue(has_expression("pre?!{{1}}post"))
        self.assertFalse(has_expression("?!{1}"))
        self.assertFalse(has_expression(123))
        self.assertFalse(has_expression(None))

    def test_substitute_in_value_recurses(self):
        value = {"a": ["?!{{1+1}}", {"b": "?!{{'x'}}"}], "c": 5}
        result = substitute_in_value(value, _ns(), True)
        self.assertEqual(result, {"a": [2, {"b": "x"}], "c": 5})

    def test_substitute_in_value_disabled_leaves_everything(self):
        value = ["?!{{1+1}}", {"a": "?!{{2}}"}]
        self.assertEqual(substitute_in_value(value, _ns(), False), value)


class TestNamespace(unittest.TestCase):
    def test_builtins_available(self):
        self.assertEqual(
            substitute_expression("?!{{__import__('math').floor(2.9)}}", _ns()),
            2,
        )
        self.assertEqual(
            substitute_expression("?!{{sum(range(4))}}", _ns()),
            6,
        )

    def test_get_system_info(self):
        self.assertEqual(substitute_expression("?!{{get_system_info()}}", _ns(system="linux_x86_64")),
                         "linux_x86_64")

    def test_get_recipe_path(self):
        path = Path("some/recipe.json")
        self.assertEqual(substitute_expression("?!{{get_recipe_path()}}", _ns(path)), str(path))

    def test_get_recipe_path_without_context_raises(self):
        with self.assertRaisesRegex(ExpressionError, "无配方上下文"):
            substitute_expression("?!{{get_recipe_path()}}", get_builtin_namespace(None, None))

    def test_namespaces_do_not_share_context(self):
        a = get_builtin_namespace(Path("a.json"), "sys-a")
        b = get_builtin_namespace(Path("b.json"), "sys-b")
        self.assertEqual(substitute_expression("?!{{get_recipe_path()}}", a), "a.json")
        self.assertEqual(substitute_expression("?!{{get_recipe_path()}}", b), "b.json")
        self.assertEqual(substitute_expression("?!{{get_system_info()}}", a), "sys-a")
        self.assertEqual(substitute_expression("?!{{get_system_info()}}", b), "sys-b")

    def test_namespaces_are_thread_safe(self):
        def work(index: int) -> str:
            ns = get_builtin_namespace(Path(f"r{index}.json"), f"sys-{index}")
            return substitute_expression("?!{{get_recipe_path() + '|' + get_system_info()}}", ns)

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(work, range(200)))
        for index, result in enumerate(results):
            self.assertEqual(result, f"r{index}.json|sys-{index}")

    def test_error_contains_full_traceback(self):
        with self.assertRaises(ExpressionError) as ctx:
            eval_expression("1 / 0", _ns(Path("r.json")))
        message = str(ctx.exception)
        self.assertIn("Traceback (most recent call last)", message)
        self.assertIn("ZeroDivisionError", message)
        self.assertIn("?!{{1 / 0}}", message)

    def test_syntax_error_is_wrapped_with_traceback(self):
        with self.assertRaises(ExpressionError) as ctx:
            eval_expression("import os", _ns())
        self.assertIn("SyntaxError", str(ctx.exception))
        self.assertIn("Traceback (most recent call last)", str(ctx.exception))

    def test_system_exit_is_treated_as_expression_error(self):
        with self.assertRaises(ExpressionError) as ctx:
            eval_expression("__import__('sys').exit(3)", _ns())
        self.assertIn("SystemExit", str(ctx.exception))


class TestExpressionConfig(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _load(self, raw: dict):
        return load_recipe(write_recipe(self.tmp, raw))

    def test_parse_true_false_and_list(self):
        self.assertIs(self._load({"expression": True}).expression, True)
        self.assertIs(self._load({"expression": False}).expression, False)
        self.assertEqual(
            self._load({"expression": ["run", "env"]}).expression, ("run", "env")
        )

    def test_parse_invalid_expression(self):
        with self.assertRaisesRegex(RecipeError, "expression"):
            self._load({"expression": "yes"})
        with self.assertRaisesRegex(RecipeError, "expression"):
            self._load({"expression": [1]})

    def test_parse_job_and_step_expression(self):
        recipe = self._load(
            {
                "jobs": {
                    "main": {
                        "expression": ["run"],
                        "steps": {
                            "s": {"expression": True, "run": [["echo", "x"]]},
                            "t": {"expression": False, "run": [["echo", "y"]]},
                        },
                    }
                }
            }
        )
        job = recipe.jobs[0]
        self.assertEqual(job.expression, ("run",))
        steps = {step.name: step for step in job.steps}
        self.assertIs(steps["s"].expression, True)
        self.assertIs(steps["t"].expression, False)

    def test_effective_expression_inheritance(self):
        self.assertIs(get_effective_expression(True), True)
        self.assertIs(get_effective_expression(True, False), False)
        self.assertIs(get_effective_expression(True, False, True), True)
        self.assertIsNone(get_effective_expression(None, None, None))
        self.assertEqual(
            get_effective_expression(True, None, ["run"]), ("run",)
        )

    def test_should_eval_expression(self):
        self.assertFalse(should_eval_expression(None, "run"))
        self.assertFalse(should_eval_expression(False, "run"))
        self.assertTrue(should_eval_expression(True, "run"))
        self.assertTrue(should_eval_expression(["run"], "run"))
        self.assertFalse(should_eval_expression(["run"], "env"))
        self.assertTrue(should_eval_expression(["clone"], "material"))
        self.assertTrue(should_eval_expression(["material"], "material"))

    def test_is_expression_enabled_inherits(self):
        self.assertTrue(is_expression_enabled(True, "run"))
        self.assertFalse(is_expression_enabled(False, "run", recipe_expr=True))
        self.assertTrue(is_expression_enabled(None, "run", recipe_expr=True))
        self.assertFalse(
            is_expression_enabled(None, "run", recipe_expr=True, job_expr=False)
        )
        self.assertTrue(is_expression_enabled(["clone"], "material"))


class ExpressionRunnerTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.workdir = self.tmp / "work"

    def run_raw(self, raw: dict, **kwargs):
        path = write_recipe(self.tmp, raw)
        return run_recipe(path, self.workdir, **kwargs)

    def make_raw(self, raw: dict):
        path = write_recipe(self.tmp, raw)
        return make_runner(self.workdir), path

    def dump_env(self, name: str, var: str) -> list[str]:
        code = f"import os;open('{name}','w').write(os.environ.get('{var}',''))"
        return py_command(code)

    def read(self, name: str) -> str:
        return _common.read(self.workdir / name)


class TestRuntimeFields(ExpressionRunnerTestCase):
    def test_expression_off_by_default(self):
        self.run_raw(
            {
                "env": [{"key": "V", "value": "?!{{1 + 2}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [self.dump_env("env.txt", "V")]}}}},
            }
        )
        self.assertEqual(self.read("env.txt"), "?!{{1 + 2}}")

    def test_run_expression(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {"run": [["echo", "?!{{1 + 2}}", ">", "out.txt"]]}
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "3")

    def test_run_expression_whole_token_keeps_type_then_stringified(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {"run": [["echo", "?!{{[1, 2]}}", ">", "out.txt"]]}
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "[1, 2]")

    def test_env_expression_string_and_int(self):
        self.run_raw(
            {
                "expression": True,
                "env": [
                    {"key": "TEXT", "value": "?!{{'a' + 'b'}}"},
                    {"key": "NUM", "value": "?!{{1 + 2}}"},
                ],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("text.txt", "TEXT"),
                                    self.dump_env("num.txt", "NUM"),
                                ]
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("text.txt"), "ab")
        self.assertEqual(self.read("num.txt"), "3")

    def test_data_expression(self):
        self.run_raw(
            {
                "expression": ["data"],
                "data": ["?!{{1 + 1}}"],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", 0, ">", "out.txt"]]}}}},
            }
        )
        self.assertEqual(self.read("out.txt"), "2")

    def test_selective_whitelist_skips_other_fields(self):
        self.run_raw(
            {
                "expression": ["run"],
                "env": [{"key": "V", "value": "?!{{1 + 2}}"}],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("env.txt", "V"),
                                    ["echo", "?!{{1 + 2}}", ">", "out.txt"],
                                ]
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("env.txt"), "?!{{1 + 2}}")
        self.assertEqual(self.read("out.txt"), "3")

    def test_step_expression_false_disables_inherited_run_expression(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "expression": False,
                                "run": [["echo", "?!{{1 + 2}}", ">", "out.txt"]],
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "?!{{1 + 2}}")

    def test_job_expression_false_disables_job_env_expression(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "expression": False,
                        "env": [{"key": "V", "value": "?!{{1 + 2}}"}],
                        "steps": {"s": {"run": [self.dump_env("env.txt", "V")]}},
                    }
                },
            }
        )
        self.assertEqual(self.read("env.txt"), "?!{{1 + 2}}")

    def test_env_area_with_expressions(self):
        self.run_raw(
            {
                "expression": True,
                "area": [{"key": "A", "value": "?!{{'area' + '1'}}"}],
                "jobs": {
                    "main": {
                        "env": [
                            {"key": "A", "value": "?!{{'job' + '1'}}"},
                            {"key": "B", "value": "?!{{'b' + '1'}}"},
                        ],
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("a.txt", "A"),
                                    self.dump_env("b.txt", "B"),
                                ]
                            }
                        },
                    }
                },
            }
        )
        self.assertEqual(self.read("a.txt"), "job1")
        self.assertEqual(self.read("b.txt"), "b1")

    def test_job_system_expression_matches(self):
        runner = self.run_raw(
            {
                "expression": ["system"],
                "jobs": {
                    "main": {
                        "system": "?!{{get_system_info()}}",
                        "steps": {"s": {"run": [["echo", "x", ">", "out.txt"]]}},
                    }
                },
            }
        )
        self.assertFalse(runner.failed)
        self.assertEqual(self.read("out.txt"), "x")

    def test_job_system_expression_mismatch_skips(self):
        self.run_raw(
            {
                "expression": ["system"],
                "jobs": {
                    "main": {
                        "system": "?!{{'other_system'}}",
                        "steps": {"s": {"run": [["echo", "x", ">", "out.txt"]]}},
                    }
                },
            }
        )
        self.assertFalse((self.workdir / "out.txt").exists())

    def test_step_system_expression_mismatch_skips(self):
        self.run_raw(
            {
                "expression": ["system"],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "system": "?!{{'other_system'}}",
                                "run": [["echo", "x", ">", "out.txt"]],
                            },
                            "t": {"run": [["echo", "y", ">", "next.txt"]]},
                        }
                    }
                },
            }
        )
        self.assertFalse((self.workdir / "out.txt").exists())
        self.assertEqual(self.read("next.txt"), "y")

    def test_system_expression_disabled_does_not_match(self):
        self.run_raw(
            {
                "expression": ["run"],
                "jobs": {
                    "main": {
                        "system": "?!{{get_system_info()}}",
                        "steps": {"s": {"run": [["echo", "x", ">", "out.txt"]]}},
                    }
                },
            }
        )
        self.assertFalse((self.workdir / "out.txt").exists())

    def test_data_expression_can_return_container(self):
        self.run_raw(
            {
                "expression": True,
                "data": ["?!{{ {'a': 1} }}"],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", 0, ">", "out.txt"]]}}}},
            }
        )
        self.assertEqual(self.read("out.txt"), "{'a': 1}")

    def test_none_result_is_stringified(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {"run": [["echo", "?!{{None}}", ">", "out.txt"]]}
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "None")

    def test_empty_first_token_after_expression_is_rejected(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {"steps": {"s": {"run": [["?!{{''}}", "x"]]}}}
                },
            }
        )
        with self.assertRaisesRegex(RecipeError, "不能为空"):
            runner.run(path)

    def test_single_brace_marker_is_untouched(self):
        self.run_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {"run": [["echo", "?!{1+2}", ">", "out.txt"]]}
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "?!{1+2}")

    def test_recipe_false_step_true_overrides(self):
        self.run_raw(
            {
                "expression": False,
                "env": [{"key": "V", "value": "?!{{'recipe'}}"}],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "expression": True,
                                "run": [
                                    self.dump_env("env.txt", "V"),
                                    ["echo", "?!{{'run'}}", ">", "out.txt"],
                                ],
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("env.txt"), "recipe")
        self.assertEqual(self.read("out.txt"), "run")

    def test_step_false_disables_inherited_recipe_env_expression(self):
        self.run_raw(
            {
                "expression": True,
                "env": [{"key": "V", "value": "?!{{'recipe'}}"}],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "expression": False,
                                "run": [self.dump_env("env.txt", "V")],
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("env.txt"), "?!{{'recipe'}}")

    def test_job_whitelist_applies_to_job_env(self):
        self.run_raw(
            {
                "expression": False,
                "jobs": {
                    "main": {
                        "expression": ["env"],
                        "env": [{"key": "V", "value": "?!{{'job'}}"}],
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("env.txt", "V"),
                                    ["echo", "?!{{'run'}}", ">", "out.txt"],
                                ]
                            }
                        },
                    }
                },
            }
        )
        self.assertEqual(self.read("env.txt"), "job")
        self.assertEqual(self.read("out.txt"), "?!{{'run'}}")

    def test_area_expression_override(self):
        self.run_raw(
            {
                "expression": True,
                "area": [{"key": "A", "value": "?!{{'recipe'}}"}],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "area": [{"key": "A", "value": "?!{{'step'}}"}],
                                "run": [self.dump_env("a.txt", "A")],
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("a.txt"), "step")

    def test_env_item_system_expression(self):
        self.run_raw(
            {
                "expression": True,
                "env": [
                    {
                        "key": "MATCH",
                        "value": "?!{{'yes'}}",
                        "system": "?!{{get_system_info()}}",
                    },
                    {
                        "key": "OTHER",
                        "value": "?!{{1 / 0}}",
                        "system": "?!{{'linux_x86_64'}}",
                    },
                ],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("match.txt", "MATCH"),
                                    self.dump_env("other.txt", "OTHER"),
                                ]
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("match.txt"), "yes")
        self.assertEqual(self.read("other.txt"), "")


class TestMaterialDownloadUnzipUnpack(ExpressionRunnerTestCase):
    def test_material_expression(self):
        runner, path = self.make_raw(
            {
                "expression": ["material"],
                "material": {"repo": "?!{{'https://host/' + 'repo.git'}}"},
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.ensure_clone") as clone:
            runner.run(path)
        self.assertEqual(clone.call_args.args[0].repo, "https://host/repo.git")

    def test_material_clone_alias(self):
        runner, path = self.make_raw(
            {
                "expression": ["clone"],
                "material": {"repo": "?!{{'git://' + 'x'}}", "branch": "?!{{'ma' + 'in'}}"},
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.ensure_clone") as clone:
            runner.run(path)
        material = clone.call_args.args[0]
        self.assertEqual(material.repo, "git://x")
        self.assertEqual(material.branch, "main")

    def test_download_expression(self):
        runner, path = self.make_raw(
            {
                "expression": ["download"],
                "download": [{"url": "?!{{'https://host/' + 'file.bin'}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.download_one") as download:
            runner.run(path)
        self.assertEqual(download.call_args.args[0].url, "https://host/file.bin")

    def test_unzip_expression(self):
        runner, path = self.make_raw(
            {
                "expression": ["unzip"],
                "unzip": [{"file": "?!{{'arch' + '.zip'}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.unzip_one") as unzip:
            runner.run(path)
        self.assertEqual(unzip.call_args.args[0].file, "arch.zip")

    def test_unpack_expression(self):
        runner, path = self.make_raw(
            {
                "expression": ["unpack"],
                "unpack": [{"url": "?!{{'https://host/' + 'a.zip'}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.unpack_one") as unpack:
            runner.run(path)
        self.assertEqual(unpack.call_args.args[0].url, "https://host/a.zip")

    def test_download_expression_disabled_stays_literal(self):
        runner, path = self.make_raw(
            {
                "expression": ["run"],
                "download": [{"url": "?!{{'https://host/file.bin'}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.download_one") as download:
            runner.run(path)
        self.assertEqual(download.call_args.args[0].url, "?!{{'https://host/file.bin'}}")

    def test_nonmatching_system_entries_are_not_evaluated(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "env": [
                    {"key": "GOOD", "value": "?!{{'ok'}}"},
                    {"key": "BAD", "value": "?!{{1 / 0}}", "system": "linux_x86_64"},
                ],
                "download": [
                    {"url": "?!{{'https://host/ok.bin'}}"},
                    {"url": "?!{{1 / 0}}", "system": "linux_x86_64"},
                ],
                "unzip": [
                    {"file": "?!{{'ok.zip'}}"},
                    {"file": "?!{{1 / 0}}", "system": "linux_x86_64"},
                ],
                "unpack": [
                    {"url": "?!{{'https://host/ok.zip'}}"},
                    {"url": "?!{{1 / 0}}", "system": "linux_x86_64"},
                ],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "run": [
                                    self.dump_env("good.txt", "GOOD"),
                                    self.dump_env("bad.txt", "BAD"),
                                ]
                            }
                        }
                    }
                },
            }
        )
        with (
            mock.patch("devkit.runner.download_one"),
            mock.patch("devkit.runner.unzip_one"),
            mock.patch("devkit.runner.unpack_one"),
        ):
            runner.run(path)
        self.assertEqual(self.read("good.txt"), "ok")
        self.assertEqual(self.read("bad.txt"), "")

    def test_download_item_system_expression(self):
        runner, path = self.make_raw(
            {
                "expression": ["system", "download"],
                "download": [
                    {"url": "?!{{'https://host/ok.bin'}}", "system": "?!{{get_system_info()}}"},
                    {"url": "?!{{1 / 0}}", "system": "?!{{'linux_x86_64'}}"},
                ],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with mock.patch("devkit.runner.download_one") as download:
            runner.run(path)
        self.assertEqual(download.call_args_list[0].args[0].url, "https://host/ok.bin")
        self.assertEqual(
            download.call_args_list[1].args[0].url, "?!{{1 / 0}}"
        )


class TestCallExpression(ExpressionRunnerTestCase):
    def test_call_path_and_args_expressions(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        write_recipe(
            sub_dir,
            {
                "expression": True,
                "data": ["placeholder", 0],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", 1, ">", "out.txt"]]}}}},
            },
        )
        self.run_raw(
            {
                "expression": ["call"],
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "call": {
                                    "?!{{'sub/' + 'recipe.json'}}": ["?!{{'hello'}}"]
                                }
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(self.read("out.txt"), "hello")

    def test_subrecipe_get_recipe_path_points_to_itself(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        sub_path = write_recipe(
            sub_dir,
            {
                "expression": True,
                "env": [{"key": "SUB", "value": "?!{{get_recipe_path()}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [self.dump_env("sub.txt", "SUB")]}}}},
            },
        )
        self.run_raw(
            {
                "jobs": {"main": {"steps": {"s": {"call": {"sub/recipe.json": []}}}}},
            }
        )
        self.assertEqual(self.read("sub.txt"), str(sub_path))


class TestSubrecipeEnvAndConcurrency(ExpressionRunnerTestCase):
    def test_subrecipe_inherits_parent_step_env_expressions(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        write_recipe(
            sub_dir,
            {
                "expression": True,
                "jobs": {"main": {"steps": {"s": {"run": [self.dump_env("sub.txt", "P")]}}}},
            },
        )
        parent_path = write_recipe(
            self.tmp,
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "env": [
                                    {"key": "P", "value": "?!{{'parent:' + get_recipe_path()}}"}
                                ],
                                "call": {"sub/recipe.json": []},
                            }
                        }
                    }
                },
            },
        )
        run_recipe(parent_path, self.workdir)
        self.assertEqual(self.read("sub.txt"), f"parent:{parent_path}")

    def test_top_level_area_blocks_inherited_env_with_expressions(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        write_recipe(
            sub_dir,
            {
                "area": [],
                "jobs": {"main": {"steps": {"s": {"run": [self.dump_env("sub.txt", "P")]}}}},
            },
        )
        parent_path = write_recipe(
            self.tmp,
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "s": {
                                "env": [{"key": "P", "value": "?!{{'parent'}}"}],
                                "call": {"sub/recipe.json": []},
                            }
                        }
                    }
                },
            },
        )
        run_recipe(parent_path, self.workdir)
        self.assertEqual(self.read("sub.txt"), "")

    def test_concurrent_subrecipes_keep_their_own_recipe_context(self):
        def make_sub(name: str) -> Path:
            directory = self.tmp / name
            directory.mkdir()
            code = (
                "import os,time;time.sleep(0.5);"
                f"open('{name}.txt','w').write(os.environ.get('P',''))"
            )
            return write_recipe(
                directory,
                {
                    "expression": True,
                    "env": [{"key": "P", "value": "?!{{get_recipe_path()}}"}],
                    "jobs": {"main": {"steps": {"s": {"run": [py_command(code)]}}}},
                },
            )

        sub_a = make_sub("suba")
        sub_b = make_sub("subb")
        parent_path = write_recipe(
            self.tmp,
            {
                "jobs": {
                    "a": {
                        "linkup": "b",
                        "steps": {"s": {"call": {"suba/recipe.json": []}}},
                    },
                    "b": {"steps": {"s": {"call": {"subb/recipe.json": []}}}},
                }
            },
        )
        run_recipe(parent_path, self.workdir)
        self.assertEqual(self.read("suba.txt"), str(sub_a))
        self.assertEqual(self.read("subb.txt"), str(sub_b))


class TestExpressionFailuresAreFatal(ExpressionRunnerTestCase):
    def test_step_run_error_bypasses_proceed(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "errskip": "proceed",
                        "steps": {
                            "boom": {
                                "errskip": "proceed",
                                "run": [["echo", "?!{{1 / 0}}"]],
                            },
                            "after": {"run": [["echo", "x", ">", "after.txt"]]},
                        },
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError) as ctx:
            runner.run(path)
        self.assertIn("ZeroDivisionError", str(ctx.exception))
        self.assertIn("Traceback (most recent call last)", str(ctx.exception))
        self.assertFalse((self.workdir / "after.txt").exists())

    def test_step_run_error_bypasses_job_skip(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "steps": {"boom": {"run": [["echo", "?!{{1 / 0}}"]]}},
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_step_system_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "steps": {
                            "boom": {"system": "?!{{1 / 0}}", "run": [["echo", "x"]]}
                        },
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_job_system_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "system": "?!{{1 / 0}}",
                        "steps": {"s": {"run": [["echo", "x"]]}},
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_env_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "env": [{"key": "V", "value": "?!{{1 / 0}}"}],
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "steps": {"s": {"run": [["echo", "x", ">", "out.txt"]]}},
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)
        self.assertFalse((self.workdir / "out.txt").exists())

    def test_data_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "data": ["?!{{1 / 0}}"],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", 0]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_download_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "download": [{"url": "?!{{1 / 0}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_unzip_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "unzip": [{"file": "?!{{1 / 0}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_unpack_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "unpack": [{"url": "?!{{1 / 0}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_system_filter_expression_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "download": [{"url": "x", "system": "?!{{1 / 0}}"}],
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_material_error_is_fatal(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "material": {"repo": "?!{{1 / 0}}"},
                "jobs": {"main": {"steps": {"s": {"run": [["echo", "x"]]}}}},
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_subrecipe_error_aborts_parent(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        write_recipe(
            sub_dir,
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "steps": {"boom": {"run": [["echo", "?!{{1 / 0}}"]]}},
                    }
                },
            },
        )
        runner, path = self.make_raw(
            {
                "jobs": {
                    "main": {
                        "errskip": "skip",
                        "steps": {
                            "call": {"call": {"sub/recipe.json": []}},
                            "after": {"run": [["echo", "x", ">", "after.txt"]]},
                        },
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)
        self.assertFalse((self.workdir / "after.txt").exists())

    def test_expression_error_wins_over_other_linkup_step_failure(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "main": {
                        "steps": {
                            "a": {
                                "linkup": ["b"],
                                "errskip": "proceed",
                                "run": [FAIL_CMD],
                            },
                            "b": {
                                "skip": True,
                                "run": [["echo", "?!{{1 / 0}}"]],
                            },
                        }
                    }
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)

    def test_expression_error_wins_over_other_linkup_job_failure(self):
        runner, path = self.make_raw(
            {
                "expression": True,
                "jobs": {
                    "a": {
                        "linkup": "b",
                        "steps": {"a": {"run": [FAIL_CMD]}},
                    },
                    "b": {
                        "skip": True,
                        "steps": {"b": {"run": [["echo", "?!{{1 / 0}}"]]}},
                    },
                },
            }
        )
        with self.assertRaises(ExpressionError):
            runner.run(path)


class TestConsole(unittest.TestCase):
    def test_console_injects_namespace(self):
        from devkit import cli
        from devkit.system import detect_system

        with tempfile.TemporaryDirectory() as td, mock.patch("code.interact") as interact:
            path = _common.write_recipe(Path(td), {})
            code = cli.main(["console", str(path)])
        self.assertEqual(code, 0)
        namespace = interact.call_args.kwargs["local"]
        self.assertIn("get_recipe_path", namespace)
        self.assertIn("get_system_info", namespace)
        self.assertIn("__builtins__", namespace)
        self.assertIn("devkit", namespace)
        self.assertIn("recipe", namespace)
        self.assertIn("runner", namespace)
        self.assertIn("expr", namespace)
        self.assertEqual(namespace["get_system_info"](), detect_system())
        self.assertEqual(namespace["__builtins__"].sum([1, 2, 3]), 6)
        self.assertIsNotNone(namespace["get_recipe_path"]())
