"""配方执行引擎: material 克隆 -> download 下载 -> jobs 工作流。

工作目录约定:

* ``--workdir``（默认 ``./.devkit``）是配方的工作区，``run`` 里的指令在这里
  执行；
* 顶层配方若自带 material，克隆结果落在工作区下的 ``workspace`` 子目录；
  被 ``call`` 的配方若自带 material，则在父工作区下用配方文件名（去掉扩展名）
  开一个子目录，克隆结果落在该子目录的 ``workspace`` 里，避免互相覆盖；没有
  material 的被调用配方与父配方共用工作区；
* 下载文件不放进工作区，统一落在下载目录 ``--download-dir``（默认 ``./.devkit``，
  与默认工作区相同），配方里用 ``<文件名>`` 这类相对路径引用；
* ``unzip`` 解压出的内容也统一落到下载目录，zip 自身按"配方目录 ->
  克隆的 workspace 子目录 -> 工作区 -> 下载目录"的顺序查找；
* ``unpack`` 是 download 与 unzip 的结合体：zip 先下载到 ``<下载目录>/.dkcache``
  缓存，再解压释放到下载目录。

执行顺序与步骤控制:

* 固定顺序: ``material`` 克隆 -> ``download``（按列表顺序）-> ``unzip``（按列表顺序）
  -> ``unpack``（按列表顺序）-> ``jobs``（按声明顺序逐个执行），没有集中式编排；
* 步骤默认按声明顺序执行，控制字段（``skip``/``errskip``/``linkup``/``jump``/``next``/
  ``if``）的语义见 ``devkit.recipe`` 的说明；失败等级 ``errskip``（``crash``/``stop``/
  ``proceed``/``skip``，缺省 ``stop``）决定失败的爆炸半径；
* ``linkup`` 会把发起联动的步骤和被联动的步骤并发执行；联动组里任何一个步骤失败，
  按发起联动的那个步骤的 ``errskip`` 处理：``proceed`` 时联动的步骤继续跑完，
  ``skip``/``stop``/``crash`` 时整个组一起被终止；
* 作业也有 ``skip``/``linkup``/``errskip``：``skip:true`` 的作业顺序走到时跳过（被
  ``next``/``linkup`` 指到时仍执行），``linkup`` 把发起联动的作业和被联动的作业并发
  执行；作业失败按 ``errskip``（``crash``/``proceed``/``skip``，缺省 ``proceed``）处理；
* 被 errskip 处理掉的失败会让整个构建最终以非 0 退出码结束（``RecipeRunner.failed``）；
* 联动期间子进程的 stdin 不是终端而是空行文件：只要有并发子进程读 stdin
  （说明它是交互式的），立刻杀掉整个联动组并报错，逼开发者把交互式程序单独串行执行。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from loguru import logger

from devkit import guard
from devkit.dl import download_one
from devkit.errors import (
    ExpressionError,
    InteractiveInputError,
    JobStop,
    RecipeError,
    StepError,
)
from devkit.expr import (
    get_builtin_namespace,
    get_effective_expression,
    has_expression,
    should_eval_expression,
    substitute_expression,
    substitute_in_value,
)
from devkit.git import ensure_clone
from devkit.recipe import (
    JOB_ERR_CRASH,
    JOB_ERR_PROCEED,
    JOB_ERR_SKIP,
    NEXT_JOB,
    RECIPE_NAMES,
    STEP_ERR_CRASH,
    STEP_ERR_PROCEED,
    STEP_ERR_STOP,
    EnvEntry,
    Job,
    Recipe,
    Step,
    bind_call_args,
    load_recipe,
    resolve_recipe_path,
)
from devkit.system import detect_system
from devkit.unpack import unpack_one
from devkit.unzip import unzip_one

#: 默认工作区（run 指令的执行目录）
DEFAULT_WORKDIR = Path(".devkit")
#: 默认下载目录（所有下载的文件都放这里）
DEFAULT_DOWNLOAD_DIR = Path(".devkit")

#: 克隆结果落在工作区下的这个子目录里
_CLONE_SUBDIR = "workspace"

#: 并发执行时让子进程脱离控制台输入：交互式程序会直接报错而不是卡住（仅 Windows）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _quote(text: str) -> str:
    return f'"{text}"' if " " in text else text


class RecipeRunner:
    """按配方执行构建工作流。"""

    def __init__(
        self,
        workdir: Path | str,
        system: str | None = None,
        *,
        download_dir: Path | str | None = None,
        dry_run: bool = False,
        jobs: Sequence[str] | None = None,
    ) -> None:
        self.workdir = Path(workdir).expanduser()
        self.download_dir = (
            Path(download_dir).expanduser() if download_dir is not None else DEFAULT_DOWNLOAD_DIR
        )
        self.system = system or detect_system()
        self.dry_run = dry_run
        self.job_filter = set(jobs) if jobs else None
        #: 是否有失败被 errskip 策略处理掉（构建最终仍以非 0 退出码结束）
        self.failed = False
        #: 作业 system 表达式的求值缓存（避免同一次构建里重复求值）
        self._job_system_cache: dict[tuple[Path, str], Any] = {}

    # ------------------------------------------------------------------ 入口

    def run(self, recipe_path: Path | str) -> None:
        """执行一个配方（顶层调用）。"""
        self._run_recipe(
            resolve_recipe_path(recipe_path),
            args=None,
            workdir=self.workdir,
            stack=(),
            top=True,
            env=None,
        )

    def _get_expr_ns(self, recipe: Recipe):
        return get_builtin_namespace(recipe.path, self.system)

    def _effective_expr(self, recipe: Recipe, job: Job | None = None, step: Step | None = None):
        return get_effective_expression(
            recipe.expression,
            job.expression if job is not None else None,
            step.expression if step is not None else None,
        )

    def _job_system(self, recipe: Recipe, job: Job) -> Any:
        """作业的 system 过滤值；表达式只求值一次并缓存。"""
        key = (recipe.path, job.name)
        if key not in self._job_system_cache:
            system = job.system
            if isinstance(system, str) and has_expression(system):
                system = self._eval_value(system, recipe, job, None, "system")
            self._job_system_cache[key] = system
        return self._job_system_cache[key]

    def _eval_value(
        self,
        value: Any,
        recipe: Recipe,
        job: Job | None = None,
        step: Step | None = None,
        feature: str = "",
    ) -> Any:
        """按生效的 expression 配置递归替换值中的表达式。"""
        if not should_eval_expression(self._effective_expr(recipe, job, step), feature):
            return value
        return substitute_in_value(value, self._get_expr_ns(recipe), True)

    def _eval_systems(
        self, systems: tuple[str, ...] | None, ns: dict[str, Any]
    ) -> tuple[str, ...] | None:
        """替换 system 过滤项里的表达式；结果可以是单个字符串或字符串列表。"""
        if not systems:
            return systems
        result: list[str] = []
        for value in systems:
            if not has_expression(value):
                result.append(value)
                continue
            evaluated = substitute_expression(value, ns)
            if isinstance(evaluated, (list, tuple)):
                result.extend(str(item) for item in evaluated)
            else:
                result.append(str(evaluated))
        return tuple(result)

    def _eval_env_entries(
        self,
        entries: tuple[EnvEntry, ...],
        recipe: Recipe,
        effective,
        feature: str,
    ) -> tuple[EnvEntry, ...]:
        """替换 env/area 项的 value（以及可选的 system 过滤）；值统一转成字符串。"""
        eval_value = should_eval_expression(effective, feature)
        eval_system = should_eval_expression(effective, "system")
        if not entries or not (eval_value or eval_system):
            return entries
        ns = self._get_expr_ns(recipe)
        result: list[EnvEntry] = []
        for entry in entries:
            systems = entry.systems
            if eval_system and systems is not None:
                systems = self._eval_systems(systems, ns)
            value = entry.value
            # 只对当前系统生效的项求值；给其他系统准备的表达式不应影响本次构建。
            if (
                eval_value
                and (systems is None or self.system in systems)
                and has_expression(value)
            ):
                value = str(substitute_expression(value, ns))
            result.append(replace(entry, value=value, systems=systems))
        return tuple(result)

    def _resolve_env(
        self,
        recipe: Recipe,
        job: Job,
        step: Step,
        inherited: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """按配方 -> 作业 -> 步骤逐级叠加 env/area。

        表达式按"当前步骤最终生效的 expression 配置"求值，因此上层开启的表达式
        可以被下层的 ``false`` 关闭，下层也能重新开启。
        """
        effective = self._effective_expr(recipe, job, step)
        result = dict(inherited) if inherited else {}
        for env, area in (
            (recipe.env, recipe.area),
            (job.env, job.area),
            (step.env, step.area),
        ):
            env = self._eval_env_entries(env, recipe, effective, "env")
            if area is not None:
                area = self._eval_env_entries(area, recipe, effective, "area")
                result = self._merge_env({}, area)
            else:
                result = self._merge_env(result, env)
        return result

    def _eval_str(self, value: str, ns: dict[str, Any]) -> str:
        if not has_expression(value):
            return value
        return str(substitute_expression(value, ns))

    def _merge_env(
        self, base: dict[str, str], entries: tuple[EnvEntry, ...]
    ) -> dict[str, str]:
        for entry in entries:
            if entry.matches(self.system):
                base[entry.key] = entry.value
        return base

    # ----------------------------------------------------------------- 配方

    def _run_recipe(
        self,
        path: Path,
        *,
        args: list[Any] | None,
        workdir: Path,
        stack: tuple[Path, ...],
        top: bool,
        env: dict[str, str] | None = None,
    ) -> None:
        path = path.resolve()
        if path in stack:
            chain = " -> ".join(p.name for p in stack + (path,))
            raise RecipeError(f"配方循环调用: {chain}")

        recipe = load_recipe(path)
        logger.info("== 配方: {}", recipe.path)

        cwd = self._prepare(recipe, workdir=workdir, top=top)
        data = bind_call_args(recipe, args)
        data = self._eval_value(data, recipe, feature="data")

        self._run_works(recipe, data=data, cwd=cwd, stack=stack, top=top, env=env)

    def _prepare(self, recipe: Recipe, *, workdir: Path, top: bool) -> Path:
        """计算并创建该配方的工作目录。"""
        if top or recipe.material is None:
            cwd = workdir
        else:
            cwd = workdir / recipe.path.stem
        cwd.mkdir(parents=True, exist_ok=True)
        return cwd

    # ------------------------------------------------------------- 顶层工作

    def _run_works(
        self,
        recipe: Recipe,
        *,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        top: bool,
        env: dict[str, str] | None = None,
    ) -> None:
        """固定顺序: 克隆 -> 下载 -> 解压 -> 下载解压 -> 工作流。"""
        if recipe.material is not None:
            self._clone(recipe, cwd)
        self._run_downloads(recipe)
        self._run_unzips(recipe, cwd)
        self._run_unpacks(recipe)
        self._run_jobs(recipe, data=data, cwd=cwd, stack=stack, top=top, env=env)

    def _clone(self, recipe: Recipe, cwd: Path) -> None:
        assert recipe.material is not None
        material = recipe.material
        if should_eval_expression(self._effective_expr(recipe), "material"):
            ns = self._get_expr_ns(recipe)
            material = replace(
                material,
                repo=self._eval_str(material.repo, ns),
                branch=(
                    self._eval_str(material.branch, ns)
                    if material.branch is not None
                    else None
                ),
                commit=(
                    self._eval_str(material.commit, ns)
                    if material.commit is not None
                    else None
                ),
            )
        target = cwd / _CLONE_SUBDIR
        if self.dry_run:
            logger.info("dry-run: 跳过克隆 {} -> {}", material.repo, target)
            return
        ensure_clone(material, target)

    def _eval_specs(self, specs, recipe: Recipe, feature: str, field: str):
        """替换 download/unzip/unpack 项的 url/file 与 system 过滤。"""
        effective = self._effective_expr(recipe)
        eval_value = should_eval_expression(effective, feature)
        eval_system = should_eval_expression(effective, "system")
        if not specs or not (eval_value or eval_system):
            return specs
        ns = self._get_expr_ns(recipe)
        result = []
        for spec in specs:
            systems = spec.systems
            if eval_system and systems is not None:
                systems = self._eval_systems(systems, ns)
            value = getattr(spec, field)
            if (
                eval_value
                and (systems is None or self.system in systems)
                and has_expression(value)
            ):
                value = self._eval_str(value, ns)
            result.append(replace(spec, **{field: value, "systems": systems}))
        return tuple(result)

    def _run_downloads(self, recipe: Recipe) -> None:
        specs = self._eval_specs(recipe.downloads, recipe, "download", "url")
        for spec in specs:
            download_one(spec, self.download_dir, self.system, dry_run=self.dry_run)

    def _run_unzips(self, recipe: Recipe, cwd: Path) -> None:
        specs = self._eval_specs(recipe.unzips, recipe, "unzip", "file")
        bases = (recipe.dir, cwd / _CLONE_SUBDIR, cwd, self.download_dir)
        for spec in specs:
            unzip_one(spec, bases, self.download_dir, self.system, dry_run=self.dry_run)

    def _run_unpacks(self, recipe: Recipe) -> None:
        specs = self._eval_specs(recipe.unpacks, recipe, "unpack", "url")
        for spec in specs:
            unpack_one(spec, self.download_dir, self.system, dry_run=self.dry_run)

    # ------------------------------------------------------------------ job

    def _run_jobs(
        self,
        recipe: Recipe,
        *,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        top: bool,
        env: dict[str, str] | None = None,
    ) -> None:
        """按声明顺序执行作业；``next`` 可以跳到别的作业，``linkup`` 可以并发联动。"""
        jobs = list(recipe.jobs)
        index_of = {job.name: pos for pos, job in enumerate(jobs)}
        by_name = {job.name: job for job in jobs}
        cursor = 0
        explicit = False  # 是不是被 next 明确指过来的（是的话 skip 不生效）
        while cursor < len(jobs):
            job = jobs[cursor]
            if job.skip and not explicit:
                logger.info("-- 跳过工作流 {} (skip)", job.name)
                cursor += 1
                continue
            explicit = False
            if top and self.job_filter is not None and job.name not in self.job_filter:
                logger.info("-- 跳过工作流 {} (未被 --job 选中)", job.name)
                cursor += 1
                continue
            job_system = self._job_system(recipe, job)
            if job_system is not None and job_system != self.system:
                logger.info("-- 跳过工作流 {} (需要系统 {}，当前 {})", job.name, job_system, self.system)
                cursor += 1
                continue
            linked = [by_name[name] for name in job.linkup]
            target = self._run_job_group(
                job,
                linked,
                recipe=recipe,
                data=data,
                cwd=cwd,
                stack=stack,
                job_names=set(index_of),
                env=env,
            )
            if target is None:
                cursor += 1
                continue
            pos = index_of.get(target)
            if pos is None:
                raise RecipeError(
                    f"{recipe.path.name} 工作流 {job.name}: next 指向的作业 {target!r} 不存在"
                )
            logger.info("> 切换到工作流 {}", target)
            cursor = pos
            explicit = True

    def _run_job_group(
        self,
        job: Job,
        linked: list[Job],
        *,
        recipe: Recipe,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        job_names: set[str],
        env: dict[str, str] | None = None,
    ) -> str | None:
        """执行一个作业；有 linkup 时和联动的作业并发执行。

        失败时按发起联动的作业的 ``errskip`` 收场（缺省 proceed）：
        ``crash`` 中止整个构建，``proceed`` 让联动的作业跑完再继续，
        ``skip`` 强行终止整个联动组。
        返回发起联动的作业的 next 目标（被联动的作业的 next 不生效）。
        """
        if not linked:
            try:
                return self._run_job(
                    job,
                    recipe=recipe,
                    data=data,
                    cwd=cwd,
                    stack=stack,
                    job_names=job_names,
                    env=env,
                )
            except JobStop as exc:
                return self._handle_job_failure(job, exc, recipe=recipe)

        members = [job, *linked]
        names = ", ".join(member.name for member in members)
        logger.info("  || 联动运行作业: {}", names)

        results: dict[str, str | None] = {}
        failures: list[tuple[Job, BaseException]] = []
        with guard.guarded() as stdin_guard:
            with ThreadPoolExecutor(max_workers=len(members)) as pool:
                futures = {
                    pool.submit(
                        self._run_job,
                        member,
                        recipe=recipe,
                        data=data,
                        cwd=cwd,
                        stack=stack,
                        job_names=job_names,
                        env=env,
                    ): member
                    for member in members
                }
                for future in as_completed(futures):
                    member = futures[future]
                    try:
                        results[member.name] = future.result()
                    except BaseException as exc:  # noqa: BLE001
                        failures.append((member, exc))
                        if job.errskip != JOB_ERR_PROCEED:
                            stdin_guard.kill_all()  # 联动组一起失败

        if stdin_guard.hit is not None:
            raise InteractiveInputError(
                f"{recipe.path.name} 联动组 {names}: 期间 {stdin_guard.hit} 读了 stdin，"
                "并发运行期间不许交互式程序，请把它单独串行执行（不要和别的作业联动）"
            )
        if failures:
            # 表达式错误优先级最高，不能被其他成员的普通失败掩盖。
            for _member, exc in failures:
                if isinstance(exc, ExpressionError):
                    return self._handle_job_failure(job, exc, recipe=recipe)
            for member, exc in failures:  # 优先抛发起联动的作业的异常
                if member is job:
                    return self._handle_job_failure(job, exc, recipe=recipe)
            return self._handle_job_failure(job, failures[0][1], recipe=recipe)
        return results.get(job.name)

    def _handle_job_failure(self, job: Job, exc: BaseException, *, recipe: Recipe) -> None:
        """作业失败后按 ``job.errskip`` 收场；``proceed``/``skip`` 返回 None 继续下一个作业。"""
        if isinstance(exc, ExpressionError):
            # 表达式错误必须强制终止整个工作流，不能被 errskip 吞掉。
            logger.error("作业 {} 表达式求值失败，终止整个构建", job.name)
            raise exc
        if job.errskip == JOB_ERR_CRASH:
            logger.error("作业 {} 失败 (errskip:crash): {}", job.name, exc)
            raise exc.cause if isinstance(exc, JobStop) else exc
        self.failed = True
        if job.errskip == JOB_ERR_SKIP:
            logger.error("作业 {} 失败 (errskip:skip): 终止它和联动的作业，继续下一个作业", job.name)
        else:
            logger.error(
                "作业 {} 失败 (errskip:{}): 停止它，联动的作业跑完后继续下一个作业",
                job.name,
                job.errskip,
            )
        return None

    def _run_job(
        self,
        job: Job,
        *,
        recipe: Recipe,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        job_names: set[str],
        env: dict[str, str] | None = None,
    ) -> str | None:
        """执行一个作业，返回要跳转到的作业名（None 表示继续下一个作业）。"""
        job_system = self._job_system(recipe, job)
        if job_system is not None and job_system != self.system:
            logger.info("-- 跳过工作流 {} (需要系统 {}，当前 {})", job.name, job_system, self.system)
            return None
        logger.info("-- 工作流: {}", job.name)
        steps = list(job.steps)
        index_of = {step.name: pos for pos, step in enumerate(steps)}
        jumps, redirects = self._flow_tables(
            steps, index_of, job_names, where=f"{recipe.path.name} 工作流 {job.name}"
        )

        cursor = 0
        explicit = False  # 是不是被 jump/redirect 明确指过来的（是的话 skip 不生效）
        while cursor < len(steps):
            step = steps[cursor]
            redirect = redirects.get(step.name)
            if redirect is not None:
                logger.info("  > 重定向 {} -> {}", step.name, redirect)
                cursor = index_of[redirect]
                explicit = True
                continue
            if step.skip and not explicit:
                logger.info("  - 跳过步骤 {}", step.name)
                cursor += 1
                continue
            explicit = False
            step_system = step.system
            if isinstance(step_system, str) and has_expression(step_system):
                step_system = self._eval_value(step_system, recipe, job, step, "system")
                if isinstance(step_system, str) and step_system != self.system:
                    logger.info("  - 跳过步骤 {} (需要系统 {}，当前 {})", step.name, step_system, self.system)
                    cursor += 1
                    continue
            elif isinstance(step.system, str) and step.system != self.system:
                logger.info("  - 跳过步骤 {} (需要系统 {}，当前 {})", step.name, step.system, self.system)
                cursor += 1
                continue

            linked = [steps[index_of[name]] for name in step.linkup]
            try:
                self._run_step(
                    step,
                    linked=linked,
                    recipe=recipe,
                    job=job,
                    data=data,
                    cwd=cwd,
                    stack=stack,
                    env=env,
                )
            except InteractiveInputError:
                raise
            except StepError as exc:
                if step.errskip == STEP_ERR_CRASH:
                    logger.error("步骤 {} 失败 (errskip:crash): {}", step.name, exc)
                    raise
                if step.errskip == STEP_ERR_STOP:
                    logger.error("步骤 {} 失败 (errskip:stop): 终止作业 {}", step.name, job.name)
                    raise JobStop(job.name, step.name, exc) from exc
                self.failed = True
                target = step.when.error or step.jump or jumps.get(step.name)
                if target is not None:
                    logger.error(
                        "步骤 {} 失败 (errskip:{}): 跳转到 {}", step.name, step.errskip, target
                    )
                    cursor = index_of[target]
                    explicit = True
                    continue
                logger.error(
                    "步骤 {} 失败 (errskip:{}): 继续下一个步骤", step.name, step.errskip
                )
                cursor += 1
                continue

            if step.next is not None:
                if step.next == NEXT_JOB:
                    logger.info("  > 步骤 {} 的 next: 结束工作流 {}", step.name, job.name)
                    return None
                logger.info("  > 步骤 {} 的 next: 切换到工作流 {}", step.name, step.next)
                return step.next
            target = step.when.success or step.jump or jumps.get(step.name)
            if target is not None:
                logger.info("  > 步骤 {} 跳转到 {}", step.name, target)
                cursor = index_of[target]
                explicit = True
                continue
            cursor += 1

        logger.info("-- 工作流 {} 完成", job.name)
        return None

    def _flow_tables(
        self,
        steps: list[Step],
        index_of: dict[str, int],
        job_names: set[str],
        *,
        where: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """校验并汇总步骤的控制字段，返回 (跳转表, 重定向表)。"""

        def check_step(name: str, field: str) -> None:
            if name not in index_of:
                raise RecipeError(f"{where}: {field} 引用了不存在的步骤 {name!r}")

        jumps: dict[str, str] = {}
        redirects: dict[str, str] = {}
        for step in steps:
            if step.jump is not None:
                check_step(step.jump, f"步骤 {step.name} 的 jump")
            if step.when.success is not None:
                check_step(step.when.success, f"步骤 {step.name} 的 if.success")
            if step.when.error is not None:
                check_step(step.when.error, f"步骤 {step.name} 的 if.error")
            if step.next is not None and step.next != NEXT_JOB and step.next not in job_names:
                raise RecipeError(
                    f"{where}: 步骤 {step.name} 的 next 引用了不存在的作业 {step.next!r}"
                )
            for name in step.linkup:
                check_step(name, f"步骤 {step.name} 的 linkup")
            for src, dst in step.when.jump:
                check_step(src, f"步骤 {step.name} 的 if.jump 键")
                check_step(dst, f"步骤 {step.name} 的 if.jump 值")
                if src in redirects:
                    raise RecipeError(f"{where}: {src!r} 同时出现在 jump 和 redirect 里，跳转冲突")
                jumps[src] = dst
            for src, dst in step.when.redirect:
                check_step(src, f"步骤 {step.name} 的 if.redirect 键")
                check_step(dst, f"步骤 {step.name} 的 if.redirect 值")
                if src in jumps:
                    raise RecipeError(f"{where}: {src!r} 同时出现在 jump 和 redirect 里，跳转冲突")
                redirects[src] = dst
        return jumps, redirects

    # ----------------------------------------------------------------- step

    # ----------------------------------------------------------------- step

    def _run_step(
        self,
        step: Step,
        *,
        linked: list[Step],
        recipe: Recipe,
        job: Job,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        env: dict[str, str] | None = None,
    ) -> None:
        """执行一个步骤；有 linkup 时和联动的步骤并发执行。"""
        if not linked:
            self._run_step_own(
                step, recipe=recipe, job=job, data=data, cwd=cwd, stack=stack, env=env
            )
            return

        members = [step, *linked]
        names = ", ".join(member.name for member in members)
        logger.info("  || 联动运行: {}", names)

        failures: list[tuple[Step, BaseException]] = []
        with guard.guarded() as stdin_guard:
            with ThreadPoolExecutor(max_workers=len(members)) as pool:
                futures = {
                    pool.submit(
                        self._run_step_own,
                        member,
                        recipe=recipe,
                        job=job,
                        data=data,
                        cwd=cwd,
                        stack=stack,
                        env=env,
                    ): member
                    for member in members
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except BaseException as exc:  # noqa: BLE001
                        failures.append((futures[future], exc))
                        # proceed 时联动的步骤继续跑完，其余等级都整组终止
                        if step.errskip != STEP_ERR_PROCEED:
                            stdin_guard.kill_all()

        if stdin_guard.hit is not None:
            raise InteractiveInputError(
                f"{recipe.path.name} 联动组 {names}: 期间 {stdin_guard.hit} 读了 stdin，"
                "说明它是交互式程序；已终止整个联动组。请把它单独串行执行（不要和别的步骤联动）。"
            )
        if failures:
            # 表达式错误优先级最高：即使联动的其他成员只是普通失败，也必须整组中止。
            for _member, exc in failures:
                if isinstance(exc, ExpressionError):
                    raise exc
            for member, exc in failures:  # 失败策略用发起联动的那个步骤的
                if member is step:
                    raise exc
            raise failures[0][1]

    def _run_step_own(
        self,
        step: Step,
        *,
        recipe: Recipe,
        job: Job,
        data: list[Any],
        cwd: Path,
        stack: tuple[Path, ...],
        env: dict[str, str] | None = None,
    ) -> None:
        logger.info("  * 步骤: {}", step.name)

        step_env = self._resolve_env(recipe, job, step, inherited=env)
        if step_env:
            logger.info("    环境: {}", "; ".join(f"{k}={v}" for k, v in step_env.items()))

        effective_expr = self._effective_expr(recipe, job, step)
        eval_run = should_eval_expression(effective_expr, "run")
        eval_call = should_eval_expression(effective_expr, "call")
        ns = self._get_expr_ns(recipe) if (eval_run or eval_call) else None

        for command in step.run:
            parts = [self._subst(token, data, recipe, step) for token in command]
            if eval_run:
                parts = [substitute_in_value(p, ns, True) for p in parts]
            if not parts or not str(parts[0]):
                raise RecipeError(f"{recipe.path.name} 步骤 {step.name}: 指令不能为空")
            line = " ".join(str(part) for part in parts)
            self._exec_shell(line, cwd=cwd, recipe=recipe, step=step, env=step_env)

        for rel_path, args in step.call:
            if eval_call:
                rel_path = str(substitute_in_value(rel_path, ns, True))
                args = substitute_in_value(args, ns, True)
            target = (recipe.dir / rel_path).resolve()
            if target.is_dir():
                for name in RECIPE_NAMES:
                    candidate = target / name
                    if candidate.is_file():
                        target = candidate
                        break
                else:
                    raise RecipeError(
                        f"{recipe.path.name} 步骤 {step.name}: 目录 {target} 中没有配方文件"
                    )
            logger.info(
                "  * 调用 {} args={}", target, json.dumps(list(args), ensure_ascii=False)
            )
            self._run_recipe(
                target,
                args=list(args),
                workdir=cwd,
                stack=stack + (recipe.path,),
                top=False,
                env=step_env,
            )

    # ----------------------------------------------------------------- 工具

    def _subst(self, token: Any, data: list[Any], recipe: Recipe, step: Step) -> Any:
        """把 run 中的数字下标替换成 data 中的数据。"""
        if isinstance(token, bool) or not isinstance(token, int):
            return token
        if not 0 <= token < len(data):
            raise RecipeError(
                f"{recipe.path.name} 步骤 {step.name}: data 下标 {token} 越界"
                f" (data 共 {len(data)} 项)"
            )
        value = data[token]
        if value is None:
            raise RecipeError(
                f"{recipe.path.name} 步骤 {step.name}: data[{token}] 为 null，不能作为参数"
            )
        return value

    def _exec(self, argv: list[Any], *, cwd: Path, recipe: Recipe, step: Step) -> None:
        if not argv or not str(argv[0]):
            raise RecipeError(f"{recipe.path.name} 步骤 {step.name}: 指令不能为空")

        pretty = " ".join(str(a) for a in argv)
        logger.info("    $ {}   (cwd: {}, shell)", pretty, cwd)
        if self.dry_run:
            return

        returncode = self._spawn(pretty, cwd=cwd, recipe=recipe, step=step, pretty=pretty)
        if returncode != 0:
            raise StepError(
                f"{recipe.path.name} 步骤 {step.name}: 指令失败 (退出码 {returncode}): {pretty}",
                returncode=returncode,
            )

    def _exec_shell(
        self,
        line: str,
        *,
        cwd: Path,
        recipe: Recipe,
        step: Step,
        env: dict[str, str] | None = None,
    ) -> None:
        """执行一条 shell 命令行（由命令片段拼成，或来自无法解析成程序的命令）。"""
        logger.info("    $ {}   (cwd: {}, shell)", line, cwd)
        if self.dry_run:
            return

        returncode = self._spawn(line, cwd=cwd, recipe=recipe, step=step, pretty=line, env=env)
        if returncode != 0:
            raise StepError(
                f"{recipe.path.name} 步骤 {step.name}: 指令失败 (退出码 {returncode}): {line}",
                returncode=returncode,
            )

    def _spawn(
        self,
        command: str | list[str],
        *,
        cwd: Path,
        recipe: Recipe,
        step: Step,
        pretty: str,
        env: dict[str, str] | None = None,
    ) -> int:
        """启动指令并等它结束，返回退出码。

        ``command`` 是字符串时交给系统 shell 执行，是列表时直接启动。
        ``env`` 是配方声明的环境变量增量，会叠加在进程自身环境之上。
        并发运行（联动）期间用 ``guard`` 的哨兵 stdin（并让子进程脱离控制台输入），
        这样交互式程序不会卡住整个构建，而是被守卫发现后整组杀掉。
        """
        shell = isinstance(command, str)
        display = command if shell else command[0]
        full_env = {**os.environ, **env} if env else None
        stdin_guard = guard.current()
        if stdin_guard is None:
            try:
                return subprocess.run(
                    command, cwd=str(cwd), shell=shell, env=full_env
                ).returncode
            except OSError as exc:
                raise StepError(
                    f"{recipe.path.name} 步骤 {step.name}: 无法执行 {display}: {exc}"
                ) from exc

        stream = stdin_guard.new_stdin()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=stream,
                shell=shell,
                creationflags=_NO_WINDOW,
                env=full_env,
            )
        except OSError as exc:
            stream.close()
            raise StepError(
                f"{recipe.path.name} 步骤 {step.name}: 无法执行 {display}: {exc}"
            ) from exc
        stdin_guard.track(proc, stream, pretty)
        return proc.wait()

    @staticmethod
    def _is_program(token: str, cwd: Path) -> bool:  # pragma: no cover
        # 统一走 shell：此方法已不再使用
        return False

    @staticmethod
    def _resolve_program(program: str, cwd: Path) -> str:
        """优先把相对路径解析成工作目录下的绝对路径，找不到就交给 PATH。"""
        candidate = Path(program)
        if candidate.is_absolute():
            return program
        local = cwd / candidate
        if local.exists():
            return str(local)
        return program
