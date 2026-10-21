"""配方 unpack 段的"下载并解压"，是 download 和 unzip 的结合体。

zip 从网络下载到 ``<下载目录>/.dkcache`` 缓存，再解压释放到下载目录
（默认 ``.devkit``）：

* ``extract:true`` 时把压缩包里的内容直接铺到下载目录；
* 否则先在下载目录下建一个与压缩包同名的子目录，再解压进去。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from devkit.dl import download_one
from devkit.recipe import DownloadSpec, UnpackSpec
from devkit.unzip import _unzip_to, extract_root

#: 下载的压缩包缓存在下载目录下的这个子目录
_CACHE_SUBDIR = ".dkcache"


def unpack_one(
    spec: UnpackSpec,
    dest: Path,
    system: str,
    *,
    dry_run: bool = False,
) -> Path | None:
    """下载并解压一个 zip 到 ``dest``，返回解压目标目录；跳过时返回 None。

    ``dest`` 是下载目录，压缩包缓存到 ``dest/.dkcache``，解压释放到 ``dest``。
    """
    if not spec.matches(system):
        logger.info("跳过下载解压 (需要系统 {}): {}", "/".join(spec.systems or ()), spec.url)
        return None

    cache_dir = dest / _CACHE_SUBDIR
    archive = download_one(
        DownloadSpec(spec.url, spec.systems), cache_dir, system, dry_run=dry_run
    )
    if dry_run or archive is None:
        # dry-run 不落地也不解压；archive 为 None 表示下载被跳过
        return None

    target = extract_root(dest, archive, extract=spec.extract)
    logger.info("解压 {} -> {}", archive, target)
    _unzip_to(archive, target, spec.url)
    return target
