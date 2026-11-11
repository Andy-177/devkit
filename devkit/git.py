"""配方 ``clone`` 字段的 git 克隆，基于 dulwich.porcelain.clone。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from loguru import logger

from devkit.errors import CloneError
from devkit.recipe import Clone

_STAGING_SUFFIX = ".devkit-clone"


def ensure_clone(clone: Clone, target: Path) -> Path:
    """确保 ``clone`` 配置的仓库被克隆到 ``target``，返回工作目录。

    * ``target`` 已是 git 工作区 -> 跳过克隆（方便重复执行）；
    * ``target`` 为空目录或不存在 -> 执行克隆；
    * ``branch`` 为空/null -> 克隆默认分支；
    * ``depth`` 为 0/null -> 克隆完整历史；
    * ``commit`` 存在 -> 克隆后检出该提交。

    克隆先落在临时目录，成功后才改名到 ``target``，因此克隆失败不会留下
    半成品工作区。
    """
    from dulwich import porcelain

    if (target / ".git").exists():
        logger.info("已存在克隆，跳过: {}", target)
        return target

    if target.exists():
        if not target.is_dir():
            raise CloneError(f"工作目录被同名文件占用: {target}")
        if any(target.iterdir()):
            raise CloneError(f"工作目录非空且不是 git 仓库: {target}")
        target.rmdir()

    kwargs: dict[str, object] = {}
    if clone.branch:
        kwargs["branch"] = clone.branch
    if clone.depth:
        kwargs["depth"] = clone.depth

    logger.info("克隆 {} -> {}", clone.repo, target)
    logger.info("  分支: {}", clone.branch or "默认分支")
    logger.info("  深度: {}", f"{clone.depth} (浅克隆)" if clone.depth else "完整历史")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / (target.name + _STAGING_SUFFIX)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        repo = porcelain.clone(clone.repo, str(staging), **kwargs)  # type: ignore[arg-type]
        try:
            if clone.commit:
                porcelain.checkout(repo, clone.commit)
                logger.info("  已检出提交: {}", clone.commit)
        finally:
            repo.close()
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise CloneError(f"克隆 {clone.repo} 失败: {exc}") from exc

    os.replace(staging, target)
    return target
