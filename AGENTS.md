# AGENTS.md

## 适用范围

本文件适用于当前仓库根目录及其子目录。

## 工作约束

- 始终使用中文简体回复。
- 修改前先阅读现有实现，优先保持 `AstrBot Python 插件 + Node sidecar` 的分层边界。
- 不要将账号、token、二维码会话、群号联调记录等本地敏感信息提交到仓库。
- sidecar 的本地运行数据仅允许留在 `sidecar/data/`，并保持被 `.gitignore` 忽略。
- 涉及 git 提交、push、历史改写时，默认使用非交互命令。
- 不要无故修改 `_refs/` 中的依赖引用；如需更新，应同时更新 commit 和说明。

## 目录说明

- `main.py`：AstrBot 命令入口与调度逻辑
- `storage.py`：插件本地状态与账号存储
- `sidecar/`：Node sidecar
- `_refs/`：依赖仓库引用信息
