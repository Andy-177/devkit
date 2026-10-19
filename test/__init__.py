"""test 包自举: 包模式导入（unittest -t . / pytest / python -m unittest test.x）时，
先把项目根和 test 目录塞进 sys.path，保证 `import _common` 与 `import devkit` 都可用。
以脚本方式直跑（python test/test_x.py）时 sys.path[0] 已是 test 目录，此文件不参与。
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
