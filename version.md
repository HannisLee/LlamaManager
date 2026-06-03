# LlamaManager 版本变更记录

> **版本号规则**：每次修改只递增最后一位（如 `0.0.0 → 0.0.1`），中间位和大版本号由用户手动指定时才跃进。

## v0.0.1 — 2026-06-03

- 创建项目基础结构：app.py、index.html、settings.json、requirements.txt、run.sh、README.md
- 基于 FastAPI 后端 + 原生 HTML/JS 单页面 WebUI
- 实现 llama-server 进程管理：启动、停止、重启
- 实现端口冲突自动检测与 kill（保护端口 22 和 PID 1）
- 实现 GGUF 模型扫描（递归扫描 model_dir）
- 实现日志查看（尾部 200 行）
- 配置持久化到 settings.json（原子写入）
- conda 环境 `llama-manager`，管理后台绑定 `0.0.0.0:8082`

## v0.0.2 — 2026-06-03

- 扫描本机 llama-server 路径，定位到 `/home/linuxbrew/.linuxbrew/bin/llama-server`
- 模型目录设为 `~/models`
- settings.json 中 `~` 路径自动展开修复

## v0.0.3 — 2026-06-03

- 前端启动区精简：移除 Host、Ctx Size、GPU Layers、Threads、Batch Size、Ubatch Size 输入框
- 只保留 Port 和 Extra Args
- Host 默认改为 `0.0.0.0`，不在前端显示
- 增加 extra_args 校验：shlex 解析 + 危险字符过滤（`|><;&`$()#`）
- 增加 last_launch.json 保存上次启动参数
- 前端自动加载上次启动的 extra_args
- settings.json 精简，移除大部分默认参数

## v0.0.4 — 2026-06-03

- 新增 GGUF 模型下载功能
- 安装 huggingface_hub 依赖，使用 `hf download` CLI
- 后端新增 3 个端点：`/api/download`、`/api/download/status`、`/api/download/cancel`
- 前端新增下载区块：仓库ID、文件名输入框、Download/Cancel 按钮、状态徽章
- 下载状态每 3 秒自动刷新，完成后自动更新模型列表
- 下载日志独立存储到 `logs/download.log`
- requirements.txt 加入 `huggingface_hub`

## v0.0.5 — 2026-06-03

- 新增 spec.md 架构文档
- 新增 version.md 版本变更记录
- 新增 CLAUDE.md 项目指令（含版本号递增规则）
- 初始化 git 仓库并推送到 GitHub（https://github.com/HannisLee/LlamaManager）

## v0.0.6 — 2026-06-03

- 更新 README.md：配置表只保留实际配置项，移除 API 参考部分
- README.md 新增「下载模型」功能说明
- llama-server 默认端口从 8080 改为 8083
- `llama_server_path` 和 `model_dir` 不再显示默认值

## v0.0.7 — 2026-06-03

- 新增下载日志展示区块和 `/api/download/logs` 端点
- 下载输入校验：仓库名格式 `owner/repo`、文件名必须 `.gguf` 结尾
- 页面区块重排序：状态→启动服务→服务日志→下载模型→下载日志→设置
- 日志 textarea 高度优化：min 80px / max 300px / 默认 150px，可拖拽
- 服务日志和下载日志行数从 200 改为 100
- 下载日志每 5 秒自动刷新

## v0.0.8 — 2026-06-03

- 下载方式从 `hf` CLI 改为 Python `huggingface_hub.hf_hub_download` API，解决未认证问题
- 下载状态管理从 subprocess 改为标志位（`_download_running` / `_download_done` / `_download_error`）
- 后台线程执行下载，日志写入 `logs/download.log`
- 下载完成后状态返回 error 字段

## v0.0.9 — 2026-06-04

- 新增反向代理：`/llama/{path}` 转发到 llama-server，通过 frp 同一端口即可访问 llama-server WebUI
- 使用 httpx.AsyncClient 流式转发，支持 SSE 长连接
- llama-server 未运行时返回 503 错误
- "Open WebUI" 按钮改为打开 `/llama/` 代理地址
- requirements.txt 加入 httpx

## v0.0.10 — 2026-06-04

- 网站图标和页面标题替换为 icon.png
- `last_launch.json` 改为 `model_params.json`，按模型路径独立存储 port 和 extra_args
- 新增 `/api/model-params` 端点，一次性返回所有模型参数到前端
- 模型下拉框切换时自动加载对应模型的参数（未启动过的模型为空）
- 启动成功后自动保存该模型的参数
