# devkit

配方（`recipe.json`）驱动的构建工作流工具。克隆仓库、下载工具链、解压资源、
按工作流执行 shell 指令，全部由一个 JSON 配方描述。

无 pyproject、无安装步骤，纯脚本直接运行；也可打包成独立 exe 分发。
想当作 Python 模块（pip 安装 / `import devkit`）用时，见 `dkpymodule/`。

## 运行要求

* Python 3.10+（已在 3.13 验证）
* 核心零第三方依赖：没装 loguru 会自动降级为内置日志
* 可选依赖（自己装进 venv，见下方「构建独立二进制」）：
  * `dulwich` — `material` 克隆需要
  * `littledl` — `download` 下载需要
  * `loguru` — 彩色日志（可选）
  * `prompt_toolkit` — `devlop` 交互终端需要

## 用法

```text
devkit build recipe.json                # 执行配方
devkit run recipe.json                  # build 的别名，完全等价
devkit devlop recipe.json               # 执行非 jobs 部分后进入交互终端
devkit shell recipe.json                # devlop 的别名，但不显示日志
devkit build bon/recipe.json -w build   # 指定工作区
devkit build recipe.json -j build-windows --dry-run
devkit recipe.json                      # 省略 build 也行
devkit system                           # 打印当前系统标识（如 win_amd64）
devkit console recipe.json             # 从配方视角启动表达式 REPL
devkit --version
```

直接以脚本运行（任何平台）：

```text
python devkit/cli.py build recipe.json
python devkit/cli.py --help
```

### 安装为 Python 模块（可选）

`devkit/` 本体保持独立；`dkpymodule/` 是一个空壳工程，构建时把
`devkit/*.py` 同步进 `src/devkit/` 并生成包入口，产出可 pip 安装的 `devkit`：

```text
python dkpymodule/sync.py                 # 手动同步（构建时会自动做）
pip install ./dkpymodule                  # 安装后得到 devkit 命令
python -m devkit build recipe.json        # 模块方式运行
```

依赖：`loguru`、`dulwich`、`littledl`、`prompt_toolkit`（由 pip 自动装）；包入口内置 loguru 垫片，
缺 loguru 也能跑。

### 构建独立二进制（可选）

venv 只用来构建，建一次即可：

```text
python -m venv venv                        # 初始化
venv\Scripts\pip.exe install -r requirements.txt   # 装运行依赖
venv\Scripts\pip.exe install pyinstaller   # 打包工具不进 requirements，单独装
```

打包（Windows，产物在 `dist/devkit/`）：

```text
venv\Scripts\pyinstaller.exe -D -c -n devkit -y --clean -p . --copy-metadata littledl --collect-binaries dulwich --hidden-import win32_setctime --hidden-import colorama --hidden-import colorama.win32 --hidden-import colorama.winterm --hidden-import h2 --hidden-import httpcore._async.http2 --hidden-import httpcore._sync.http2 --hidden-import anyio._backends._asyncio --exclude-module dulwich.tests --exclude-module tkinter --distpath dist --workpath build\pyinstaller --specpath build\pyinstaller devkit\cli.py
```

打包后 `dist\devkit\devkit.exe build recipe.json` 即可使用（loguru 靠垫片降级，
故未打进 requirements；dulwich/littledl 依赖已随包收集）。

### build 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `-w, --workdir` | 工作区，run 指令在这里执行，克隆落在其 `workspace` 子目录 | `.devkit` |
| `-d, --download-dir` | 下载/解压落地目录 | `.devkit` |
| `-s, --system` | 覆盖自动检测的系统标识 | 自动检测 |
| `-j, --job` | 只执行指定名称的工作流，可重复 | 全部 |
| `--dry-run` | 只打印要做的事，不克隆/不下载/不执行 | 关 |
| `-v, --verbose` | 输出 DEBUG 日志 | 关 |

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 全部完成 |
| `1` | 有步骤/作业失败被 errskip 处理，或配方/参数错误 |
| `N` | 步骤 `errskip: crash` 时透传子进程退出码 |
| `130` | Ctrl+C 中断 |

## 配方格式

顶层字段全部可选，固定执行顺序：`material` 克隆 -> `download`（按列表顺序）
-> `unzip`（按列表顺序）-> `unpack`（按列表顺序）-> `jobs`（按声明顺序）。

```json
{
  "material": {
    "repo": "https://github.com/xxx/yyy.git",
    "branch": "main",
    "depth": 1,
    "commit": null
  },
  "download": [
    "https://example.com/tool.zip",
    {"url": "https://example.com/win.exe", "system": "win_amd64"},
    {"url": "https://example.com/mac.dmg", "system": ["macos_arm64", "macos_amd64"]}
  ],
  "unzip": [
    "assets.zip",
    {"file": "toolchain.zip", "system": "win_amd64", "extract": true}
  ],
  "unpack": [
    "https://example.com/bundle.zip",
    {"url": "https://example.com/win-tools.zip", "system": "win_amd64", "extract": true}
  ],
  "env": [
    {"key": "hello", "value": "world"},
    {"key": "world", "value": "hello;world", "system": "win_arm64"}
  ],
  "data": ["build.sh", 0],
  "jobs": {
    "build-windows": {
      "system": "win_amd64",
      "env": [{"key": "CC", "value": "clang"}],
      "steps": {
        "init": {
          "run": [
            ["msys2-base.exe"],
            ["buildsh.bat", "-c", 0],
            ["echo hello > out.txt"]
          ],
          "env": [{"key": "CC", "value": "gcc"}],
          "call": {"hello.json": ["hello"]}
        }
      }
    }
  }
}
```

### material / download / unzip / unpack

* `material`：一个 git 仓库；`repo` 必填。`branch` 空 -> 默认分支，`depth` 0/null
  -> 完整历史，`commit` 存在则克隆后检出。克隆先落临时目录，成功才改名，
  失败不留半成品；目标已是 git 仓库则跳过（方便重复执行）。
* `download` / `unzip` / `unpack`：语法统一，每一项是字符串简写或对象：

  ```json
  "download": ["https://a/b.zip", {"url": "https://a/c.exe", "system": "win_amd64"}],
  "unzip":    ["a.zip", {"file": "b.zip", "system": "win_amd64", "extract": true}],
  "unpack":   ["https://a/b.zip", {"url": "https://a/c.zip", "system": "win_amd64", "extract": true}]
  ```

  * 键：`download`/`unpack` 用网络链接 `url`，`unzip` 用本地文件 `file`；
  * `system`（可选）：单个字符串或列表，缺省对所有系统生效；
  * `extract`（可选，缺省 `false`，仅 `unzip`/`unpack`）：`true` 时把压缩包里的内容
    直接铺到下载目录，否则先在下载目录下建一个与压缩包同名的子目录再解压进去；
  * `download` 是纯下载，只有 `url` 和 `system`，没有 `extract`；下载文件统一落在
    下载目录；
  * `unzip` 的本地 zip 按 "配方目录 -> 克隆的 workspace -> 工作区 -> 下载目录"
    顺序查找；`unpack` 先把 zip 下载到 `<下载目录>/.dkcache` 缓存，再解压释放；
  * 两者都解压到下载目录（默认 `.devkit`），含 zip-slip 防护。

### env / area 环境块（可选）

`env` 可以放在三处：全局（与 `download` 平级）、作业内（与 `system` 平级）、
步骤内（与 `run` 平级）。格式是列表，每项 `key`/`value` 必填，`system` 可选：

```json
"env": [
  {"key": "hello", "value": "world"},
  {"key": "world", "value": "hello;world", "system": "win_arm64"}
]
```

`system` 是单个字符串或列表，缺省对所有系统生效，只有当前系统匹配的项才参与合并。

`area` 是 `env` 的变种，格式相同，三处都能写。`env` 与 `area` 每一级的逻辑完全一样，
只是层数不同：

* 从上层继承的环境起底（顶层配方为空；被 `call` 调用的子配方默认继承调用方步骤的
  环境）；
* 依次叠加配方级、作业级、步骤级；每一级都一样：该级写了 `area` 就强制覆盖继承下来
  的结果以及同级的 `env`，只保留自身 `area`（空 `area` 也是一条覆盖线，会把环境清空）；
  否则把同级 `env` 覆盖合并到当前结果上（同 key 覆盖、异 key 合并）；
* 因此上层若用了 `area`，从它继承下来的 area 仍可被下层的 `env` 修改；
* 同一个块内重复的 key，后出现的覆盖先出现的；
* 合并结果只增量叠加在进程自身环境之上，只作用于该步骤每个 `run` 子进程。

顶层 `area` 用来让子配方**不接受**从调用方继承的环境：子配方在顶层写 `area`，就只用
自己的 area。

`devkit console` 会启动一个 Python REPL，内置完整的 builtins、`get_recipe_path()`、
`get_system_info()`，并注入 `devkit` 包及各子模块（`recipe`/`runner`/`expr` 等），
方便从配方视角手工验证表达式。

### devlop / shell 交互终端

`devkit devlop recipe.json` 先执行配方的非 `jobs` 部分（`material`/`download`/
`unzip`/`unpack`），然后进入交互终端。终端默认工作目录是 `--workdir`；命令通过与
`run` 相同的执行路径运行，因此继承配方级 `env`/`area`，但不会继承作业级、步骤级
环境。`cd`/`chdir`、`exit`/`quit`/`logout` 是终端内建命令。

`devkit shell recipe.json` 是 devlop 的别名，行为完全一致，只是隐藏 INFO/DEBUG
日志，界面更简洁；错误仍会显示。

```text
devlop 用户名@PC名 完整所在路径 $
└─> 
```

支持多行输入，续行提示为 ` ╰> `，各行用空格拼接（例如第一行 `cd`、第二行 `/?`
等价于 `cd /?`）。Enter 执行，Ctrl-J 换行（行数不限），Ctrl-D 退出。参数与 `build`
相同，但没有 `-j/--job`。

提示符颜色由 `devkit/_color.py` 的 `devlop_shell_color()` 决定，返回一个 6 元组，
依次对应 `(devlop, 用户名@主机名, 路径, $, └─>, ╰>)`。无法调用或格式不正确时
使用全白色；修改后重新进入 devlop 生效。

### 表达式 `?!{{...}}`

任意字符串字段中都可以写 Python 表达式，默认**关闭**，必须显式打开：

```json
{
  "expression": true,
  "download": [{"url": "?!{{'https://host/' + get_system_info() + '.zip'}}"}],
  "jobs": {
    "build": {
      "expression": ["run", "env"],
      "steps": {
        "s": {
          "run": [["echo", "?!{{1 + 2}}", ">", "out.txt"]],
          "env": [{"key": "HOME2", "value": "?!{{get_recipe_path()}}"}]
        }
      }
    }
  }
}
```

* 表达式在**运行时**求值（解析配方时只保留原文）；
* 整串只含一个表达式时保留结果原始类型（数字、列表、字典等），否则把各段结果
  转成字符串后拼接；
* 命名空间包含完整 builtins（可以 `import`），以及 `get_recipe_path()`（当前配方
  路径）与 `get_system_info()`（当前系统标识）；
* 支持嵌套花括号（如 `{'a': {'b': 1}}`），引号内的花括号不参与配对；
* 求值失败会打印完整 Python 报错并**强制终止整个工作流**，不受任何 `errskip` 影响。

`expression` 可写在全局、作业、步骤三处，按就近原则覆盖：`true` 开启全部，
`["run", "env", ...]` 是功能/字段白名单，`false` 关闭（可关闭上层开启的表达式）。
可用功能名：`material`（别名 `clone`）、`download`、`unzip`、`unpack`、`env`、
`area`、`data`、`system`、`run`、`call`。默认关闭。`system` 也可用于作业/步骤/
环境项/下载项的 `system` 过滤（如 `"system": "?!{{get_system_info()}}"`）。

### run 与 data

`run` 永远是双层列表：第一层是命令列表（按位置定顺序），第二层是一条命令，
元素只能是字符串或数字，数字表示 `data` 中对应下标：

```json
"run": [["echo", 0, ">", "out.txt"], ["echo", "second command"]]
```

所有命令一律拼成一行字符串交给系统 shell（Windows 为 cmd，POSIX 为 `/bin/sh`），
管道、重定向、`&&` 均可用。失败按非 0 退出码判定。

`data` 中的整数在顶层配方里是普通数据；被 `call` 调用的配方里则是入参占位符
（"取 call 参数列表第 N 个"）。

### 步骤控制字段（全部可选）

```json
"step-name": {
  "run": [...],
  "call": {"child.json": ["arg"]},
  "skip": true,
  "errskip": "stop",
  "linkup": ["other-step"],
  "jump": "step-x",
  "next": null,
  "if": {
    "error": "on-error",
    "success": "on-ok",
    "jump": {"src": "dst"},
    "redirect": {"src": "dst"}
  }
}
```

* `skip`：顺序走到时跳过；被 `jump`/`if`/`linkup`/`next` 明确指到时照常执行
* `errskip`（步骤缺省 `stop`）：
  * `crash` — 整个构建立即中止，透传退出码
  * `stop` — 终止当前作业，交作业级 errskip 收场
  * `proceed` / `skip` — 终止出错步骤，按去向跳转，作业继续
* 成功去向优先级：`next` > `if.success` > `jump` > `if.jump` > 顺序下一步
* 失败去向（proceed/skip）：`if.error` > `jump` > `if.jump` > 顺序下一步
* `next`：`null` 结束本作业；作业名切换到该作业
* `linkup`：与被指步骤并发执行；组内失败按发起方的 errskip 处理
* `call`：相对本配方文件的路径（文件或目录，目录按
  `recipe.json` 查找）；被调配方有 material 时在工作区下
  以配方名开子目录，否则共用父工作区；循环调用会报错

### 作业（jobs）

```json
"build": {
  "system": "win_amd64",
  "skip": true,
  "linkup": "prepare",
  "errskip": "proceed",
  "steps": {...}
}
```

* `system` 与当前系统不一致则跳过整个工作流
* `skip` 顺序走到时跳过，被 `next`/`linkup` 指到仍执行
* `errskip`（作业缺省 `proceed`）：`crash` 中止整个构建；`proceed` 终止出错
  作业、联动作业跑完后继续；`skip` 强行终止整个联动组后继续
* 被 errskip 处理掉的失败会让最终退出码非 0

### 联动（linkup）与 stdin 守卫

联动期间子进程的 stdin 不是终端，而是一个装满空行的哨兵文件：只要任何并发
子进程读 stdin（说明它是交互式程序），整个联动组立即被杀掉并报
`InteractiveInputError`。交互式程序请单独串行执行，不要参与联动。
不联动时子进程继承真实终端，交互正常。

### call 示例

父配方：

```json
{"steps": {"s": {"call": {"hello.json": ["hello"]}}}}
```

子配方 `hello.json`：

```json
{
  "data": ["hi", 0],
  "jobs": {"print": {"steps": {"s": {"run": [["echo", 1]]}}}}
}
```

调用后 `data` 变为 `["hi", "hello"]`，`echo 1` 输出 `hello`。

## 目录结构

```text
devkit/
  cli.py        命令行入口（python devkit/cli.py 直接可跑）
  runner.py     执行引擎: 克隆 -> 下载 -> 解压 -> 下载解压 -> 工作流
  recipe.py     配方解析与校验
  git.py        material 克隆（dulwich）
  dl.py         下载（littledl）
  unzip.py      本地 zip 解压（zipfile，含 zip-slip 防护）
  unpack.py     下载并解压（download + unzip，缓存到 .dkcache）
  guard.py      并发运行的 stdin 守卫
  progress.py   单行进度条（纯标准库）
  system.py     系统标识检测（<os>_<arch>）
  errors.py     异常体系
  _version.py   版本号（唯一来源）
dkpymodule/    可选壳工程: pyproject/setup.py/sync.py + src/devkit（同步生成）
example/       示例配方: recipe.json + hello.json
requirements.txt 运行依赖（pyinstaller 不在此列，打包时单独装）
test/           全套单元/集成/端到端测试（221 个）
```

## 测试

```text
python -m unittest discover -s test -t .
```

* 三种方式均可：`discover -s test -t .`、`discover -s test`、单文件直跑
  `python test/test_runner.py`
* 无 loguru / 无 dulwich 的解释器上自动跳过依赖项，其余照常通过
* 覆盖：配方解析、执行引擎（四种 errskip、jump/redirect/next、call、联动并发、
  stdin 守卫、dry-run 零副作用）、CLI 退出码、真实子进程端到端
