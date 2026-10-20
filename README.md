# devkit

配方（`repice.json`）驱动的构建工作流工具。克隆仓库、下载工具链、解压资源、
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

## 用法

```text
devkit build repice.json                # 执行配方
devkit run repice.json                  # build 的别名，完全等价
devkit build bon/repice.json -w build   # 指定工作区
devkit build repice.json -j build-windows --dry-run
devkit repice.json                      # 省略 build 也行
devkit system                           # 打印当前系统标识（如 win_amd64）
devkit --version
```

直接以脚本运行（任何平台）：

```text
python devkit/cli.py build repice.json
python devkit/cli.py --help
```

### 安装为 Python 模块（可选）

`devkit/` 本体保持独立；`dkpymodule/` 是一个空壳工程，构建时把
`devkit/*.py` 同步进 `src/devkit/` 并生成包入口，产出可 pip 安装的 `devkit`：

```text
python dkpymodule/sync.py                 # 手动同步（构建时会自动做）
pip install ./dkpymodule                  # 安装后得到 devkit 命令
python -m devkit build repice.json        # 模块方式运行
```

依赖：`loguru`、`dulwich`、`littledl`（由 pip 自动装）；包入口内置 loguru 垫片，
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

打包后 `dist\devkit\devkit.exe build repice.json` 即可使用（loguru 靠垫片降级，
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
-> `unzip`（按列表顺序）-> `jobs`（按声明顺序）。

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
    {"https://example.com/win.exe": "win_amd64"},
    {"https://example.com/mac.dmg": ["macos_arm64", "macos_amd64"]}
  ],
  "unzip": [
    "assets.zip",
    {"toolchain.zip": "win_amd64"}
  ],
  "data": ["build.sh", 0],
  "jobs": {
    "build-windows": {
      "system": "win_amd64",
      "steps": {
        "init": {
          "run": [
            ["msys2-base.exe"],
            ["buildsh.bat", "-c", 0],
            ["echo hello > out.txt"]
          ],
          "call": {"hello.json": ["hello"]}
        }
      }
    }
  }
}
```

### material / download / unzip

* `material`：一个 git 仓库；`repo` 必填。`branch` 空 -> 默认分支，`depth` 0/null
  -> 完整历史，`commit` 存在则克隆后检出。克隆先落临时目录，成功才改名，
  失败不留半成品；目标已是 git 仓库则跳过（方便重复执行）。
* `download` / `unzip`：字符串表示总执行，`{值: 系统}` 表示仅系统匹配时执行，
  系统值可给单个字符串或列表。下载文件统一落在下载目录；zip 相对路径按
  "配方目录 -> 克隆的 workspace -> 工作区 -> 下载目录" 顺序查找，解压结果统一
  落到下载目录（含 zip-slip 防护）。

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
  `repice.json` / `recipe.json` 查找）；被调配方有 material 时在工作区下
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
  runner.py     执行引擎: 克隆 -> 下载 -> 解压 -> 工作流
  recipe.py     配方解析与校验
  git.py        material 克隆（dulwich）
  dl.py         下载（littledl）
  unzip.py      解压（zipfile，含 zip-slip 防护）
  guard.py      并发运行的 stdin 守卫
  progress.py   单行进度条（纯标准库）
  system.py     系统标识检测（<os>_<arch>）
  errors.py     异常体系
  _version.py   版本号（唯一来源）
dkpymodule/    可选壳工程: pyproject/setup.py/sync.py + src/devkit（同步生成）
example/       示例配方: repice.json + hello.json
requirements.txt 运行依赖（pyinstaller 不在此列，打包时单独装）
test/           全套单元/集成/端到端测试（209 个）
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
