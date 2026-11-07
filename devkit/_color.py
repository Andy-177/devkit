"""develop 终端配色（用户可修改）。``devlop_shell_color()`` 为旧名兼容别名。

``develop_shell_color()`` 必须返回 6 个颜色字符串组成的元组，依次对应：

``(develop, 用户名@主机名, 路径, $, └─>, ╰>)``

无法调用或返回格式不正确时，终端使用全白色。
"""

from __future__ import annotations


def develop_shell_color():
    return sky


def devlop_shell_color():
    return develop_shell_color()


cherry = ("#e09bff", "#8a52bc", "#6b73ad", "#cc87eb", "#6b73ad", "#6b73ad")
sky = ("#9bd8ff", "#5295bc", "#6ba8ad", "#87d0eb", "#6ba8ad", "#6ba8ad")
cool = ("#00e5ff", "#0096c7", "#48cae4", "#00ffc8", "#00e5ff", "#00e5ff")
devloper = ("#4da6ff", "#1e6fd9", "#2e8bc0", "#00bfff", "#2e8bc0", "#2e8bc0")
