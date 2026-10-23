"""表达式求值: ``?!{{python_expression}}`` 运行时替换。

表达式可以在任意字符串里出现，运行时由 :func:`substitute_in_value` 递归替换。
整串只含一个表达式时返回表达式结果的原始类型，否则把各段结果 ``str()`` 后拼接。
表达式的命名空间包含完整 builtins（可以 ``import``），另有两个内置函数:

* ``get_recipe_path()``: 当前配方的路径（字符串）；
* ``get_system_info()``: 当前系统标识（如 ``win_amd64`` / ``linux_x86_64``）。

求值失败会抛出 :class:`~devkit.errors.ExpressionError`（带完整 Python 报错信息），
runner 会据此强制终止整个工作流，不受 ``errskip`` 影响。
"""
from __future__ import annotations

import builtins
import traceback
from pathlib import Path
from typing import Any, Callable

from devkit.errors import ExpressionError
from devkit.system import detect_system

_EXPRESSION_START = "?!{{"


def _iter_expressions(text: str):
    """扫描出所有 ``?!{{...}}`` 表达式。

    用花括号配对而不是正则，这样嵌套字典/集合（``{'a': {'b': 1}}``）也能正确识别；
    引号内的花括号不参与配对。未闭合的 ``?!{{`` 原样保留。
    """
    index = 0
    length = len(text)
    while index < length:
        start = text.find(_EXPRESSION_START, index)
        if start < 0:
            return
        pos = start + len(_EXPRESSION_START)
        depth = 2
        quote: str | None = None
        triple = False
        escape = False
        matched = False
        while pos < length:
            char = text[pos]
            if quote is not None:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif triple:
                    if text.startswith(quote * 3, pos):
                        pos += 3
                        quote = None
                        triple = False
                        continue
                elif char == quote:
                    quote = None
            else:
                if char in ("'", '"'):
                    quote = char
                    if text.startswith(char * 3, pos):
                        triple = True
                        pos += 3
                        continue
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        if text[pos - 1] != "}":
                            # 不是相邻的 "}}"，说明这个 "}" 属于表达式内容，
                            # 整体视为未闭合，原样保留。
                            return
                        expr = text[start + len(_EXPRESSION_START) : pos - 1]
                        end = pos + 1
                        if expr.strip():
                            yield start, end, expr
                        index = end
                        matched = True
                        break
            pos += 1
        if not matched:
            return


ExpressionConfig = bool | tuple[str, ...] | list[str] | None

#: 功能名归一: ``material`` 对应 ``clone`` 功能。
_FEATURE_ALIASES = {"material": "clone"}


def _get_recipe_path(recipe_path: Path | None) -> Callable[[], str]:
    def get_recipe_path() -> str:
        if recipe_path is None:
            raise ExpressionError("表达式内 get_recipe_path() 无法使用：当前无配方上下文")
        return str(recipe_path)

    return get_recipe_path


def _get_system_info(system: str | None) -> Callable[[], str]:
    def get_system_info() -> str:
        return system if system is not None else detect_system()

    return get_system_info


def get_builtin_namespace(
    recipe_path: Path | None = None, system: str | None = None
) -> dict[str, Any]:
    """构造一个独立的表达式命名空间（闭包持有上下文，线程安全）。

    每次求值都应携带自己的配方路径/系统，避免并发联动时上下文互相串扰。
    """
    return {
        "get_recipe_path": _get_recipe_path(recipe_path),
        "get_system_info": _get_system_info(system),
        "__builtins__": builtins,
    }


def eval_expression(expr: str, ns: dict[str, Any]) -> Any:
    """求值单个表达式；失败时抛 :class:`ExpressionError` 并附完整堆栈。"""
    try:
        return eval(expr, ns, ns)  # noqa: S307 - 表达式系统本就执行配方里的 Python
    except ExpressionError:
        raise
    except BaseException:  # noqa: BLE001 - 连 SystemExit/KeyboardInterrupt 也视为求值失败
        detail = traceback.format_exc()
        raise ExpressionError(f"表达式求值失败: ?!{{{{{expr}}}}}\n{detail}") from None


def has_expression(value: Any) -> bool:
    """值里是否可能出现表达式。"""
    return isinstance(value, str) and _EXPRESSION_START in value


def substitute_expression(value: str, ns: dict[str, Any]) -> Any:
    """替换字符串里的所有表达式。

    整串恰好是一个表达式时返回原始结果类型；否则把各表达式结果转成字符串后拼接。
    未闭合或内容为空的 ``?!{{`` 视为普通文本，原样保留。
    """
    if not isinstance(value, str) or _EXPRESSION_START not in value:
        return value

    matches = list(_iter_expressions(value))
    if not matches:
        return value

    if len(matches) == 1 and matches[0][0] == 0 and matches[0][1] == len(value):
        return eval_expression(matches[0][2], ns)

    pieces: list[str] = []
    cursor = 0
    for start, end, expr in matches:
        pieces.append(value[cursor:start])
        pieces.append(str(eval_expression(expr, ns)))
        cursor = end
    pieces.append(value[cursor:])
    return "".join(pieces)


def substitute_in_value(value: Any, ns: dict[str, Any], enabled: bool) -> Any:
    """递归替换值中的表达式。``enabled`` 为假时原样返回。"""
    if not enabled:
        return value
    if isinstance(value, str):
        return substitute_expression(value, ns)
    if isinstance(value, list):
        return [substitute_in_value(item, ns, enabled) for item in value]
    if isinstance(value, tuple):
        return tuple(substitute_in_value(item, ns, enabled) for item in value)
    if isinstance(value, dict):
        return {key: substitute_in_value(item, ns, enabled) for key, item in value.items()}
    return value


def get_effective_expression(
    recipe_expr: ExpressionConfig = None,
    job_expr: ExpressionConfig = None,
    step_expr: ExpressionConfig = None,
) -> ExpressionConfig:
    """获取最终生效的 expression 配置（就近覆盖: step > job > recipe）。"""
    effective = recipe_expr
    if job_expr is not None:
        effective = job_expr
    if step_expr is not None:
        effective = step_expr
    if isinstance(effective, list):
        return tuple(effective)
    return effective


def should_eval_expression(effective: ExpressionConfig, feature: str) -> bool:
    """按生效配置判断某个功能/字段是否启用表达式。"""
    if effective is None or effective is False:
        return False
    if effective is True:
        return True
    if isinstance(effective, (tuple, list)):
        alias = _FEATURE_ALIASES.get(feature, feature)
        return feature in effective or alias in effective
    return False
