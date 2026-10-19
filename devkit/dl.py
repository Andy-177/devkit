"""构建所需工具链/资源的下载，基于 littledl。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from loguru import logger

from devkit.errors import DownloadError
from devkit.progress import ProgressBar, format_size
from devkit.recipe import DownloadSpec


def filename_for(url: str) -> str:
    """从链接推导本地文件名。"""
    name = Path(unquote(urlparse(url).path)).name
    return name or "download.bin"


def download_one(
    spec: DownloadSpec,
    dest: Path,
    system: str,
    *,
    dry_run: bool = False,
) -> Path | None:
    """下载单个链接到 ``dest``，返回落地文件；跳过时返回 None。"""
    name = filename_for(spec.url)
    target = dest / name
    if not spec.matches(system):
        logger.info("跳过下载 (需要系统 {}): {}", "/".join(spec.systems or ()), name)
        return None
    if target.exists():
        logger.info("已存在，跳过下载: {}", target)
        return target

    logger.info("下载 {} -> {}", spec.url, target)
    if dry_run:
        return None

    from littledl import download_file_sync

    dest.mkdir(parents=True, exist_ok=True)
    with ProgressBar(name) as bar:
        try:
            path = download_file_sync(spec.url, save_path=str(dest), progress_callback=bar)
        except Exception as exc:  # littledl 定义了自己的异常体系
            raise DownloadError(f"下载 {spec.url} 失败: {exc}") from exc
    if not path.exists():
        logger.warning("下载完成但未找到文件: {}", path)
    else:
        logger.info("下载完成: {} ({})", path.name, format_size(path.stat().st_size))
    return path
