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

## v0.0.11 — 2026-06-04

- 停止 llama-server 后自动清空服务日志（截断日志文件）
- 下载模型新增进度条展示（百分比 + 已下载/总大小 MB）
- 后端自定义 `_DownloadTqdm` 类追踪 `hf_hub_download` 下载进度
- 下载前预获取远程文件大小（`HfApi().model_info()`）
- `/api/download/status` 新增 `progress` 字段返回下载进度
- 新增"强制重新下载"复选框（`force_download` 参数）
- HF 默认通过 ETag 校验已有文件，未勾选时命中缓存则跳过下载
- spec.md 架构文档全面更新同步

## v0.0.12 — 2026-06-04

- 新增 `/api/gpus` GPU 监控接口，基于 `nvidia-smi` 采集多 GPU 状态和 GPU 进程列表
- GPU 进程补充 username、command，并从 `--model`、`-m`、`.gguf` 路径等参数推断 Model Name
- 前端新增 GPU 监控区块，支持多 GPU 卡片横向展示、自动换行和绿色利用率状态条
- 新增 Cards per row 设置，支持 Auto / 2 / 3 / 4，并通过 localStorage 保存显示偏好
- 新增 GPU 进程表，按多卡汇总展示 GPU、PID、进程显存、总显存、温度和模型名
- `nvidia-smi` 不存在或驱动不可用时返回友好错误提示，页面不会报错
- spec.md 同步更新 GPU 监控接口、数据结构和采集流程

## v0.0.13 — 2026-06-04

- GPU 监控顶部新增每卡独立 util 波形图，纵轴固定 0 / 50 / 100，横轴显示过去 X 小时
- GPU util 历史采样保存到本地 `gpu_history.json`，前端每 5 秒刷新并驱动后端采样
- 设置区新增 GPU 历史小时数，默认 2 小时
- 移除独立状态卡片，将 Open / Stop / Restart 操作移动到受管进程表
- 后端进程管理扩展为多实例 `_managed_processes`，支持增量启动多个 llama-server
- 启动服务新增增量启动和选择 GPU，选卡通过 `CUDA_VISIBLE_DEVICES` 限制可见 GPU
- GPU 进程表只展示 LlamaManager 启动的受管进程，不展示系统或外部进程
- 新增按 PID 代理路径 `/llama-process/{pid}/...`，用于打开指定实例 WebUI
- 新增 `.gitignore` 忽略本地 GPU 历史文件
- `nvidia-smi` 不可用时，GPU 波形图改为基于本地 `gpu_history.json` 兜底展示
- 修复 `/llama-process/{pid}/` 根路径代理，确保进程表 Open 按钮能打开指定实例

## v0.1.0 — 2026-06-10

- 新增自定义服务注册功能，支持将 vLLM / ASR 等非 llama.cpp 启动命令加入模型下拉框
- 新增 `custom_services.json` 本地注册表，并加入 `.gitignore`
- 自定义服务启动命令由后端统一解析、补齐 host/port，并由 LlamaManager 负责后台运行和日志重定向
- 启动服务区新增“添加自定义服务”表单，服务项在模型下拉框中以 `[VLLM]` 等类型标识
- 受管进程新增 service_kind、service_type、display_name、log_file 等元数据
- 服务日志改为按进程选择展示，`/api/logs` 支持 `pid` 参数
- 服务日志框高度增大，默认显示更多日志内容
- 新增 `/api/custom-services` 和 `/api/managed-processes` API
- spec.md 同步更新自定义服务和多进程日志设计

## v0.1.1 — 2026-06-10

- 自定义服务新增已注册列表，支持编辑和删除已有服务命令
- 编辑自定义服务时复用原 service_id，并保留原始 created_at
- GPU 进程采集新增受管父进程与子进程 PID 映射，兼容 vLLM / conda 启动器由子进程实际占用 GPU 的情况
- GPU 进程表支持将同一服务的多 GPU 占用汇总展示
- `nvidia-smi` 未返回 compute-apps 行但启动时选择了 GPU 时，使用所选 GPU 回填 GPU Name、Util、Total Mem、Temp，Used Mem 保持空值
- spec.md 同步更新自定义服务管理和 GPU PID 映射逻辑

## v0.1.2 — 2026-06-10

- 自定义服务编辑表单移除类型和 Port 输入，只保留服务名和启动命令
- 自定义服务端口改为只从启动命令中的 `--port` 解析，缺少端口时返回明确错误
- 启动自定义服务时不再用前端 Port 覆盖命令内容，注册命令按原样执行
- 选择自定义服务时，启动区隐藏 Port 和 Extra Args
- 自定义服务下拉和列表保留解析出的端口展示，便于确认服务入口
- spec.md 同步更新自定义服务端口来源和 UI 行为

## v0.1.3 — 2026-06-10

- `/api/start` 响应新增 host 和 port 字段，方便前端展示启动结果
- 启动按钮点击后新增 Starting 状态，避免 vLLM 冷启动期间看起来没有反馈
- 启动成功后在启动区显示 PID 和 Port，并提示查看服务日志
- 启动成功后自动刷新受管进程并切换到新 PID 的服务日志
- 未选择模型或自定义服务时，前端直接提示错误
- 停止服务时递归终止受管进程树，避免 `conda run` / vLLM 子进程残留占用端口
- spec.md 同步更新启动反馈流程
