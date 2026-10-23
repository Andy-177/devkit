"""devlop：执行配方的非 jobs 部分后进入交互式终端。

终端只继承配方级（全局）环境变量；作业/步骤级环境不会带入。每条输入命令
都交给 :meth:`devkit.runner.RecipeRunner.run_devlop_line` 执行，与 ``run``
步骤走同一条 shell 执行路径。
"""

from __future__ import annotations

import getpass
import socket
import sys
from collections.abc import Callable
from pathlib import Path

from devkit.errors import DevkitError
from devkit.recipe import Recipe
from devkit.runner import RecipeRunner

#: 续行提示符（与 ``prompt_toolkit`` 的 prompt_continuation 对应）
CONTINUATION = " ╰> "

#: 无法读取用户配色时的默认值：全白
DEFAULT_COLORS = ("#ffffff",) * 6


def normalize_shell_colors(value: object) -> tuple[str, ...]:
    """校验用户配色：必须是 6 个合法颜色的元组，否则返回全白。"""
    if not isinstance(value, tuple) or len(value) != 6:
        return DEFAULT_COLORS
    if not all(isinstance(color, str) and color.strip() for color in value):
        return DEFAULT_COLORS
    colors = tuple(color.strip() for color in value)
    try:
        from prompt_toolkit.styles import Style

        Style.from_dict({f"c{i}": f"fg:{color}" for i, color in enumerate(colors)})
    except Exception:
        return DEFAULT_COLORS
    return colors


def load_shell_colors(
    color_func: Callable[[], object] | None = None,
) -> tuple[str, ...]:
    """读取 ``_color.py`` 的 ``devlop_shell_color()``；失败时返回全白。"""
    if color_func is None:
        try:
            from devkit._color import devlop_shell_color as color_func
        except Exception:
            return DEFAULT_COLORS
    try:
        return normalize_shell_colors(color_func())
    except Exception:
        return DEFAULT_COLORS


def prompt_fragments(
    cwd: Path | str,
    *,
    user: str | None = None,
    host: str | None = None,
    colors: tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """按 ``(devlop, user@host, path, $, └─>, ╰>)`` 顺序给主提示符上色。"""
    colors = colors or load_shell_colors()
    path = Path(cwd).expanduser().resolve()
    return [
        (f"fg:{colors[0]}", "devlop"),
        ("", " "),
        (f"fg:{colors[1]}", f"{user or _username()}@{host or _hostname()}"),
        ("", " "),
        (f"fg:{colors[2]}", str(path)),
        ("", " "),
        (f"fg:{colors[3]}", "$"),
        ("", "\n"),
        (f"fg:{colors[4]}", "└─>"),
        ("", " "),
    ]


def continuation_fragments(
    colors: tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """续行提示符 `` ╰> ``，只给 ``╰>`` 上色。"""
    colors = colors or load_shell_colors()
    return [("", " "), (f"fg:{colors[5]}", "╰>"), ("", " ")]


def _username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "?"


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "?"


def prompt_text(cwd: Path | str, *, user: str | None = None, host: str | None = None) -> str:
    """生成两行主提示符：``devlop user@host 绝对路径 $`` / ``└─> ``。"""
    path = Path(cwd).expanduser().resolve()
    return f"devlop {user or _username()}@{host or _hostname()} {path} $\n└─> "


def join_input_lines(text: str) -> str:
    """把多行输入拼成一行；行间用单个空格连接，空行忽略。"""
    lines = (line.strip() for line in text.splitlines())
    return " ".join(line for line in lines if line)


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


class DevlopShell:
    """devlop 的交互式终端。

    ``execute_line`` 不依赖 prompt_toolkit，便于单独测试；``run`` 负责绘制
    提示符并读取输入。
    """

    def __init__(
        self,
        runner: RecipeRunner,
        recipe: Recipe,
        cwd: Path | str,
        *,
        env: dict[str, str] | None = None,
        workdir: Path | str | None = None,
    ) -> None:
        self.runner = runner
        self.recipe = recipe
        self.cwd = Path(cwd).expanduser().resolve()
        self.env = dict(env or {})
        self.root = Path(workdir).expanduser().resolve() if workdir is not None else self.cwd
        self._previous_cwd: Path | None = None
        self.colors = load_shell_colors()

    @property
    def prompt(self) -> str:
        return prompt_text(self.cwd)

    def change_directory(self, argument: str = "") -> None:
        """处理 cd/chdir；无参数回到工作区，``-`` 回到上一个目录。"""
        if not argument:
            target = self.root
        elif argument == "-":
            target = self._previous_cwd or self.root
        else:
            target = Path(_strip_quotes(argument)).expanduser()
            if not target.is_absolute():
                target = self.cwd / target
        try:
            target = target.resolve()
        except OSError as exc:
            print(f"cd: {exc}", file=sys.stderr)
            return
        if not target.is_dir():
            print(f"cd: 目录不存在: {target}", file=sys.stderr)
            return
        self._previous_cwd = self.cwd
        self.cwd = target

    def execute_line(self, text: str) -> bool:
        """执行一行输入；返回 False 表示退出终端。"""
        line = join_input_lines(text)
        if not line:
            return True

        parts = line.split(None, 1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command in {"exit", "quit", "logout"}:
            return False
        if command in {"cd", "chdir"}:
            self.change_directory(argument)
            return True

        try:
            self.runner.run_devlop_line(
                line, recipe=self.recipe, cwd=self.cwd, env=self.env
            )
        except DevkitError as exc:
            print(exc, file=sys.stderr)
        except KeyboardInterrupt:
            print(file=sys.stderr)
        return True

    def run(self, *, input=None, output=None) -> None:
        """用 prompt_toolkit 绘制终端并循环读取命令。"""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        def continuation(width: int, line_number: int, wrap_count: int) -> FormattedText:
            return FormattedText(continuation_fragments(self.colors))

        session = PromptSession(key_bindings=bindings, input=input, output=output)
        print("devlop 终端：Enter 执行，Ctrl-J 换行，Ctrl-D 退出", file=sys.stderr)

        while True:
            try:
                text = session.prompt(
                    FormattedText(prompt_fragments(self.cwd, colors=self.colors)),
                    multiline=True,
                    prompt_continuation=continuation,
                )
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            if not self.execute_line(text):
                break


def run_devlop(runner: RecipeRunner, recipe_path: Path | str) -> int:
    """执行配方的非 jobs 部分，然后进入 devlop 终端。"""
    recipe, cwd = runner.prepare_devlop(recipe_path)
    env = runner.devlop_env(recipe)
    shell = DevlopShell(
        runner,
        recipe,
        cwd,
        env=env,
        workdir=runner.workdir,
    )
    shell.run()
    return 0
