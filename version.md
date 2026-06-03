# LlamaManager 版本变更记录

## v0.1.0 — 2026-06-03

### 初始版本

- 创建项目基础结构：app.py、index.html、settings.json、requirements.txt、run.sh、README.md
- 基于 FastAPI 后端 + 原生 HTML/JS 单页面 WebUI
- 实现 llama-server 进程管理：启动、停止、重启
- 实现端口冲突自动检测与 kill（保护端口 22 和 PID 1）
- 实现 GGUF 模型扫描（递归扫描 model_dir）
- 实现日志查看（尾部 200 行）
- 配置持久化到 settings.json（原子写入）
- conda 环境 `llama-manager`，管理后台绑定 `0.0.0.0:8000`

### v0.2.0 — 2026-06-03

- 管理后台端口从 8000 改为 8082
- 扫描本机 llama-server 路径，定位到 `/home/linuxbrew/.linuxbrew/bin/llama-server`
- 模型目录从 `/home/lihan/gguf` 改为 `~/models`
- settings.json 中 `~` 路径自动展开修复

### v0.3.0 — 2026-06-03

- 前端启动区精简：移除 Host、Ctx Size、GPU Layers、Threads、Batch Size、Ubatch Size 输入框
- 只保留 Port 和 Extra Args
- Host 默认改为 `0.0.0.0`，不在前端显示
- 增加 extra_args 校验：shlex 解析 + 危险字符过滤（`|><;&`$()#`）
- 增加 last_launch.json 保存上次启动参数
- 前端自动加载上次启动的 extra_args
- settings.json 精简，移除大部分默认参数

### v0.4.0 — 2026-06-03

- 新增 GGUF 模型下载功能
- 安装 huggingface_hub 依赖，使用 `hf download` CLI
- 后端新增 3 个端点：`/api/download`、`/api/download/status`、`/api/download/cancel`
- 前端新增下载区块：仓库ID、文件名输入框、Download/Cancel 按钮、状态徽章
- 下载状态每 3 秒自动刷新，完成后自动更新模型列表
- 下载日志独立存储到 `logs/download.log`
- requirements.txt 加入 `huggingface_hub`

### v0.5.0 — 2026-06-03

- 新增 spec.md 架构文档
- 新增 version.md 版本变更记录
- 新增 CLAUDE.md 项目指令
- 初始化 git 仓库并推送到 GitHub
