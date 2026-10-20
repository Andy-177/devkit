"""测试公共设施: sys.path 自举、配方构造、跨平台命令片段。

先导入 ``devkit.cli``：它在导入任何 devkit 模块之前装好 loguru 降级垫片，
这样在没装 loguru 的解释器上， ``devkit.runner`` 等模块也能导入。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
for _p in (str(ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import devkit.cli  # noqa: F402,E402  装载 loguru 垫片
from devkit.recipe import Recipe, Step  # noqa: E402
from devkit.runner import RecipeRunner  # noqa: E402

#: 所有测试统一使用的系统标识（与真实主机无关，保证可重复）
TEST_SYSTEM = "win_amd64"
#: 永远找不到的命令名 -> 任何 shell 都返回非 0
FAIL_CMD = ["this-command-does-not-exist-devkit"]
#: sys.executable 是否含空格（含空格时无法安全拼进 shell 命令行）
PY_SPACED = " " in sys.executable

IS_WIN = sys.platform == "win32"


def write_recipe(directory: Path, raw: dict, name: str = "recipe.json") -> Path:
    """把 dict 写成 JSON 配方文件，返回路径。"""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def make_runner(workdir: Path, **kwargs) -> RecipeRunner:
    """构造一个跑在临时目录里的 RecipeRunner。"""
    kwargs.setdefault("download_dir", workdir / "dl")
    kwargs.setdefault("system", TEST_SYSTEM)
    return RecipeRunner(workdir=workdir, **kwargs)


def run_recipe(path: Path, workdir: Path, **kwargs) -> RecipeRunner:
    """跑一个配方并返回 runner（断言 runner.failed 用）。"""
    runner = make_runner(workdir, **kwargs)
    runner.run(path)
    return runner


def read(path: Path | str) -> str:
    """读文件内容并去掉首尾空白（跨平台换行差异）。"""
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()


def exit_code_command(code: int) -> list[str]:
    """一条退出码为 ``code`` 的 shell 命令片段（只用 shell 内建，跨平台）。"""
    if IS_WIN:
        return ["cmd", "/c", f'"exit {code}"']
    return ["sh", "-c", f'"exit {code}"']


def slow_write(name: str, seconds: int = 1) -> list[str]:
    """一条“先等 seconds 秒再写文件”的 shell 命令片段（sleep 全平台可用）。"""
    if IS_WIN:
        # ping 是 Windows 上不用 stdin 的最小延时手段（timeout 在 stdin 重定向下会报错）
        return ["ping", "-n", str(seconds + 1), "127.0.0.1", "> ", "nul", "&&",
                "echo", "x", ">", name]
    return ["sleep", str(seconds), "&&", "echo", "x", ">", name]


def py_command(code: str) -> list[str]:
    """一条 ``python -c "<code>"`` 的 shell 命令片段；code 内不能含双引号。"""
    return [sys.executable, "-c", f'"{code}"']


def echo_write(name: str, text: str = "x") -> list[str]:
    """一条 ``echo text > name`` 的 shell 命令片段。"""
    return ["echo", text, ">", name]


def simple_recipe(tmp: Path, *, steps: dict, jobs_extra: dict | None = None,
                  data: list | None = None, name: str = "recipe.json") -> Path:
    """单作业配方：jobs = {"main": {"steps": steps, **jobs_extra}}。"""
    job_body: dict = {"steps": steps}
    if jobs_extra:
        job_body.update(jobs_extra)
    raw: dict = {"jobs": {"main": job_body}}
    if data is not None:
        raw["data"] = data
    return write_recipe(tmp, raw, name)


def raw_step(**body) -> dict:
    """步骤体（run 等字段按关键字传入）。"""
    return body


def dummy(path: Path | None = None) -> tuple[Recipe, Step]:
    """给需要 Recipe/Step 参数的底层函数用的占位对象。"""
    return Recipe(path=path or Path("recipe.json")), Step(name="s")
