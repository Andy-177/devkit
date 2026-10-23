"""devkit 异常类型。"""

from __future__ import annotations


class DevkitError(Exception):
    """所有 devkit 错误的基类。"""


class RecipeError(DevkitError):
    """配方文件缺失、JSON 非法或字段不合法。"""


class ExpressionError(RecipeError):
    """表达式求值失败。

    这类错误必须立即终止整个工作流，不能被任何 ``errskip`` 策略吞掉。
    """


class CloneError(DevkitError):
    """git 克隆失败。"""


class DownloadError(DevkitError):
    """下载失败。"""


class UnzipError(DevkitError):
    """解压 zip 失败。"""


class InteractiveInputError(DevkitError):
    """并发运行期间有子进程试图读 stdin（交互式程序不许并发）。"""


class StepError(DevkitError):
    """工作流中某个步骤执行失败。"""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class JobStop(DevkitError):
    """步骤要求终止当前作业（``errskip: stop``），最终收场交给作业级 errskip。"""

    def __init__(self, job_name: str, step_name: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.job_name = job_name
        self.step_name = step_name
        self.cause = cause
