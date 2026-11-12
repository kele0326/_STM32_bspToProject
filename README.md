# bspToProject

把 `bsp` 目录（含所有子目录）里的 `.c` 文件自动同步到 **Keil MDK** 和 **EIDE** 工程，避免手动添加、删除源文件。

![platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![python](https://img.shields.io/badge/python-3.8%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

> **English overview:** A Windows/Python tool that syncs `bsp/**/*.c` into the source list of Keil MDK (`.uvprojx`) and EIDE (`eide.yml`) projects. It adds new files, removes deleted ones, creates timestamped backups, supports dry-run, and uses only the Python standard library.

---

## 这个工具解决什么问题

STM32 工程通常把板级支持包放在 `bsp/` 目录下。每新增一个 `.c` 文件，就要在 Keil 的工程树里手动 Add 一次；每删除一个文件，又容易忘记从工程里移除，导致编译报错或链接到旧代码。使用 EIDE 时还要再维护一遍 `eide.yml`。

`bspToProject` 直接扫描磁盘上的 `bsp/**/*.c`，让工程源文件清单和实际文件保持一致：

- **Keil**：维护 `*.uvprojx` 中名为 `bsp` 的 Group；
- **EIDE**：维护 `.eide/eide.yml` 中 `virtualFolder` 下名为 `bsp` 的虚拟文件夹。

支持新增、删除和重复运行（幂等）。一个工程只有 Keil 或只有 EIDE 也可以使用。

## 功能特性

- 递归扫描 `bsp` 及其所有子目录中的 `.c` 文件；
- 新增文件自动加入工程，磁盘上已删除的文件自动从工程移除；
- 同时支持 Keil `.uvprojx` 和 EIDE `eide.yml`；
- 同一工程根目录下存在多个工程文件时，会全部处理；
- 修改前自动备份，写入后校验，失败自动回滚；
- 按行编辑，尽量保持原文件的缩进、换行、BOM 和 XML 声明不变；
- 支持 `--dry-run`，先预览再写入；
- 只使用 Python 标准库，不需要安装 PyYAML 等第三方包；
- 不写死工程名、工程文件名，换一个 STM32 工程也能直接用；
- 支持一个工程内多个 Keil Target（默认只同步已有 `bsp` 组的 Target）。

## 环境要求

- Windows 10 / 11；
- Python 3（建议 3.8 及以上；开发和测试使用 Python 3.12）；
- 按需安装 Keil MDK 5 或 VSCode + EIDE 扩展。

> 脚本本身只修改工程文件，不依赖 Keil/EIDE 的安装路径；即使没有安装 Keil，也可以运行。编译验证仍需要对应的 IDE。

## 快速开始

### 方式一：放进 STM32 工程根目录（推荐）

把发布包里的两项复制到你的 STM32 工程根目录：

```text
YourProject/
├─ _bspToProject.bat          ← 复制这里
├─ Tool_bspToProject/         ← 复制这里
│  └─ bspToProject.py
├─ bsp/                       ← 你的板级支持包
│  ├─ bspCommon.c
│  ├─ disFont.c
│  └─ user_main.c
├─ MDK-ARM/
│  └─ YourProject.uvprojx
└─ EIDE/
   └─ .eide/
      └─ eide.yml
```

双击工程根目录下的 `_bspToProject.bat` 即可。

脚本默认把“`Tool_bspToProject` 的上一级目录”当作工程根，所以不需要修改任何路径，也不要求工程文件夹叫 `Code_STM32F103C8`。

### 方式二：工具放在任意位置

也可以把本仓库克隆到任意位置，用 `--root` 指定目标工程：

```bat
git clone https://github.com/<your-name>/bspToProject.git
cd bspToProject
_bspToProject.bat --root "D:\path\to\YourProject"
```

还可以直接把工程文件夹拖到 `_bspToProject.bat` 图标上，Windows 会把文件夹路径作为参数传进去。

### 第一次运行建议

先预览，不写文件：

```bat
_bspToProject.bat --dry-run --verbose
```

确认输出中的“新增 / 删除”列表符合预期后，再去掉 `--dry-run` 正式运行：

```bat
_bspToProject.bat
```

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| 无参数 | 以 `Tool_bspToProject` 的上一级目录为工程根，自动查找工程 |
| `<路径>` | 工程目录、`.uvprojx` 或 `eide.yml`，可传一个或多个 |
| `--root <目录>` | 指定工程根目录；不能和位置参数同时使用 |
| `--bsp <目录>` | 手动指定 bsp 目录，默认从工程目录向上找最近的 `bsp` |
| `--dry-run` | 只显示将要修改的内容，不写工程文件 |
| `--verbose` | 输出详细过程，包括每个新增/删除项 |
| `--all-targets` | 给所有 Keil Target 都创建/同步 `bsp` 组 |
| `--keep-backups N` | 每个文件保留最近 N 份 `.bak`，默认 10；0 表示不清理 |
| `--no-log` | 不写日志文件 |
| `-h`, `--help` | 显示帮助 |

示例：

```bat
:: 预览当前工程
_bspToProject.bat --dry-run --verbose

:: 同步指定工程
_bspToProject.bat "D:\Work\MySTM32Project"

:: 只处理某个 uvprojx
_bspToProject.bat "D:\Work\MySTM32Project\MDK-ARM\MySTM32Project.uvprojx"

:: 工程里没有 bsp 目录时手动指定
_bspToProject.bat --root "D:\Work\MySTM32Project" --bsp "D:\Work\MySTM32Project\Board\bsp"
```

## 它会修改哪些文件

| 工程 | 文件 | 修改位置 |
| --- | --- | --- |
| Keil | `*.uvprojx` | Target 内名为 `bsp` 的 `<Group>` 的 `<Files>` |
| EIDE | `.eide/eide.yml` | `virtualFolder` 下名为 `bsp` 的虚拟文件夹的 `files` |

只会处理 **`bsp` 目录下、扩展名为 `.c`** 的条目。其他组、其他扩展名、组内非 bsp 条目都不会动。

以下内容不会被修改：

- `*.uvoptx`、`*.uvguix.*` 等 Keil 用户选项/界面文件；
- `eide.yml` 的 `srcDirs`、`incList`、`targets` 等配置；
- `.eide/files.options.yml`（单文件附加编译参数）；
- `build/`、`compile_commands.json`、`ref.json` 等构建生成物；
- `.h` 头文件；
- 任何 include path 配置。

> 需要给 bsp 子目录额外添加 include path 时，请手动在 Keil/EIDE 中配置；本工具只维护源文件清单。

## 同步规则

1. 从工程目录向上查找最近的 `bsp` 目录（可用 `--bsp` 覆盖）。
2. 递归扫描 `bsp/**/*.c`，跳过 `.git`、`.svn`、`.vs`、`.idea`、`node_modules`、`__pycache__` 等目录。
3. 所有子目录的 `.c` **平铺**加入同一个 `bsp` 组/虚拟文件夹，不镜像子目录结构。
4. EIDE 中写入的路径相对于 EIDE 工程目录（通常是 `.eide` 的上一级目录），统一使用 `/`；Keil 中使用相对于 `.uvprojx` 所在目录的路径，使用 `\`。
5. 删除条件严格为：条目路径位于 `bsp` 下、扩展名为 `.c`、且磁盘上已不存在。重命名会表现为“删除旧项 + 新增新项”。
6. 新增项按相对路径排序追加；已有项顺序保持不变。
7. 如果 bsp 下所有 `.c` 都被删除，Keil 会保留空的 `bsp` 组，EIDE 会保留 `files: []`，不会删除整个组/文件夹。
8. Keil 多 Target 时，默认只同步已经有 `bsp` 组的 Target；如果一个都没有，则在第一个 Target 创建。使用 `--all-targets` 才会给每个 Target 都创建。

## 备份、日志与退出码

- 修改前备份为 `原文件名.YYYYMMDD_HHMMSS.bak`，默认每个文件保留最近 10 份；可用 `--keep-backups` 调整。
- 日志默认写到 `Tool_bspToProject/bspToProject_log.txt`（UTF-8 BOM），包含时间、工程、增删项和错误信息。
- `--dry-run` 不会修改工程文件，但仍会写日志；需要完全不写日志时加 `--no-log`。
- 退出码：`0` 表示成功或无需修改，`1` 表示处理失败，`2` 表示命令行参数错误。

## 常见问题

### 提示 “Python 3 was not found”

安装 Python 3，并在安装时勾选 **Add python.exe to PATH**。安装后重新打开命令行，确认：

```bat
py -3 --version
```

或：

```bat
python --version
```

Windows 应用商店的 `python.exe` 存根可能被误判为可用；如果运行没有输出，请安装官方 Python 或使用 `py -3`。

### 提示 “没有找到 Keil .uvprojx 或 EIDE .eide/eide.yml”

说明脚本没有在目标工程里找到工程文件。检查：

- `_bspToProject.bat` 和 `Tool_bspToProject` 是否放在工程根目录；
- 工程文件是否在工程根目录的子目录中（脚本会递归查找）；
- 或者显式传入工程目录：`_bspToProject.bat --root "D:\path\to\Project"`。

### Keil 里没有 `bsp` 组

如果 bsp 中至少有一个 `.c`，脚本会自动创建。若工程有多个 Target，默认只在第一个 Target 创建；需要每个 Target 都有时加 `--all-targets`。

### EIDE 修改后没有变化

EIDE 可能会缓存工程配置。执行 `Developer: Reload Window`，或关闭并重新打开工程。确认 `.eide/eide.yml` 中 `virtualFolder` 下已经出现 `bsp` 文件夹。

### 写入失败，提示文件被占用

先关闭 Keil、VSCode 或其他正在使用工程文件的程序，再运行。脚本写入失败时会回滚本轮已写入的文件。

### 子目录里的头文件找不到

本工具只同步 `.c`，不会自动添加 include path。请在 Keil 的 `Include Paths` 或 EIDE 的 include 配置中手动加入对应子目录。

### 不同子目录有同名 `.c`

脚本会给出警告，但不会自动改名。请确认这些同名文件不会在工程中产生冲突，或调整文件名/目录结构。

### 会不会误删我手动加入的文件

不会。只有同时满足“位于 bsp 下 + 扩展名 `.c` + 磁盘上不存在”的条目才会被移除。其他条目保持原样。

## 目录结构

```text
bspToProject/
├─ _bspToProject.bat          # Windows 启动器
├─ Tool_bspToProject/
│  ├─ bspToProject.py         # 主脚本（仅标准库）
│  └─ bspToProject_log.txt    # 运行后生成，已被 .gitignore 忽略
├─ README.md
├─ CHANGELOG.md
├─ LICENSE
└─ .gitignore
```

## 开发与测试

脚本只使用 Python 标准库。语法检查：

```bat
python -m py_compile Tool_bspToProject\bspToProject.py
```

建议在工程副本上验证：

1. 在 `bsp/` 下新建一个子目录和 `.c` 文件；
2. 运行 `_bspToProject.bat --dry-run --verbose`，确认能看到新增项；
3. 正式运行后，在 Keil/EIDE 中确认文件已加入；
4. 删除该 `.c` 再运行，确认条目被移除；
5. 再运行一次，确认输出“无需修改”且不产生新备份。

## 许可证

本项目使用 [MIT License](LICENSE)。

## 贡献

欢迎提交 Issue 或 Pull Request。报告问题时，建议附上：

- 操作系统和 Python 版本；
- 使用的是 Keil、EIDE 还是两者；
- `--dry-run --verbose` 的输出；
- 相关工程文件的最小片段（请先脱敏，不要上传完整商业项目）。

---

如果这个工具对你有帮助，欢迎点一个 Star。
