# Changelog

所有值得注意的变更都记录在这里。

## [1.0.0] - 2026-11-12

### Added

- 递归扫描 `bsp/` 及所有子目录中的 `.c` 文件。
- 同步 Keil `*.uvprojx` 中名为 `bsp` 的 Group。
- 同步 EIDE `.eide/eide.yml` 中名为 `bsp` 的 Virtual Folder。
- 支持新增和删除：bsp 中删掉的 `.c` 会从工程源文件清单中移除。
- 幂等运行：工程已同步时不写文件、不产生新备份。
- `--dry-run`、`--verbose`、`--all-targets`、`--bsp`、`--keep-backups`、`--no-log` 参数。
- 修改前自动备份，写入后校验，失败回滚。
- 保持原文件编码、BOM、换行和缩进风格。
- 仅依赖 Python 标准库。
- Windows 启动器 `_bspToProject.bat`，自动探测 Python 3。
