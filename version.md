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

## v0.1.4 — 2026-06-15

- 下载模型新增仓库全量下载：文件名留空时下载整个仓库到 `model_dir/owner--repo/`（`/` 替换为 `--`）
- 全量下载使用 `huggingface_hub.snapshot_download`，文件保持仓库结构存放
- `_DownloadTqdm` 改造：用 `unit=="B"` 区分字节进度条，避免 `snapshot_download` 的文件数进度条污染全局变量
- 下载总大小改为预获取（单文件取该文件 size，全量求和 siblings size），进度条对两种场景都准确
- 文件名校验：filename 为空时跳过 `.gguf` 校验，支持全量下载
- `/api/download` 与 `/api/download/status` 新增 `target_dir` 字段，前端显示下载目标目录
- 前端文件名输入框 placeholder 改为提示“留空则全量下载整个仓库”
- 强制重新下载复选框对全量下载同样生效（`snapshot_download` 的 `force_download`）
- spec.md 同步更新下载分支和全量下载设计

## v0.1.5 — 2026-06-15

- 新增 `managed_processes.json` 持久化 LlamaManager 启动过的受管进程记录
- 后台服务重启后按 PID 和 `process_create_time` 恢复仍存活的受管进程，避免 PID 复用误识别
- 恢复后的受管进程继续支持 Open / Stop / Restart、日志查看和 GPU 子进程归属
- 受管进程命令新增 `command_tokens` 保存，避免带空格参数在重启恢复时被错误拆分
- spec.md 同步更新受管进程持久化和恢复流程

## v0.1.6 — 2026-06-15

- 修复模型列表刷新时会清空当前模型选择的问题
- 刷新模型列表时保留已选模型，且不再重置用户正在编辑的 Port / Extra Args
- spec.md 同步更新 `loadModels()` 前端行为说明

## v0.1.7 — 2026-06-15

- 启动服务区下方新增 vLLM 服务区，承载原自定义服务的注册、编辑、删除和启动操作
- 新增 `/api/model-repositories`，扫描 `model_dir` 下全量下载的仓库目录，如 `Qwen--Qwen3.5`
- vLLM 服务区新增仓库模型下拉框，展示可用全量仓库目录并显示实际路径
- 启动服务区模型下拉改为仅展示 GGUF 模型，自定义服务入口移入 vLLM 服务区
- 已注册 vLLM / 自定义服务列表新增“启动”按钮，可直接以受管进程运行服务
- spec.md 同步更新 vLLM 服务区、仓库目录扫描接口和前端函数说明

## v0.1.8 — 2026-06-16

- 设置区新增“检测 llama.cpp”按钮，可自动检索环境变量和 PATH 中的 llama-server
- 新增 `/api/detect-llama-cpp` 接口，支持从 `LLAMA_CPP`、`LLAMA_CPP_PATH`、`LLAMA_SERVER_PATH` 等环境变量推断 llama-server 路径
- 检测成功后前端自动填充 llama-server 路径，并显示来源和可执行状态
- spec.md 同步更新检测接口、工具函数和设置区说明

## v0.1.9 — 2026-06-16

- 将模型启动参数、自定义服务、受管进程记录和 GPU 历史统一存入 `settings.json`
- 应用启动时自动迁移并删除旧版 `model_params.json`、`last_launch.json`、`custom_services.json`、`managed_processes.json`、`gpu_history.json`
- `/api/settings` 继续只返回前端设置页需要的用户配置，避免内部历史状态拖慢页面加载
- 删除已废弃的独立状态 JSON 文件和对应 `.gitignore` 条目
- spec.md 同步更新单文件配置与内部状态结构说明

## v0.1.10 — 2026-06-16

- 下载模型改为多任务模式，允许同时新增多个 Hugging Face 仓库或文件下载
- `/api/download/status` 返回下载任务列表，每个任务独立展示进度、状态和目标目录
- 下载日志改为按任务写入 `logs/downloads/<task_id>.log`，前端支持下拉选择查看
- 下载表单只负责新增任务，下载进度和 Cancel/Logs 操作移到下方任务列表
- spec.md 同步更新多下载任务接口和页面行为说明

## v0.1.11 — 2026-06-18

- 合并「启动服务」与「vLLM 服务」两块卡片为统一的「服务管理」卡片：llama.cpp 与 vLLM 两类服务统一为「先注册、再从列表启动」模式，移除 llama.cpp 一次性启动表单
- 注册表单支持类型切换（llama.cpp / vLLM），字段随类型变化；新增「已注册服务」列表（编辑/删除）与「启动/运行中」列表（按 service_id 匹配运行状态，切换 启动 / 停止·重启·打开）
- `custom_services` 注册表扩展支持 `service_type=llama`（含 model/port/extra_args/gpu_indexes 字段），`_normalize_custom_service` 按 service_type 分 llama/vllm 两支校验，记录结构为两类字段超集
- `/api/start` 改为基于已注册服务：`custom:<id>` 启 vLLM、`llama:<id>` 启 llama.cpp，无前缀一律拒绝；llama.cpp 参数从注册表读取，两类进程记录均写入 `service_id` 供前端匹配运行状态
- 应用启动时自动将旧版 `model_params` 迁移为 llama.cpp 注册项并清空（name 取模型文件名，幂等），废弃按模型路径记忆启动参数的机制
- 设置区新增独立「默认端口」字段，解耦原启动表单对全局 port/auto_kill_port 配置的借用
- spec.md 同步更新端点语义、启动流程、数据结构与页面布局

## v0.1.12 — 2026-06-18

- 服务管理卡片：「已注册服务」列表上移到最上方，启动/停止/重启/打开按钮合并到每行，按 service_id 匹配运行状态切换（未运行→启动，运行中→停止/重启/打开）
- 删除独立的「启动/运行中」板块；auto_kill 与增量启动全局开关保留
- GPU 监控受管进程表的 Model Name 改为显示注册服务名（display_name，回退 model_name），便于按服务名识别进程
- spec.md 同步更新页面布局、GPU 进程表字段与前端函数说明

## v0.1.13 — 2026-07-01

- 统一管理后台端口文档：`CLAUDE.md` 中端口由 8082 更正为 8081，与 `run.sh` 实际监听端口一致
- 关键约束中的端口号同步更正为 8081

## v0.1.14 — 2026-08-09

- 同步 README、架构文档和项目指引中的管理后台默认端口为 `0.0.0.0:8081`

## v0.1.15 — 2026-08-31

- 修复以 `conda run` 启动 Qwen3-ASR 等自定义服务时，后台服务 PATH 未包含 Conda 导致的“找不到 conda”错误
- 启动前自动从 PATH、`CONDA_EXE` 及用户目录下常见 Conda 安装位置解析 Conda 的绝对路径
- spec.md 同步更新自定义服务的 Conda 命令解析流程

## v0.1.16 — 2026-09-04

- 为运行中的 Qwen3-ASR-1.7B vLLM 服务适配专用 Open 页面；其他服务仍打开原有代理 WebUI
- 专用页面支持选择音频、点击“开始解析”、显示完整转写文字并复制结果
- 后端新增 Qwen3-ASR 专用页面、实例信息和音频转写 API；音频按静音切为最长 10 分钟的本地 FLAC 片段后依次调用 `/v1/audio/transcriptions`
- 自动清理上传音频和切片临时文件，兼容 Qwen `<asr_text>` 标记及 OpenAI 风格返回
- spec.md 同步更新专用转写流程和 API 文档

## v0.1.17 — 2026-09-04

- Qwen3-ASR 专用页新增拖放上传区，支持将常见音频文件直接拖入选择
- 新增本地转写历史：全文保存到 `data/asr_history/`，JSON 索引保存上传时间、原始文件名、时长、片段数和自定义名称，不保留原始音频
- 新增历史列表、按需展开全文和记录名称修改功能
- 新增 ASR 历史读取、全文读取、名称修改 API，并将本地历史目录加入 Git 忽略
- spec.md 同步更新历史存储、拖放上传和 API 文档

## v0.1.18 — 2026-09-04

- Qwen3-ASR 专用页调整为服务与批量上传区置顶、转写历史置底，最新上传记录按时间倒序展示
- 拖放和文件选择均支持一次提交多个音频，前端并行上传，后端创建历史 item 后后台排队转写
- 历史记录新增排队中、转写中、已完成、失败状态；完成后不自动展示正文，仅点击已完成 item 才按需展开
- 后端新增单任务并发调度和临时任务目录清理，避免单卡 vLLM 被多路转写请求争抢
- spec.md 同步更新批量上传、状态流转和 API 响应语义

## v0.1.19 — 2026-09-04

- 转写历史标题改为最多两行显示，便于识别长视频文件名
- 已完成等状态徽章固定为单行展示，避免被长标题挤压换行
- 展开历史后的记录名称编辑框改为可调整大小的两行文本框，提升长名称的查看和修改体验

## v0.1.20 — 2026-09-04

- 修复长音频转写仅按时长切片导致 FLAC 实际文件过大、被 vLLM 以 HTTP 400 拒绝的问题
- 初始按静音边界和 10 分钟时长切分后，新增 16 MB FLAC 安全阈值；超限片段自动按实际大小继续切分
- 遇到 vLLM 返回“Maximum file size exceeded”时，自动再次细分该片段并重试
- spec.md 同步更新 Qwen3-ASR 的双重切片策略

## v0.1.21 — 2026-09-04

- Qwen3-ASR 转写历史新增删除按钮与删除 API，可删除已完成或失败的历史记录及其本地全文
- 转写排队中或进行中的记录暂不允许删除，避免与后台任务发生冲突
- spec.md 同步更新删除接口和历史记录操作说明

## v0.1.22 — 2026-09-04

- 服务注册改为通用本地命令模式：仅保留服务名、完整启动命令和 CUDA GPU 选择，移除 llama.cpp 路径、服务类型、端口与额外参数配置
- 启动时使用 `nohup bash -lc` 创建独立后台进程，stdout/stderr 自动写入每个服务的独立日志文件
- 移除自动清理端口与增量启动；端口仅从命令中被动识别，用于保留可选 Open 和 Qwen3-ASR 专用页面支持
- 服务日志下拉框仅显示当前运行中的受管任务，不再提供已停止任务的历史日志选择
- 旧 llama.cpp / vLLM 注册项自动迁移为完整启动命令，保留既有服务与 GPU 配置；spec.md 同步更新

## v0.1.23 — 2026-09-04

- 修复通用命令服务以后台环境启动时找不到 `conda` 的问题
- 对命令开头的 `conda` 自动解析本机 Conda 绝对路径，用户保存的原始命令保持不变

## v0.1.24 — 2026-09-04

- 将包含本机服务、进程状态和 GPU 监控记录的 `settings.json` 加入 Git 忽略，避免本地运行配置进入版本库

## v0.1.25 — 2026-09-04

- Qwen3-ASR 专用转写页固定为 `/asr`，后端自动使用唯一运行中的 Qwen3-ASR 服务，不再将 PID 写入页面和转写 API 地址
- 旧版 `/asr/{pid}` 地址自动重定向到固定的 `/asr`
- 服务管理中的 CUDA_VISIBLE_DEVICES 文案统一调整为「GPU」，底层仍按所选 GPU 注入 CUDA 环境变量
- spec.md 同步更新固定 ASR 地址、接口和页面说明

## v0.1.26 — 2026-09-04

- 服务管理新增明确的服务类型选择：ASR 或标准 LLM；旧服务按名称、模型和命令中的 ASR 标识自动迁移
- 打开按钮按服务类型适配：ASR 固定打开 `/asr`，标准 LLM 继续打开对应端口的代理 WebUI
- 受管进程记录保存服务类型，确保重启后台后仍可正确识别 ASR 服务
- spec.md 同步更新服务类型、打开行为和 ASR 实例选择说明

## v0.1.28 — 2026-09-04

- 回退按单个 GPU 编号暴露标准 LLM 服务的代理规则，恢复标准 LLM 按受管进程 PID 的代理地址，支持一个服务使用多张 GPU
- ASR 专用页继续固定使用 `/asr`

## v0.1.29 — 2026-09-04

- 已注册服务列表改为纵向对齐布局：服务类型、GPU 和启动命令均固定为独立行，标签列统一对齐
- 「使用全部 GPU」和长启动命令在对应字段行内对齐显示，操作按钮保持在右侧
- spec.md 同步更新服务管理列表布局说明

## v0.1.30 — 2026-09-04

- 设置区新增 OpenAI 兼容 API 地址和密钥配置，以及模型列表连接测试与结果提示
- API 密钥只写入本地 settings.json；读取设置或保存响应均不返回原始密钥，重新打开页面仅显示已配置状态
- 支持清除已保存密钥；连接测试由后端携带已保存密钥请求 `/models`
- spec.md 同步更新连接测试接口、设置页面和密钥存储说明

## v0.1.31 — 2026-09-04

- OpenAI 兼容 API 设置新增模型名称字段，支持手动输入
- 测试连接成功后自动读取模型列表并填充为可选候选项，未填写时自动选择第一个返回模型
- spec.md 同步更新模型名称配置和测试返回语义

## v0.1.32 — 2026-09-04

- ASR 已完成历史新增「原文 / 一键提取」操作；提取完成后按钮变为「已提取」，再次点击即可展示已保存的提取结果
- 信息提取使用已配置的 OpenAI 兼容 API、模型和可编辑提示词；结果单独保存到本地历史目录，并随历史记录删除
- 转写历史下方新增提取提示词设置与保存，默认提示词针对抖音音频转写去除冗余、提炼关键信息且禁止编造
- spec.md 同步更新 ASR 信息提取流程、结果存储和 API 文档

## v0.1.33 — 2026-09-04

- 修复 ASR 历史自动轮询每次整体重绘导致原文或提取结果阅读时滚动位置回到顶部的问题；无摘要变化时不再重绘，有变化时保留页面和文本框滚动位置
- 原文 / 已提取操作栏右侧新增「复制」按钮，可复制当前显示的文字
- spec.md 同步更新 ASR 历史刷新和复制行为说明

## v0.1.34 — 2026-09-04

- 新增 `/chat/{pid}` 通用模型聊天页，标准 LLM 服务的「打开」统一进入该页，不再依赖推理框架自带 WebUI
- 通用页通过受管服务反向代理兼容 OpenAI 风格 `/v1/models` 和 `/v1/chat/completions`，支持模型名称手动输入或选择、系统提示词、多轮上下文与流式输出
- ASR 服务继续使用固定的 `/asr` 专用转写页；spec.md 同步更新打开行为、页面和路由说明

## v0.1.35 — 2026-09-04

- 通用模型聊天页新增 llama.cpp 原生接口兼容：OpenAI 风格聊天请求失败时，自动使用 `/apply-template` 生成模型对应提示词并调用 `/completion` 流式生成
- 修复受管服务流式反向代理始终返回 HTTP 200 的问题，现会透传上游状态码，便于前端准确回退和展示错误
- spec.md 同步更新通用聊天页的接口兼容策略

## v0.1.36 — 2026-09-04

- 修复部分 llama.cpp 服务对 `/v1/chat/completions` 返回空流时未触发原生补全回退的问题；未收到可显示文字时同样自动改用 `/completion`
- spec.md 同步更新通用聊天页回退条件

## v0.1.37 — 2026-09-04

- 修复通用模型聊天页模板中的接口路径反引号未转义导致 JavaScript 运行时异常、页面空白的问题

## v0.1.38 — 2026-09-04

- 通用模型聊天页重做为参考 llama.cpp 原版 WebUI 的沉浸式布局：左侧对话栏、居中消息区与底部悬浮输入框，优化视觉层级和移动端布局
- 新增本地对话管理：同一服务按 PID 独立保存会话，支持新建、切换、重命名和删除，内容仅保存于浏览器 localStorage
- 每条用户、模型或错误消息均新增复制按钮；模型与系统提示词设置收纳到侧边栏
- spec.md 同步更新聊天页布局、会话存储与复制行为说明

## v0.1.39 — 2026-09-06

- ASR 上传不再按扩展名拦截，新增对 M4S 及所有可由 FFmpeg 解码媒体格式的支持，并继续统一转为 FLAC 切片后转写
- 上传选择器增加 M4S、MKV、TS 等媒体格式提示，页面明确说明本地转码流程
- 未安装 FFmpeg/FFprobe 时给出安装指引；独立 DASH M4S 缺少初始化信息时给出完整媒体或合并媒体的处理建议
- 新增 MEMORY.md，记录 ASR 转码链路、FFmpeg 依赖和 M4S 初始化段限制
- spec.md 同步更新 ASR 格式兼容与转码限制说明

## v0.1.40 — 2026-09-06

- ASR 上传区新增逐文件真实上传进度条，显示上传百分比和已传输字节数
- ASR 历史记录新增上传、排队、FFmpeg 分析、自动转写、完成和失败等阶段状态与进度条，任务进行中不允许删除
- FFmpeg 静音分析读取原生进度输出；转写阶段按已完成片段数显示百分比，并标识当前是 FFmpeg 转码还是等待 ASR 返回
- 进度状态持久化到转写历史，刷新页面后仍能查看后台任务的当前阶段和进展
- spec.md 与 MEMORY.md 同步记录 ASR 进度机制
