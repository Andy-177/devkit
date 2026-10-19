"""配方文件(repice.json)的加载与校验。

配方是一个 JSON 文件，所有字段都是可选的::

    {
      "material": {
        "repo": "https://github.com/xxx/yyy.git",   # 有 material 就必须有 repo
        "branch": "main",                            # 缺省/null/"" -> 默认分支
        "depth": 1,                                  # 0/null -> 完整历史，>0 -> 浅克隆深度
        "commit": null                               # 可选，克隆后检出该提交
      },
      "download": [
        "https://example.com/tool.zip",                    # 总是下载
        {"https://example.com/win.exe": "win_amd64"},      # 仅当系统匹配才下载
        {"https://example.com/mac.dmg": ["macos_arm64", "macos_amd64"]}
      ],
      "unzip": [
        "releases.zip",                                    # 总是解压
        {"toolchain.zip": "win_amd64",                     # 仅当系统匹配才解压
         "../assets/extra.zip": ["macos_arm64", "macos_amd64"]}
      ],
      "data": ["build.sh", 0],
      "jobs": {
        "build-windows": {
          "system": "win_amd64",          # 与当前系统不一致则跳过该工作流
          "skip": true,                   # 顺序走到这个作业时跳过它（被 next/linkup 指到时仍执行）
          "linkup": "prepare",            # 联动: 和 prepare 一起并发执行（字符串或列表）
          "steps": {
            "init-msys2": {
              "run": [
                ["msys2-base.exe"],         # 一条命令（能解析成程序就直接启动）
                ["buildsh.bat", "-c", 0],   # 数字表示引用 data 中对应下标的数据
                ["echo hello"]              # 一条命令（解析不出程序就交给 shell）
              ],
              "call": {
                "hello.json": ["hello"]   # 相对本配方文件的路径 + 传入的参数列表
              }
            }
          }
        }
      }
    }

unzip 和 download 类似，只是把链接换成了 zip 文件路径：相对路径先按配方文件所在
目录解析，找不到再按工作区、最后按下载目录；解压结果统一落到下载目录
（默认 ``.devkit``）。

``run`` 永远是"双层列表"：第一层是要执行的命令列表，第二层才是一条命令。第一层
里命令的位置就是执行顺序，第 0 条跑完跑第 1 条，依次往下::

    "run": [["echo a"], ["echo b"]]   # 两条命令: 先 echo a，再 echo b

即使只有一条命令，也要保留第一层列表::

    "run": [["echo a"]]               # 一条命令

第二层列表是一条命令，里面的元素只能是字符串或数字（数字是 data 下标）。运行时
先做替换，然后按下面的规则解析成一条命令：

* 第一个片段是绝对路径，或者能在工作区里找到同名文件 -> 当作"可执行文件 + 参数"
  直接启动（不走 shell）；
* 否则把各片段按空格拼成一条 shell 命令行，交给系统 shell。

因此 ``[["echo a"]]``、``[["echo", 0, ">", "out.txt"]]`` 走 shell；而
``[["C:/Python/python.exe", "-c", 0]]``、``[["tool.exe", "-v"]]`` 直接启动。

执行顺序是固定的，没有集中式编排：``material`` 克隆 -> ``download``（按列表顺序）
-> ``unzip``（按列表顺序）-> ``jobs``（按声明顺序）。编排的权利下放到步骤里，
步骤的控制字段（全部可选）::

    "init": {
      "run": [...],
      "call": {...},
      "skip": true,         # 顺序走到这个步骤时跳过它（被 jump/linkup/if 指到时仍然执行）
      "errskip": "stop",    # 失败等级: crash/stop/proceed/skip（缺省 stop）
      "linkup": ["other"],  # 联动: 和 other 等步骤一起并发执行
      "jump": "step-x",     # 执行完这个步骤后跳转到 step-x
      "next": null,         # 切换作业: null=结束本作业继续下一个作业，"作业名"=跳到该作业
      "if": {
        "error": "on-error",        # 失败后跳转到 on-error
        "success": "on-ok",         # 成功后跳转到 on-ok
        "jump": {"src": "dst"},     # 全局跳转表: src 执行完后跳转到 dst
        "redirect": {"src": "dst"}  # 全局重定向表: 走到 src 时不执行它，直接跳到 dst
      }
    }

* ``skip``/``linkup`` 只影响"顺序流过"：被 ``jump``/``if.success``/``if.error``/
  ``linkup`` 明确指到的步骤照常执行。``linkup`` 指到的步骤如果没有 ``skip:true``，
  联动跑完后顺序走到它时会再执行一次；
* 失败等级 ``errskip``（缺省 ``stop``，只能是 crash/stop/proceed/skip 之一）：
  - ``crash``: 打印报错，整个构建立即中止（不理会作业级的 errskip）；
  - ``stop``: 打印报错，终止当前作业，由所在作业的 ``errskip`` 决定最终收场；
  - ``proceed``: 只终止出错的步骤（联动的步骤继续跑完），再按去向跳转，作业继续；
  - ``skip``: 终止出错的步骤和它联动的步骤，再按去向跳转，作业继续；
  - ``proceed``/``skip`` 的去向：``if.error`` > ``jump`` > 属于该步骤的 ``if.jump`` 边
    > 顺序下一步（``if.error`` 优先级最高，只有它为空时才用 ``jump``）；
  - 被 errskip 处理掉的失败会让整个构建最终以非 0 退出码结束；
* 成功后的去向优先级：``next`` > ``if.success`` > ``jump`` > ``if.jump``（属于该步骤的边）
  > 顺序下一步；
* ``if.jump``/``if.redirect`` 的键是"发出跳转的步骤"，值是目标步骤，在同一个作业内
  生效；同一个步骤的 ``if`` 里两张表不能有相同的键，引用了不存在的步骤/作业都会报错；
* ``linkup`` 组里任何一个步骤失败，按发起联动的那个步骤的 ``errskip`` 处理：``proceed``
  时联动的步骤继续跑完，``skip``/``stop``/``crash`` 时整个组一起被终止。

作业（jobs 里的每个工作流）也有 ``skip``/``linkup``/``errskip`` 三个控制字段（全部可选）::

    "build": {
      "system": "win_amd64",
      "skip": true,           # 顺序走到这个作业时跳过它（被 next/linkup 指到时仍执行）
      "linkup": "prepare",    # 联动: 和 prepare 一起并发执行（值是作业名或作业名列表）
      "errskip": "proceed",   # 失败等级: crash/proceed/skip（缺省 proceed）
      "steps": {...}
    }

* ``skip:true`` 的作业顺序走到时跳过，被 ``next``/``linkup`` 指到时照常执行；
* 失败等级 ``errskip``（缺省 ``proceed``，只能是 crash/proceed/skip 之一）：
  - ``crash``: 打印报错，整个构建立即中止；
  - ``proceed``: 终止出错的作业，它联动的作业继续跑完，然后执行下一个作业；
  - ``skip``: 强行终止出错的作业和它联动的作业，然后执行下一个作业；
* ``linkup`` 的作业和发起联动的作业并发执行（每个作业内部的步骤仍按自己的顺序跑），
  联动组里任何一个作业失败，按发起联动的那个作业的 ``errskip`` 处理（``proceed`` 时
  联动的作业跑完，否则整组终止）；被联动的作业如果没有 ``skip:true``，联动跑完后顺序
  走到它时会再执行一次；
* 联动期间子进程的 stdin 守卫照常生效（整个联动组共用，任何成员读 stdin 就整组杀掉）；
* 被作业级 errskip 处理掉的失败会让整个构建最终以非 0 退出码结束。

联动是并发执行，期间子进程的 stdin 不是终端，而是一个装满空行的临时文件：只要哪个
并发子进程去读 stdin，就说明这个程序需要人输入，devkit 会立刻杀掉整个联动组并报错。
交互式程序请不要联动（不联动就会继承真正的终端，交互正常）。

data 里出现整数时，含义取决于配方如何被使用：

* 顶层配方: 整数是普通数据，``run`` 中用下标引用它；
* 被 ``call`` 调用的配方: 整数是入参占位符，表示"取 call 参数列表中第 N 个"。
  例如 ``hello.json`` 的 ``data`` 是 ``["hi", 0]``，``0`` 位于下标 1，
  被 ``call: {"hello.json": ["hello"]}`` 调用后 data 变成 ``["hi", "hello"]``,
  于是 ``run: [["echo", 1]]`` 输出的就是传入的 ``hello``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from devkit.errors import RecipeError

#: 目录形式的配方路径会依次尝试这些文件名
RECIPE_NAMES = ("repice.json", "recipe.json")

#: ``step.next`` 为 null（结束本作业、继续下一个作业）时用它表示
NEXT_JOB = ""

#: 步骤的 errskip 等级
STEP_ERR_CRASH = "crash"
STEP_ERR_STOP = "stop"
STEP_ERR_PROCEED = "proceed"
STEP_ERR_SKIP = "skip"
STEP_ERR_LEVELS = (STEP_ERR_CRASH, STEP_ERR_STOP, STEP_ERR_PROCEED, STEP_ERR_SKIP)
#: 作业的 errskip 等级（比步骤少一档：没有 stop，缺省是 proceed）
JOB_ERR_CRASH = "crash"
JOB_ERR_PROCEED = "proceed"
JOB_ERR_SKIP = "skip"
JOB_ERR_LEVELS = (JOB_ERR_CRASH, JOB_ERR_PROCEED, JOB_ERR_SKIP)

#: 步骤 errskip 缺省: 只终止当前作业，由作业的 errskip 收场
DEFAULT_STEP_ERRSKIP = STEP_ERR_STOP
#: 作业 errskip 缺省: 终止出错的作业，联动的作业跑完再继续下一个作业
DEFAULT_JOB_ERRSKIP = JOB_ERR_PROCEED

_KNOWN_TOP_KEYS = {"material", "download", "unzip", "data", "jobs"}
_KNOWN_MATERIAL_KEYS = {"repo", "branch", "depth", "commit"}
_KNOWN_JOB_KEYS = {"system", "steps", "skip", "linkup", "errskip"}
_KNOWN_STEP_KEYS = {"run", "call", "skip", "errskip", "linkup", "jump", "next", "if"}
_KNOWN_IF_KEYS = {"error", "success", "jump", "redirect"}


@dataclass(frozen=True)
class Material:
    """构建材料: 一个 git 仓库。"""

    repo: str
    branch: str | None = None
    depth: int | None = None
    commit: str | None = None


@dataclass(frozen=True)
class DownloadSpec:
    """一个下载项，可带系统过滤。``systems`` 为 None 表示所有系统都下载。"""

    url: str
    systems: tuple[str, ...] | None = None

    def matches(self, system: str) -> bool:
        return self.systems is None or system in self.systems


@dataclass(frozen=True)
class UnzipSpec:
    """一个解压项: 一个 zip 路径，可带系统过滤。``systems`` 为 None 表示总是解压。"""

    path: str
    systems: tuple[str, ...] | None = None

    def matches(self, system: str) -> bool:
        return self.systems is None or system in self.systems


@dataclass(frozen=True)
class StepIf:
    """步骤的 ``if``：失败/成功后的去向，以及全局跳转/重定向表。"""

    error: str | None = None
    success: str | None = None
    jump: tuple[tuple[str, str], ...] = ()
    redirect: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Step:
    """一个步骤: 依次执行 run 中的指令，然后调用 call 中的配方。

    ``run`` 永远是双层列表：第一层是要执行的命令列表（按位置决定顺序），第二层是
    一条命令，只能由字符串/数字组成（数字是 data 下标）。运行时若第一个片段是
    绝对路径或工作区里的已有文件，就"可执行文件 + 参数"直接启动，否则各片段按
    空格拼成一条 shell 命令行。
    """

    name: str
    run: tuple[tuple[Any, ...], ...] = ()
    call: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    skip: bool = False
    errskip: str = DEFAULT_STEP_ERRSKIP
    linkup: tuple[str, ...] = ()
    jump: str | None = None
    next: str | None = None
    when: StepIf = StepIf()


@dataclass(frozen=True)
class Job:
    """一个工作流，仅在当前系统与 ``system`` 一致时执行。"""

    name: str
    system: str | None = None
    steps: tuple[Step, ...] = ()
    skip: bool = False
    linkup: tuple[str, ...] = ()
    errskip: str = DEFAULT_JOB_ERRSKIP

    def matches(self, system: str) -> bool:
        return self.system is None or self.system == system


@dataclass(frozen=True)
class Recipe:
    """解析后的配方。"""

    path: Path
    material: Material | None = None
    downloads: tuple[DownloadSpec, ...] = ()
    unzips: tuple[UnzipSpec, ...] = ()
    data: tuple[Any, ...] = ()
    jobs: tuple[Job, ...] = field(default_factory=tuple)

    @property
    def dir(self) -> Path:
        return self.path.parent


def resolve_recipe_path(path: str | Path) -> Path:
    """把用户给出的路径解析成具体的配方文件路径。

    目录的话依次尝试 ``repice.json`` / ``recipe.json``。
    """
    p = Path(path).expanduser()
    if p.is_dir():
        for name in RECIPE_NAMES:
            cand = p / name
            if cand.is_file():
                return cand
        raise RecipeError(f"目录 {p} 中没有找到 {'/'.join(RECIPE_NAMES)}")
    if not p.is_file():
        raise RecipeError(f"配方文件不存在: {p}")
    return p


def load_recipe(path: str | Path) -> Recipe:
    """读取并校验一个配方文件。"""
    file_path = resolve_recipe_path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RecipeError(f"无法读取配方 {file_path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecipeError(f"配方 {file_path} 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RecipeError(f"配方 {file_path} 顶层必须是 JSON 对象")
    return _parse_recipe(file_path, raw)


def _warn_unknown(raw: dict, known: set[str], where: str) -> None:
    for key in raw:
        if key not in known:
            logger.warning("忽略未知字段: {} (in {})", key, where)


def _optional_str(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecipeError(f"{where} 必须是字符串")
    return value or None


def _parse_material(raw: Any, where: str) -> Material | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是对象")
    _warn_unknown(raw, _KNOWN_MATERIAL_KEYS, where)
    repo = raw.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise RecipeError(f"{where}.repo 必填且不能为空")
    branch = _optional_str(raw.get("branch"), f"{where}.branch")
    commit = _optional_str(raw.get("commit"), f"{where}.commit")
    depth = raw.get("depth")
    if depth is not None:
        if not isinstance(depth, int) or isinstance(depth, bool):
            raise RecipeError(f"{where}.depth 必须是整数")
        if depth < 0:
            raise RecipeError(f"{where}.depth 不能为负数")
        depth = depth or None
    return Material(repo=repo.strip(), branch=branch, depth=depth, commit=commit)


def _parse_system_filter(value: Any, where: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,) if value else None
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value) or None
    raise RecipeError(f"{where} 必须是字符串或字符串列表")


def _parse_spec_list(raw: Any, where: str, make, what: str) -> tuple:
    """解析 download/unzip 这类"列表 + 可选系统过滤"的字段。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RecipeError(f"{where} 必须是列表")
    specs: list[Any] = []
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        if isinstance(item, str):
            if not item:
                raise RecipeError(f"{item_where} {what}不能为空")
            specs.append(make(item, None))
        elif isinstance(item, dict):
            if not item:
                raise RecipeError(f"{item_where} 不能为空对象")
            for value, systems in item.items():
                if not isinstance(value, str) or not value:
                    raise RecipeError(f"{item_where} 的键必须是非空的{what}")
                specs.append(make(value, _parse_system_filter(systems, item_where)))
        else:
            raise RecipeError(f"{item_where} 必须是{what}字符串或 {{{what}: 系统}} 对象")
    return tuple(specs)


def _parse_download(raw: Any, where: str) -> tuple[DownloadSpec, ...]:
    return _parse_spec_list(raw, where, DownloadSpec, "下载链接")


def _parse_unzip(raw: Any, where: str) -> tuple[UnzipSpec, ...]:
    return _parse_spec_list(raw, where, UnzipSpec, "zip 路径")


def _run_scalar(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    return isinstance(value, (str, int, float))


def _parse_run(raw: Any, where: str) -> tuple[tuple[Any, ...], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RecipeError(f"{where} 必须是列表")
    commands: list[tuple[Any, ...]] = []
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        if not isinstance(item, list):
            raise RecipeError(f"{item_where} 必须是一个命令列表（run 永远是双层列表）")
        if not item:
            raise RecipeError(f"{item_where} 指令不能为空")
        for part in item:
            if not _run_scalar(part):
                raise RecipeError(f"{item_where} 的元素必须是字符串或数字")
            if isinstance(part, str) and not part:
                raise RecipeError(f"{item_where} 指令片段不能为空")
        commands.append(tuple(item))
    return tuple(commands)


def _parse_call(raw: Any, where: str) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是对象")
    calls: list[tuple[str, tuple[Any, ...]]] = []
    for rel, args in raw.items():
        if not isinstance(rel, str) or not rel:
            raise RecipeError(f"{where} 的键必须是配方相对路径")
        if args is None:
            calls.append((rel, ()))
        elif isinstance(args, list):
            calls.append((rel, tuple(args)))
        else:
            calls.append((rel, (args,)))
    return tuple(calls)


def _parse_bool(raw: Any, where: str) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise RecipeError(f"{where} 必须是 true/false")
    return raw


def _parse_level(raw: Any, where: str, levels: tuple[str, ...], default: str) -> str:
    if raw is None:
        return default
    if isinstance(raw, str) and raw in levels:
        return raw
    raise RecipeError(f"{where} 必须是 {'/'.join(levels)} 之一")


def _parse_step_name(raw: Any, where: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise RecipeError(f"{where} 必须是非空字符串(步骤名)")
    return raw


def _parse_step_names(raw: Any, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in raw):
        raise RecipeError(f"{where} 必须是步骤名列表")
    return tuple(raw)


def _parse_job_linkup(raw: Any, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) and raw:
        return (raw,)
    if isinstance(raw, list) and raw and all(isinstance(v, str) and v for v in raw):
        return tuple(raw)
    raise RecipeError(f"{where} 必须是作业名或作业名列表")


def _parse_next(raw: Any, where: str, present: bool) -> str | None:
    if not present:
        return None
    if raw is None:
        return NEXT_JOB
    if not isinstance(raw, str) or not raw:
        raise RecipeError(f"{where} 必须是 null（下一个作业）或非空作业名")
    return raw


def _parse_name_map(raw: Any, where: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是 {{原步骤: 目标步骤}} 对象")
    pairs: list[tuple[str, str]] = []
    for src, dst in raw.items():
        if not isinstance(src, str) or not src or not isinstance(dst, str) or not dst:
            raise RecipeError(f"{where} 的键和值都必须是非空字符串(步骤名)")
        pairs.append((src, dst))
    return tuple(pairs)


def _parse_step_if(raw: Any, where: str) -> StepIf:
    if raw is None:
        return StepIf()
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是对象")
    _warn_unknown(raw, _KNOWN_IF_KEYS, where)
    jumps = _parse_name_map(raw.get("jump"), f"{where}.jump")
    redirects = _parse_name_map(raw.get("redirect"), f"{where}.redirect")
    clash = {src for src, _ in jumps} & {src for src, _ in redirects}
    if clash:
        raise RecipeError(
            f"{where}: {', '.join(sorted(clash))} 同时出现在 jump 和 redirect 里，跳转冲突"
        )
    return StepIf(
        error=_parse_step_name(raw.get("error"), f"{where}.error"),
        success=_parse_step_name(raw.get("success"), f"{where}.success"),
        jump=jumps,
        redirect=redirects,
    )


def _parse_steps(raw: Any, where: str) -> tuple[Step, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是对象")
    steps: list[Step] = []
    for name, body in raw.items():
        step_where = f"{where}.{name}"
        if not isinstance(body, dict):
            raise RecipeError(f"{step_where} 必须是对象")
        _warn_unknown(body, _KNOWN_STEP_KEYS, step_where)
        linkup = _parse_step_names(body.get("linkup"), f"{step_where}.linkup")
        if name in linkup:
            raise RecipeError(f"{step_where}.linkup 不能联动自己")
        if len(set(linkup)) != len(linkup):
            raise RecipeError(f"{step_where}.linkup 里有重复的步骤")
        steps.append(
            Step(
                name=name,
                run=_parse_run(body.get("run"), f"{step_where}.run"),
                call=_parse_call(body.get("call"), f"{step_where}.call"),
                skip=_parse_bool(body.get("skip"), f"{step_where}.skip"),
                errskip=_parse_level(
                    body.get("errskip"),
                    f"{step_where}.errskip",
                    STEP_ERR_LEVELS,
                    DEFAULT_STEP_ERRSKIP,
                ),
                linkup=linkup,
                jump=_parse_step_name(body.get("jump"), f"{step_where}.jump"),
                next=_parse_next(body.get("next"), f"{step_where}.next", "next" in body),
                when=_parse_step_if(body.get("if"), f"{step_where}.if"),
            )
        )
    return tuple(steps)


def _parse_jobs(raw: Any, where: str) -> tuple[Job, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise RecipeError(f"{where} 必须是对象")
    jobs: list[Job] = []
    for name, body in raw.items():
        job_where = f"{where}.{name}"
        if not isinstance(body, dict):
            raise RecipeError(f"{job_where} 必须是对象")
        _warn_unknown(body, _KNOWN_JOB_KEYS, job_where)
        system = body.get("system")
        if system is not None and not isinstance(system, str):
            raise RecipeError(f"{job_where}.system 必须是字符串")
        linkup = _parse_job_linkup(body.get("linkup"), f"{job_where}.linkup")
        if name in linkup:
            raise RecipeError(f"{job_where}.linkup 不能联动自己")
        if len(set(linkup)) != len(linkup):
            raise RecipeError(f"{job_where}.linkup 里有重复的作业")
        jobs.append(
            Job(
                name=name,
                system=system or None,
                steps=_parse_steps(body.get("steps"), f"{job_where}.steps"),
                skip=_parse_bool(body.get("skip"), f"{job_where}.skip"),
                linkup=linkup,
                errskip=_parse_level(
                    body.get("errskip"),
                    f"{job_where}.errskip",
                    JOB_ERR_LEVELS,
                    DEFAULT_JOB_ERRSKIP,
                ),
            )
        )
    names = {job.name for job in jobs}
    for job in jobs:
        for target in job.linkup:
            if target not in names:
                raise RecipeError(f"jobs.{job.name}.linkup 联动的作业 {target!r} 不存在")
    return tuple(jobs)


def _parse_recipe(path: Path, raw: dict) -> Recipe:
    _warn_unknown(raw, _KNOWN_TOP_KEYS, str(path))
    return Recipe(
        path=path,
        material=_parse_material(raw.get("material"), "material"),
        downloads=_parse_download(raw.get("download"), "download"),
        unzips=_parse_unzip(raw.get("unzip"), "unzip"),
        data=tuple(raw.get("data") or ()),
        jobs=_parse_jobs(raw.get("jobs"), "jobs"),
    )


def bind_call_args(recipe: Recipe, args: list[Any] | tuple[Any, ...] | None) -> list[Any]:
    """把 call 传入的参数绑定到配方的 data 上。

    data 中的整数是入参占位符，表示"取 args 中第 N 个"。``args`` 为 None 表示
    配方是顶层执行的，data 原样返回。
    """
    if args is None:
        return list(recipe.data)
    bound: list[Any] = []
    for index, item in enumerate(recipe.data):
        if isinstance(item, int) and not isinstance(item, bool):
            if not 0 <= item < len(args):
                raise RecipeError(
                    f"{recipe.path.name} data[{index}] 的占位符 {item} 超出 call 传入参数范围"
                    f" (共 {len(args)} 个)"
                )
            bound.append(args[item])
        else:
            bound.append(item)
    return bound
