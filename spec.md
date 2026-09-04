# LlamaManager 架构文档

## 项目概述

LlamaManager 是一个本地多模型服务管理工具，通过单页面 WebUI 管理任意本机启动命令的运行、停止与重启，支持从 Hugging Face 下载模型，并提供多 GPU 监控与 GPU 进程列表展示。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI |
| 前端 | 原生 HTML/CSS/JS（无框架） |
| 进程管理 | subprocess + psutil |
| GPU 监控 | nvidia-smi + psutil |
| 模型下载 | huggingface_hub Python API（`hf_hub_download` 单文件 / `snapshot_download` 全量） |
| 配置存储 | settings.json（无数据库） |
| 运行环境 | conda 环境 `llama-manager` |

## 项目结构

```
LlamaManager/
├── app.py              # FastAPI 后端主程序
├── index.html          # 单页面 WebUI
├── settings.json       # 持久化配置与运行状态
├── requirements.txt    # Python 依赖
├── run.sh              # 启动脚本
├── logs/               # 日志目录
│   ├── llama-server.log    # llama-server 输出日志
│   ├── download.log        # 旧版下载任务日志
│   └── downloads/          # 多下载任务独立日志
├── spec.md             # 本文件，架构文档
├── version.md          # 版本变更记录
├── CLAUDE.md           # Claude Code 项目指令
└── README.md           # 使用说明
```

## 后端架构（app.py）

### 全局状态

后端使用模块级全局变量管理运行时状态，通过 `threading.Lock` 保证线程安全。

**llama-server 进程状态：**

```python
_managed_processes  # {pid: {process, command, model, host, port, gpu_indexes, started_at}}
_managed_process_records # {pid: record}，持久化到 settings.json.managed_processes
_current_process    # 最新存活 subprocess.Popen 实例，兼容旧状态接口
_current_command    # 最新实例启动命令列表
_current_model      # 最新实例模型路径
_current_port       # 最新实例端口
_current_host       # 最新实例绑定地址
_process_lock       # 进程操作互斥锁
```

**GPU 历史状态：**

```python
_gpu_history_lock            # 历史文件读写锁
_last_gpu_sample_ts          # 上次写入采样时间
GPU_SAMPLE_INTERVAL_SECONDS  # 采样最小间隔，当前为 5 秒
```

**下载任务状态：**

```python
_download_tasks  # {task_id: {repo, filename, target_dir, running, done, progress_n, progress_total, log_file}}
_download_lock   # 下载任务状态读写锁
```

### 工具函数

| 函数 | 功能 |
|------|------|
| `_load_settings()` | 读取 settings.json，自动展开 `~` 路径 |
| `_load_settings_raw()` | 读取 settings.json 原始内容，不展开路径 |
| `_save_settings(data)` | 原子写入 settings.json（先写临时文件再 rename） |
| `_save_settings_state(key, value)` | 保存 settings.json 中的内部状态字段 |
| `_migrate_legacy_state_files()` | 将旧版分散 JSON 状态迁移到 settings.json 并删除旧文件 |
| `_command_tokens(command)` | 校验命令引号并提取 token，用于识别模型名和可选端口 |
| `_load_custom_services()` | 从 settings.json 读取并迁移通用命令服务注册表 |
| `_normalize_custom_service(data)` | 校验并标准化服务名、完整启动命令和 GPU 选择 |
| `_load_managed_process_records_file()` | 从 settings.json 读取受管进程记录 |
| `_save_managed_process_records_file()` | 保存受管进程记录到 settings.json |
| `_restore_managed_process_records()` | 后端重启后按 PID 和进程创建时间恢复仍存活的受管进程 |
| `_collect_gpu_status()` | 调用 nvidia-smi 采集 GPU 与进程信息 |
| `_infer_model_name(cmdline)` | 从进程命令行推断模型名 |
| `_append_gpu_history_sample(gpus, history_hours)` | 将 GPU util 采样写入 settings.json.gpu_history |
| `_history_by_gpu(history_hours)` | 按 GPU index 读取最近 X 小时历史 |
| `_managed_process_snapshot()` | 获取当前存活受管实例快照 |
| `_managed_process_records_snapshot()` | 获取当前运行期已知进程日志记录 |
| `_managed_process_pid_map(managed)` | 将受管父进程及其子进程 PID 映射到受管根 PID |
| `_make_download_tqdm(task_id)` | 创建绑定指定下载任务的 tqdm 类，按任务写入字节进度 |
| `_download_task_snapshot(task)` | 生成下载任务 API 快照 |
| `_stop_process_internal(pid, clear_log)` | 停止指定或全部受管命令服务进程 |

### API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 返回 index.html |
| GET | `/icon.png` | 返回网站图标 |
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 保存配置 |
| GET | `/api/models` | 递归扫描 model_dir 下 .gguf 文件 |
| GET | `/api/model-repositories` | 扫描 model_dir 下全量下载的仓库目录 |
| GET | `/api/custom-services` | 读取已注册的通用命令服务列表 |
| POST | `/api/custom-services` | 注册或更新服务（服务名、服务类型、完整启动命令、GPU 选择） |
| DELETE | `/api/custom-services/{service_id}` | 删除已注册服务 |
| GET | `/api/status` | 当前 llama-server 进程状态 |
| GET | `/api/gpus` | 当前 GPU 状态、每卡 util 历史和受管进程列表 |
| GET | `/api/managed-processes` | 当前运行期已知受管进程和日志记录 |
| GET | `/asr` | 返回固定地址的 ASR 专用音频转写页 |
| GET | `/api/asr/history` | 返回本地 ASR 历史记录摘要（不含全文） |
| GET | `/api/asr/history/{record_id}/text` | 读取指定本地 ASR 历史的全文 |
| PATCH | `/api/asr/history/{record_id}` | 修改指定 ASR 历史记录的自定义名称 |
| DELETE | `/api/asr/history/{record_id}` | 删除已结束的 ASR 历史记录及其保存的全文 |
| GET | `/api/asr` | 获取唯一运行中的 ASR 实例信息 |
| POST | `/api/asr/transcriptions` | 上传一个音频，创建历史 item 并在后台队列中转写，立即返回 `202` |
| POST | `/api/start` | 启动已注册服务（model 为 `custom:<id>` 启 vLLM，或 `llama:<id>` 启 llama.cpp） |
| POST | `/api/stop` | 停止指定 PID 或全部受管实例 |
| POST | `/api/restart` | 重启指定 PID 或最新受管实例 |
| GET | `/api/model-params` | （已废弃）旧版按模型记忆参数，现统一由注册服务管理，返回空 |
| GET | `/api/logs?pid=<pid>` | 读取指定受管进程日志；不传 pid 时读取最新受管进程或默认日志 |
| POST | `/api/download` | 新增一个 Hugging Face 下载任务（指定文件名下单个文件，留空全量下载整个仓库；支持 `force_download`） |
| GET | `/api/download/status` | 查询下载任务列表（含每个任务的进度信息和 `target_dir`） |
| GET | `/api/download/logs?task_id=<id>` | 读取指定下载任务日志尾部 100 行；不传时读取最新任务 |
| POST | `/api/download/cancel` | 标记取消指定下载任务（`task_id`） |
| GET/POST/... | `/llama-process/{pid}/{path}` | 反向代理到指定受管 llama-server |
| GET/POST/... | `/{gpu_index}/{path}` | 反向代理到指定 GPU 上唯一运行中的标准 LLM 服务 |
| GET/POST/... | `/llama/{path}` | 反向代理到 llama-server |

### `/api/gpus` 返回结构

```json
{
  "ok": true,
  "error": null,
  "history_hours": 2,
  "managed_processes": [
    {
      "pid": 12345,
      "model_name": "Qwen3-ASR-1.7B.gguf",
      "port": 8083,
      "gpu_indexes": [0],
      "proxy_url": "/llama-process/12345/",
      "gpu": 0,
      "used_mem": 6326
    }
  ],
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA A100-PCIE-40GB",
      "driver_version": "535.129.03",
      "uuid": "GPU-...",
      "bus_id": "00000000:01:00.0",
      "gpu_util": 0,
      "used_mem": 6339,
      "total_mem": 40960,
      "temperature": 51,
      "process_count": 1,
      "users": ["user"],
      "history": [
        {"timestamp": 1717480000.0, "gpu_util": 20}
      ],
      "processes": [
        {
          "pid": 12345,
          "used_mem": 6326,
          "process_name": "python",
          "username": "user",
          "command": "python ...",
          "model_name": "Qwen3-ASR-1.7B"
        }
      ]
    }
  ]
}
```

### 核心流程

**统一服务管理（注册 + 启动）：**

所有本地模型服务统一在「服务管理」卡片中，先注册后启动。每个服务保存服务类型（`asr` 或 `llm`）、完整终端启动命令及可选的 GPU 选择，配置保存在 `settings.json.custom_services`。

**注册服务：**
1. 注册表单只包含服务名、服务类型（ASR / 标准 LLM）、完整启动命令和 GPU 选择；服务名留空时从 `--model` 等参数推断
2. POST `/api/custom-services` 校验命令引号，保存原始命令与 GPU 索引；若命令含合法 `--port`，仅被动记录该端口以支持 Open 与 Qwen ASR 页面，不作为必填项
3. 注册项 upsert 到 `custom_services`，编辑时复用原 service_id 覆盖
4. 旧 llama.cpp / vLLM 注册项会迁移为完整命令，保留原有服务与 GPU 配置

**启动已注册服务：**
1. POST `/api/start` 只接收 `service_id`，从注册表读取完整命令和 GPU 选择
2. 后端以 `nohup bash -lc <命令>` 创建独立会话；命令以 `conda` 开头时自动解析本机 Conda 绝对路径，`stdout/stderr` 全部重定向到 `logs/services/command-<name>-<port或process>-<timestamp>.log`
3. 勾选 GPU 时，进程环境注入 `CUDA_VISIBLE_DEVICES=<索引列表>`；未勾选则沿用系统可见 GPU
4. 不再自动清理占用端口、不再有增量启动开关，也不要求命令包含端口；端口冲突由服务自身写入日志报告
5. 写入 `_managed_processes[pid]` 和 `settings.json.managed_processes`，记录 `service_id` 与 `process_create_time`，支持后台重启后恢复管理
6. 「已注册服务」列表按 service_id 匹配受管进程：未运行显示「启动」，运行中提供停止/重启。ASR 始终打开 `/asr`；标准 LLM 仅在选择了唯一 GPU 且命令识别到端口时显示 Open，固定打开 `/{gpu_index}/`

**后台重启后的受管进程恢复：**
- 每次启动、停止、状态同步时，后端将受管进程元数据写入 `settings.json.managed_processes`
- 后台服务器重启后首次读取状态/GPU/日志/代理时，加载 `settings.json.managed_processes`
- 仅当记录中的 PID 仍存在且 `process_create_time` 与当前进程匹配时恢复为受管进程，避免 PID 复用导致误识别
- 恢复后的进程使用 `psutil.Process` 句柄，继续支持 Open / Stop / Restart、日志查看和 GPU 子进程归属

**服务日志：**
- 每个受管进程都有独立 `log_file`
- `/api/logs?pid=<pid>` 读取指定进程日志尾部 200 行
- 不传 pid 时读取最新受管进程日志；没有受管进程时回退到 `settings.log_file`

**ASR 专用转写：**
1. 服务注册时明确选择 ASR 或标准 LLM。ASR 服务的 Open 固定跳转到 `/asr`；标准 LLM 服务的 Open 仍打开原反向代理页
2. 专用页将服务与批量拖放/点击上传区置顶，转写历史放在下方；可一次选择或拖入多个音频，并行上传到 `/api/asr/transcriptions`；支持 aac、flac、m4a、mp3、mp4、ogg、opus、wav、webm，单文件最多 4 GB
3. 后端自动使用唯一运行中的 ASR 受管实例；未运行时返回 404，存在多个实例时返回 409。音频写入系统临时目录，并使用 ffmpeg 静音检测按语音边界切为 FLAC；初始单片最长 600 秒，若导出后的 FLAC 超过 16 MB 安全阈值则自动继续切分
4. 后端按顺序向该实例本机地址的 `/v1/audio/transcriptions` 发送 multipart 请求，自动从 `qwen-asr-serve` 命令读取模型路径，兼容 `text` 和 OpenAI `choices` 返回格式并去除 `<asr_text>` 标记；服务端仍返回“文件过大”时会再切分后重试该片段
5. 服务端为每个上传文件立即创建历史 item，并在后台队列执行转写；默认同时只运行 1 个任务，以避免单卡 vLLM 争抢资源。状态依次为「排队中 / 转写中 / 已完成 / 失败」
6. 所有临时音频和 FLAC 切片在任务结束后删除；成功转写的全文保存到 `data/asr_history/<记录 ID>.txt`，元数据保存到 `data/asr_history/records.json`，临时任务目录为 `data/asr_jobs/`，三者均不纳入 Git
7. 专用页以最新上传在前的时间倒序展示可展开 item，列表保存自定义名称、原始文件名、上传时间、时长、片段数和状态；只有点击「已完成」条目时才按需读取全文，并可修改记录名称或删除已结束的记录

**下载模型：**
1. 校验仓库名（`owner/repo`）；指定文件名时校验 `.gguf` 结尾，留空则全量下载
2. 每次提交创建独立 `task_id`，允许多个仓库或文件同时下载
3. 计算目标目录：单文件 → `model_dir`；全量 → `model_dir/owner--repo`（`/` 替换为 `--`）
4. 预获取远程文件总大小：单文件用 `model_info()` 取该文件 size，全量用 `repo_info(files_metadata=True)` 求 siblings size 之和
5. 后台线程按分支下载：单文件用 `hf_hub_download()`，全量用 `snapshot_download()`，均传按 `task_id` 绑定的 tqdm 类追踪进度
6. `force_download=False`（默认）时，HF 通过 ETag 校验已有文件，命中缓存则跳过
7. 每个任务日志写入 `logs/downloads/<task_id>.log`
8. 前端轮询 `/api/download/status` 获取任务列表、进度、百分比和 `target_dir`

**停止服务：**
1. 表格行 Stop 传入 PID，只停止对应受管实例，不清空日志
2. 不传 PID 时停止全部受管实例，并清空服务日志文件
3. 停止时递归处理受管父进程及其所有子进程，兼容 `conda run` / vLLM wrapper
4. terminate 进程树 → 等待 3 秒 → kill 仍存活的进程

**GPU 监控：**
1. 调用 `nvidia-smi --query-gpu=index,name,driver_version,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits`
2. 调用 `nvidia-smi --query-compute-apps=gpu_uuid,gpu_bus_id,pid,used_memory,process_name --format=csv,noheader,nounits`
3. 进程归属优先按 `gpu_uuid` 映射到 GPU，失败时按 `gpu_bus_id` 映射；仍无法映射的进程行会被忽略
4. GPU 进程 PID 会先通过 `_managed_process_pid_map()` 归属到 LlamaManager 启动的父服务 PID，兼容 `conda run` / vLLM 启动器由子进程实际占用 GPU 的情况
5. GPU 进程表只保留 `_managed_processes` 中仍存活的服务，系统或其他用户进程不进入前端进程表
6. 同一服务占用多张 GPU 时，GPU index/name 汇总展示，进程显存累加，总显存累加，GPU util/温度取最大值
7. 如果 `nvidia-smi` 暂时没有返回该服务的 compute-apps 行，但启动时选择了 GPU，则用所选 GPU 回填 GPU name/util/total mem/temp，进程显存保持空值
8. 每次 `/api/gpus` 采集时最多每 5 秒写入一条 GPU util 样本到 `settings.json.gpu_history`
9. 按 `settings.json.gpu_history_hours` 返回每张 GPU 最近 X 小时的 `history`，默认 2 小时
10. `nvidia-smi` 不存在、驱动不可用或查询超时时，优先从 `settings.json.gpu_history` 恢复 GPU 列表和波形，返回 `stale: true`
11. 本地历史也为空时，接口返回 JSON：`{"ok": false, "error": "...", "gpus": []}`

**端口冲突处理：**
- 使用 `psutil.net_connections(kind="inet")` 查找 LISTEN 状态连接
- `protected_ports`（默认 `[22]`）中的端口不会被 kill
- PID 1（init）不会被 kill
- terminate → 等待 3 秒 → kill

## 前端架构（index.html）

### 页面布局

单页面，暗色主题，max-width 1200px 居中。六个卡片区块纵向排列：

1. **GPU 监控区** — 每张 GPU 的 util 波形图、多 GPU 卡片、受管进程表
2. **服务管理区** — 顶部「已注册服务」列表（显示服务类型，按运行状态显示 启动 或 停止/重启/可选 Open，另可编辑/删除）、下方为服务名、服务类型、GPU 选择和完整启动命令的注册表单
3. **服务日志** — 下拉框仅展示当前仍在运行的受管任务，readonly textarea 显示对应实时日志尾部
4. **下载区** — HF 仓库ID、文件名（留空则全量下载整个仓库）、Download 按钮、强制重新下载复选框；可连续新增多个下载任务
5. **下载任务区** — 多任务进度列表、每任务 Cancel/Logs 操作、下载日志任务下拉、Refresh 按钮、readonly textarea
6. **设置区** — 模型下载目录、GPU 历史小时数与 Save 按钮

ASR 服务的 Open 会复用同一个 `index.html`，并固定通过 `/asr` 呈现独立转写页，不加载管理后台的轮询逻辑。该页自动使用唯一运行中的 ASR 实例；标准 LLM 服务按其唯一 GPU 暴露，例如 GPU 0 对应 `/0/`，GPU 1 对应 `/1/`，该地址会代理到相应服务端口。一个 GPU 上存在多个标准 LLM、服务未选择 GPU 或未运行时，接口返回明确错误而不猜测目标。顶部为服务与批量拖放/点击上传区，下方为按时间倒序自动刷新的历史记录；仅在点击已完成条目时显示全文，并支持名称修改。历史标题和名称编辑框均支持两行展示，状态徽章固定单行。

GPU 监控区使用 CSS Grid 横向展示 GPU 卡片：
- `Auto`：`repeat(auto-fit, minmax(240px, 1fr))`
- `2 / 3 / 4`：固定每排 GPU 卡片数
- 页面最大宽度限制为 1200px，因此每排最多 4 张 GPU；8 卡服务器显示为 2 排
- 窄屏下自动退化为单列
- 设置保存到 `localStorage.gpu_cards_per_row`

GPU 波形图区位于 GPU 卡片上方：
- 每张 GPU 单独一张 canvas 波形图
- 左侧纵轴固定显示 0 / 50 / 100
- 横轴显示过去 X 小时的起止时间
- X 小时来自 `settings.json.gpu_history_hours`，默认 2
- 当前硬件查询失败时，波形图仍可基于本地历史文件显示，并在页面顶部提示当前状态不可用

GPU 进程表只展示 LlamaManager 当前运行期启动的受管实例，字段为 GPU、GPU Name、GPU Util、PID、Used Mem、Total Mem、Temp、Model Name、Actions。Model Name 显示注册服务名（display_name，回退 model_name）；Actions 包含 Open、Stop、Restart。

### JavaScript 架构

所有 JS 内联在 `<script>` 标签中，无模块化。

**核心函数：**

| 函数 | 功能 |
|------|------|
| `api(method, path, body)` | 通用 API 调用封装，统一错误处理 |
| `showError(msg)` | 显示顶部错误横幅（5 秒自动消失） |
| `loadSettings()` | 加载配置并填充表单 |
| `detectLlamaCpp()` | 调用 `/api/detect-llama-cpp` 检测环境变量和 PATH，成功时填充 llama-server 路径 |
| `loadModels()` | 扫描 GGUF 模型并填充注册表单下拉框，刷新时保留当前已选模型 |
| `loadVllmRepositories()` | 扫描全量仓库目录并填充 vLLM 注册下拉框 |
| `onVllmRepositoryChange()` | vLLM 仓库目录切换时显示路径并更新命令模板 |
| `onServiceTypeChange()` | 注册表单 llama.cpp / vLLM 类型切换，显隐对应字段集 |
| `loadCustomServices()` | 加载已注册服务列表，刷新注册列表与启动列表 |
| `saveService()` | 按当前类型注册或更新服务（llama / vllm） |
| `editService(id)` | 回填注册表单并切到对应类型 |
| `deleteService(id)` | 删除已注册服务 |
| `resetServiceForm()` | 清空注册表单 |
| `renderRegisteredServices()` | 渲染「已注册服务」列表，按 service_id 匹配运行状态显示 启动/停止/重启/打开，另可编辑/删除 |
| `startRegisteredService(id, kind)` | 启动已注册服务（custom:<id> / llama:<id>） |
| `loadGpuStatus()` | 获取 GPU 状态、波形历史和受管进程列表并更新 UI |
| `drawGpuWaveform()` | 绘制单张 GPU util 波形图 |
| `loadManagedProcesses()` | 刷新受管进程并更新启动列表运行状态与日志选择器 |
| `stopManagedProcess(pid)` | 停止指定受管实例 |
| `restartManagedProcess(pid)` | 重启指定受管实例 |
| `openManagedWebUI(pid)` | ASR 服务打开固定转写页，标准 LLM 服务打开原代理页 |
| `initAsrPage()` | 初始化固定 ASR 页的批量拖放上传、后台任务状态和历史自动刷新 |
| `loadAsrHistory()` | 加载本地转写历史摘要并渲染可展开列表 |
| `toggleAsrHistoryItem(id)` | 展开或收起一条历史记录，展开时按需读取全文 |
| `saveAsrHistoryName(id)` | 保存历史记录的自定义名称 |
| `startDownload()` | 调用 /api/download 新增下载任务（含 force_download，filename 可留空触发全量下载） |
| `cancelDownload(taskId)` | 调用 /api/download/cancel 标记取消指定任务 |
| `loadDownloadStatus()` | 轮询下载任务列表，更新多任务进度和 UI |
| `loadDownloadLogs()` | 按下载任务下拉读取对应日志 |

**自动刷新：**
- GPU 监控：每 5 秒
- 日志：每 5 秒
- 下载状态：每 3 秒
- 下载完成时自动刷新模型列表

## 配置文件

### settings.json

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_dir` | string | `~/models` | GGUF 模型目录（递归扫描） |
| `gpu_history_hours` | number | `2` | GPU util 波形显示的历史小时数 |
| `model_params` | object | `{}` | （已废弃）按模型路径保存的启动参数，启动时迁移为 llama 注册项后清空 |
| `custom_services` | object | `{}` | 用户注册的通用命令服务 |
| `managed_processes` | object | `{"processes":[]}` | LlamaManager 启动过的受管进程记录 |
| `gpu_history` | object | `{"samples":[]}` | GPU util 历史采样 |

### 内部状态结构

`settings.json.model_params`（已废弃，启动时自动迁移为 llama 注册项后清空）曾按模型路径独立存储启动参数：

```json
{
  "/home/lihan/models/Qwen3-ASR-1.7B-Q8_0.gguf": {
    "port": 8083,
    "extra_args": "--jinja -ngl 99"
  }
}
```

`settings.json.custom_services` 保存用户注册的服务，按 `service_type` 区分 llama.cpp 与 vLLM（字段为两类超集，无关项为 null）：

```json
{
  "svc_aaa": {
    "id": "svc_aaa",
    "name": "Qwen3-ASR-1.7B",
    "service_category": "asr",
    "service_type": "llama",
    "model": "/home/lihan/models/Qwen3-ASR-1.7B-Q8_0.gguf",
    "port": 8083,
    "extra_args": "--jinja -ngl 99",
    "gpu_indexes": [],
    "command": null,
    "created_at": 1781070614.24
  },
  "svc_bbb": {
    "id": "svc_bbb",
    "name": "[vLLM]Qwen3-ASR-1.7B",
    "service_category": "asr",
    "service_type": "vllm",
    "model": null,
    "port": 8085,
    "extra_args": null,
    "gpu_indexes": null,
    "command": "conda run -n qwen3-asr qwen-asr-serve /home/lihan/models/Qwen3-ASR-1.7B --host 0.0.0.0 --port 8085",
    "created_at": 1781069153.0
  }
}
```

`settings.json.managed_processes` 保存 LlamaManager 启动过的受管进程记录：

```json
{
  "processes": [
    {
      "pid": 12345,
      "model": "/home/lihan/models/model.gguf",
      "display_name": "model.gguf",
      "host": "0.0.0.0",
      "port": 8083,
      "command": "llama-server -m /home/lihan/models/model.gguf --host 0.0.0.0 --port 8083",
      "command_tokens": ["llama-server", "-m", "/home/lihan/models/model.gguf", "--host", "0.0.0.0", "--port", "8083"],
      "log_file": "/home/lihan/run/LlamaManager/logs/services/llama-model.gguf-8083-20260615-120000.log",
      "process_create_time": 1781496000.0,
      "running": true
    }
  ]
}
```

`settings.json.gpu_history` 保存 GPU util 采样：

```json
{
  "samples": [
    {
      "timestamp": 1717480000.0,
      "gpus": [
        {"index": 0, "name": "NVIDIA A100-PCIE-40GB", "gpu_util": 42}
      ]
    }
  ]
}
```

旧版 `model_params.json`、`last_launch.json`、`custom_services.json`、`managed_processes.json`、`gpu_history.json` 会在应用启动时自动迁移到 `settings.json` 并删除。

此外，旧版按模型路径记忆的 `settings.json.model_params` 会在应用启动时自动迁移为 `custom_services` 中的 llama.cpp 注册项（name 取模型文件名；幂等：同 model 路径已存在则跳过），迁移完成后清空 `model_params`。

## 安全设计

- extra_args 经过 shlex 解析和危险字符过滤（`|><;&`$()#`）
- protected_ports 防止误杀 SSH（端口 22）
- PID 1 永远不会被 kill
- settings.json 使用原子写入防止损坏
- 管理后台绑定 `0.0.0.0:8081`，README 中提醒公网暴露风险
