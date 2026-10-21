"""配方 unzip 段的解压，基于标准库 zipfile。

zip 文件是相对路径，按下面的顺序查找: 配方文件所在目录 -> 克隆的 workspace 子目录
-> 工作区 -> 下载目录。解压结果统一落到下载目录（默认 ``.devkit``）：

* ``extract:true`` 时把压缩包里的内容直接铺到下载目录；
* 否则先在下载目录下建一个与压缩包同名的子目录，再解压进去。
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from loguru import logger

from devkit.errors import UnzipError
from devkit.progress import ProgressBar
from devkit.recipe import UnzipSpec

_CHUNK = 1024 * 256


def extract_root(dest: Path, archive: Path, *, extract: bool) -> Path:
    """按 ``extract`` 选项算出解压目标目录。

    ``extract`` 为 True 时直接返回 ``dest``，否则返回 ``dest / archive.stem``。
    """
    return dest if extract else dest / archive.stem


def resolve_zip(path: str, bases: Sequence[Path], *, strict: bool = True) -> Path:
    """把 unzip 里的路径解析成 zip 文件。

    ``strict`` 为 False 时（dry-run）不要求文件存在，只返回首选路径。
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        if candidate.is_file() or not strict:
            return candidate
        raise UnzipError(f"找不到 zip 文件: {candidate}")

    for base in bases:
        target = base / candidate
        if target.is_file():
            return target
    if not strict:
        return bases[0] / candidate
    tried = ", ".join(str(base / candidate) for base in bases)
    raise UnzipError(f"找不到 zip 文件: {path} (已尝试: {tried})")


def _extract_all(archive: zipfile.ZipFile, dest: Path, label: str, bar: ProgressBar) -> int:
    """逐条解压（边写边上报进度），解压前先做 zip-slip 检查。"""
    root = dest.resolve()
    members = archive.infolist()
    for member in members:
        target = (root / member.filename).resolve()
        if not target.is_relative_to(root):
            raise UnzipError(f"{label}: zip 里的 {member.filename!r} 解压后会跑到目标目录之外")

    total = sum(member.file_size for member in members)
    written = 0
    bar.update(0, total)
    for member in members:
        target = root / member.filename
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, open(target, "wb") as sink:
            while chunk := source.read(_CHUNK):
                sink.write(chunk)
                written += len(chunk)
                bar.update(written, total)
        _restore_mtime(target, member)
    bar.update(max(written, total), total)
    return written


def _restore_mtime(target: Path, member: zipfile.ZipInfo) -> None:
    """按 zip 里记录的时间设置文件修改时间。"""
    try:
        stamp = datetime(*member.date_time).timestamp()
        os.utime(target, (stamp, stamp))
    except (ValueError, OverflowError, OSError):
        pass


def unzip_one(
    spec: UnzipSpec,
    bases: Sequence[Path],
    dest: Path,
    system: str,
    *,
    dry_run: bool = False,
) -> Path | None:
    """解压一个本地 zip 到 ``dest``，返回目标目录；跳过时返回 None。"""
    if not spec.matches(system):
        logger.info("跳过解压 (需要系统 {}): {}", "/".join(spec.systems or ()), spec.file)
        return None

    source = resolve_zip(spec.file, bases, strict=not dry_run)
    target = extract_root(dest, source, extract=spec.extract)
    logger.info("解压 {} -> {}", source, target)
    if dry_run:
        return None

    _unzip_to(source, target, spec.file)
    return target


def _unzip_to(source: Path, target: Path, label: str) -> None:
    """把 ``source`` 解压到 ``target``，进度条以 zip 名显示。"""
    try:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as archive, ProgressBar(source.name) as bar:
            _extract_all(archive, target, label, bar)
    except zipfile.BadZipFile as exc:
        raise UnzipError(f"{source} 不是合法的 zip 文件: {exc}") from exc
    except OSError as exc:
        raise UnzipError(f"解压 {source} 失败: {exc}") from exc
