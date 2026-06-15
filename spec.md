# LlamaManager 架构文档

## 项目概述

LlamaManager 是一个极简的 llama.cpp Web 管理工具，通过单页面 WebUI 管理本机 llama-server 进程的启动、停止、重启，支持从 Hugging Face 下载 GGUF 模型，并提供多 GPU 监控与 GPU 进程列表展示。

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
├── settings.json       # 持久化配置
├── model_params.json   # 按模型存储的启动参数（自动生成）
├── gpu_history.json    # GPU util 历史采样（自动生成，git 忽略）
├── custom_services.json # 自定义服务注册表（自动生成，git 忽略）
├── managed_processes.json # 受管进程记录（自动生成，git 忽略）
├── requirements.txt    # Python 依赖
├── run.sh              # 启动脚本
├── logs/               # 日志目录
│   ├── llama-server.log    # llama-server 输出日志
│   └── download.log        # 下载任务日志
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
_managed_process_records # {pid: record}，持久化到 managed_processes.json
_current_process    # 最新存活 subprocess.Popen 实例，兼容旧状态接口
_current_command    # 最新实例启动命令列表
_current_model      # 最新实例模型路径
_current_port       # 最新实例端口
_current_host       # 最新实例绑定地址
_process_lock       # 进程操作互斥锁
```

**GPU 历史状态：**

```python
GPU_HISTORY_PATH             # gpu_history.json 本地历史文件
_gpu_history_lock            # 历史文件读写锁
_last_gpu_sample_ts          # 上次写入采样时间
GPU_SAMPLE_INTERVAL_SECONDS  # 采样最小间隔，当前为 5 秒
```

**下载任务状态：**

```python
_download_running    # 是否正在下载
_download_done       # 是否下载完成
_download_repo       # 下载的仓库名
_download_filename   # 下载的文件名（留空=全量下载）
_download_target_dir # 下载目标目录（全量时为 model_dir/owner--repo）
_download_error      # 下载错误信息
_download_lock       # 下载操作互斥锁
```

**下载进度追踪：**

```python
_download_progress_n      # 已下载字节
_download_progress_total  # 总字节数（下载前预获取）
_DownloadTqdm             # 自定义 tqdm 类，用 unit=="B" 区分字节进度条，
                          # 避免 snapshot_download 的文件数进度条污染
```

### 工具函数

| 函数 | 功能 |
|------|------|
| `_load_settings()` | 读取 settings.json，自动展开 `~` 路径 |
| `_save_settings(data)` | 原子写入 settings.json（先写临时文件再 rename） |
| `_validate_extra_args(extra)` | 校验 extra_args，禁止 shell 注入字符 |
| `_load_model_params()` | 读取所有模型的启动参数（model_params.json） |
| `_save_model_param(model, port, extra_args)` | 保存单个模型的启动参数 |
| `_load_custom_services()` | 读取自定义服务注册表 |
| `_normalize_custom_service(data)` | 校验并标准化自定义服务命令 |
| `_load_managed_process_records_file()` | 读取 `managed_processes.json` 中的受管进程记录 |
| `_save_managed_process_records_file()` | 原子写入受管进程记录 |
| `_restore_managed_process_records()` | 后端重启后按 PID 和进程创建时间恢复仍存活的受管进程 |
| `_collect_gpu_status()` | 调用 nvidia-smi 采集 GPU 与进程信息 |
| `_infer_model_name(cmdline)` | 从进程命令行推断模型名 |
| `_append_gpu_history_sample(gpus, history_hours)` | 将 GPU util 采样写入 gpu_history.json |
| `_history_by_gpu(history_hours)` | 按 GPU index 读取最近 X 小时历史 |
| `_managed_process_snapshot()` | 获取当前存活受管实例快照 |
| `_managed_process_records_snapshot()` | 获取当前运行期已知进程日志记录 |
| `_managed_process_pid_map(managed)` | 将受管父进程及其子进程 PID 映射到受管根 PID |
| `_build_command(settings, overrides)` | 拼接 llama-server 启动命令 |
| `_build_custom_command(service)` | 按注册命令原样构建自定义服务启动命令 |
| `_kill_port_occupant(port, protected)` | 杀掉占用端口的进程（跳过受保护端口和 PID 1） |
| `_stop_process_internal(pid, clear_log)` | 停止指定或全部受管 llama-server 进程 |

### API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 返回 index.html |
| GET | `/icon.png` | 返回网站图标 |
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 保存配置 |
| GET | `/api/models` | 递归扫描 model_dir 下 .gguf 文件 |
| GET | `/api/custom-services` | 读取自定义服务注册表 |
| POST | `/api/custom-services` | 注册或更新自定义服务 |
| DELETE | `/api/custom-services/{service_id}` | 删除自定义服务 |
| GET | `/api/status` | 当前 llama-server 进程状态 |
| GET | `/api/gpus` | 当前 GPU 状态、每卡 util 历史和受管进程列表 |
| GET | `/api/managed-processes` | 当前运行期已知受管进程和日志记录 |
| POST | `/api/start` | 启动 llama-server，支持增量启动和 GPU 选择 |
| POST | `/api/stop` | 停止指定 PID 或全部受管实例 |
| POST | `/api/restart` | 重启指定 PID 或最新受管实例 |
| GET | `/api/model-params` | 获取所有模型的启动参数 |
| GET | `/api/logs?pid=<pid>` | 读取指定受管进程日志；不传 pid 时读取最新受管进程或默认日志 |
| POST | `/api/download` | 从 Hugging Face 下载模型（指定文件名下单个文件，留空全量下载整个仓库；支持 `force_download`） |
| GET | `/api/download/status` | 查询下载状态（含进度信息和 `target_dir`） |
| GET | `/api/download/logs` | 读取下载日志尾部 100 行 |
| POST | `/api/download/cancel` | 取消下载 |
| GET/POST/... | `/llama-process/{pid}/{path}` | 反向代理到指定受管 llama-server |
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

**启动 llama-server：**
1. 读取 settings.json 合并请求参数
2. 校验 extra_args（shlex 解析 + 危险字符过滤）
3. 校验 llama-server 二进制和模型文件存在
4. 读取 `incremental_start`：为 false 时先停止全部受管实例，为 true 时追加启动新实例
5. 读取 `gpu_indexes`：非空时设置 `CUDA_VISIBLE_DEVICES=0,1`
6. 检查端口占用 → auto_kill_port 时自动 kill；增量启动时禁止复用已受管端口
7. 拼接命令：`llama-server -m <model> --host 0.0.0.0 --port <port> <extra_args>`
8. subprocess.Popen 启动，stdout/stderr 重定向到日志文件
9. 写入 `_managed_processes[pid]` 和 `managed_processes.json`，记录 `process_create_time` 用于重启后校验 PID 身份
10. 按模型保存启动参数到 model_params.json

**启动自定义服务：**
1. 前端注册服务命令到 `custom_services.json`，如 `conda run -n qwen3-asr qwen-asr-serve ...`
2. 模型下拉使用 `custom:<service_id>` 标识自定义服务，并显示服务名和从命令解析出的端口
3. 启动时后端使用 `shlex.split` 解析命令，不使用 shell，不要求用户写 `> log 2>&1 &`
4. 自定义服务命令必须显式包含 `--port` 或 `--port=<port>`，后端只解析端口用于端口冲突检测、日志命名和代理记录，不再覆盖命令
5. stdout/stderr 重定向到 `logs/services/<type>-<name>-<port>-<timestamp>.log`
6. 自定义服务同样写入 `_managed_processes`，可在进程表中 Open / Stop / Restart
7. 已注册自定义服务可在启动区直接编辑或删除，编辑时复用原 service_id 覆盖注册表记录
8. 点击 Start 后前端禁用按钮并显示启动中提示，启动成功后展示 PID / Port 并自动切换到新进程日志

**后台重启后的受管进程恢复：**
- 每次启动、停止、状态同步时，后端将受管进程元数据写入 `managed_processes.json`
- 后台服务器重启后首次读取状态/GPU/日志/代理时，加载 `managed_processes.json`
- 仅当记录中的 PID 仍存在且 `process_create_time` 与当前进程匹配时恢复为受管进程，避免 PID 复用导致误识别
- 恢复后的进程使用 `psutil.Process` 句柄，继续支持 Open / Stop / Restart、日志查看和 GPU 子进程归属

**服务日志：**
- 每个受管进程都有独立 `log_file`
- `/api/logs?pid=<pid>` 读取指定进程日志尾部 200 行
- 不传 pid 时读取最新受管进程日志；没有受管进程时回退到 `settings.log_file`

**下载模型：**
1. 校验仓库名（`owner/repo`）；指定文件名时校验 `.gguf` 结尾，留空则全量下载
2. 检查是否有下载任务在运行
3. 计算目标目录：单文件 → `model_dir`；全量 → `model_dir/owner--repo`（`/` 替换为 `--`）
4. 预获取远程文件总大小：单文件用 `model_info()` 取该文件 size，全量用 `repo_info(files_metadata=True)` 求 siblings size 之和
5. 后台线程按分支下载：单文件用 `hf_hub_download()`，全量用 `snapshot_download()`，均传 `_DownloadTqdm` 追踪进度
6. `force_download=False`（默认）时，HF 通过 ETag 校验已有文件，命中缓存则跳过
7. 前端轮询 `/api/download/status` 获取进度、百分比和 `target_dir`

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
8. 每次 `/api/gpus` 采集时最多每 5 秒写入一条 GPU util 样本到 `gpu_history.json`
9. 按 `settings.json.gpu_history_hours` 返回每张 GPU 最近 X 小时的 `history`，默认 2 小时
10. `nvidia-smi` 不存在、驱动不可用或查询超时时，优先从 `gpu_history.json` 恢复 GPU 列表和波形，返回 `stale: true`
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
2. **启动区** — 模型/自定义服务下拉、Port、Extra Args、auto_kill、增量启动、GPU 选择、Start 按钮、自定义服务注册表单和已注册服务编辑/删除列表；选择自定义服务时隐藏 Port / Extra Args
3. **服务日志** — 进程日志下拉、Refresh 按钮、readonly textarea
4. **下载区** — HF 仓库ID、文件名（留空则全量下载整个仓库）、Download/Cancel 按钮、状态徽章、进度条、强制重新下载复选框，状态文本显示下载目标目录
5. **下载日志** — Refresh 按钮、readonly textarea
6. **设置区** — llama-server 路径、模型目录、GPU 历史小时数、Save/Rescan 按钮

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

GPU 进程表只展示 LlamaManager 当前运行期启动的受管实例，字段为 GPU、GPU Name、GPU Util、PID、Used Mem、Total Mem、Temp、Model Name、Actions。Actions 包含 Open、Stop、Restart。

### JavaScript 架构

所有 JS 内联在 `<script>` 标签中，无模块化。

**核心函数：**

| 函数 | 功能 |
|------|------|
| `api(method, path, body)` | 通用 API 调用封装，统一错误处理 |
| `showError(msg)` | 显示顶部错误横幅（5 秒自动消失） |
| `loadSettings()` | 加载配置并填充表单 |
| `loadModels()` | 扫描模型并填充下拉框 |
| `saveCustomService()` | 注册自定义服务命令 |
| `editCustomService(id)` | 编辑已有自定义服务 |
| `deleteCustomService(id)` | 删除已有自定义服务 |
| `loadModelParams()` | 加载所有模型参数存入 `modelParams` |
| `onModelChange()` | 模型下拉切换时自动回填普通模型参数；自定义服务隐藏 Port / Extra Args |
| `loadGpuStatus()` | 获取 GPU 状态、波形历史和受管进程列表并更新 UI |
| `drawGpuWaveform()` | 绘制单张 GPU util 波形图 |
| `startServer()` | 收集表单参数调用 /api/start |
| `loadManagedProcesses()` | 刷新日志进程选择器 |
| `stopManagedProcess(pid)` | 停止指定受管实例 |
| `restartManagedProcess(pid)` | 重启指定受管实例 |
| `startDownload()` | 调用 /api/download（含 force_download，filename 可留空触发全量下载） |
| `cancelDownload()` | 调用 /api/download/cancel |
| `loadDownloadStatus()` | 轮询下载状态，更新进度条和 UI |

**自动刷新：**
- GPU 监控：每 5 秒
- 日志：每 5 秒
- 下载状态：每 3 秒
- 下载完成时自动刷新模型列表

## 配置文件

### settings.json

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llama_server_path` | string | `/home/linuxbrew/.linuxbrew/bin/llama-server` | llama-server 二进制路径 |
| `model_dir` | string | `~/models` | GGUF 模型目录（递归扫描） |
| `host` | string | `0.0.0.0` | llama-server 绑定地址 |
| `port` | int | `8083` | llama-server 端口 |
| `extra_args` | string | `""` | 额外启动参数 |
| `log_file` | string | `./logs/llama-server.log` | 日志文件路径 |
| `auto_kill_port` | bool | `true` | 端口占用时自动 kill |
| `protected_ports` | array | `[22]` | 受保护端口 |
| `gpu_history_hours` | number | `2` | GPU util 波形显示的历史小时数 |

### gpu_history.json

自动生成并由 `.gitignore` 忽略，保存 GPU util 采样：

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

### model_params.json

自动生成，按模型路径独立存储启动参数：

```json
{
  "/home/lihan/models/Qwen3-ASR-1.7B-Q8_0.gguf": {
    "port": 8083,
    "extra_args": "--jinja -ngl 99"
  }
}
```

### custom_services.json

自动生成并由 `.gitignore` 忽略，保存用户注册的非 llama.cpp 服务命令：

```json
{
  "svc_xxx": {
    "id": "svc_xxx",
    "name": "Qwen3-ASR-1.7B",
    "service_type": "custom",
    "command": "conda run -n qwen3-asr qwen-asr-serve /home/lihan/models/Qwen3-ASR-1.7B --gpu-memory-utilization 0.8 --host 0.0.0.0 --port 8085",
    "port": 8085,
    "created_at": 1781069153.0
  }
}
```

### managed_processes.json

自动生成并由 `.gitignore` 忽略，保存 LlamaManager 启动过的受管进程记录：

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

## 安全设计

- extra_args 经过 shlex 解析和危险字符过滤（`|><;&`$()#`）
- protected_ports 防止误杀 SSH（端口 22）
- PID 1 永远不会被 kill
- settings.json 使用原子写入防止损坏
- 管理后台绑定 `0.0.0.0:8082`，README 中提醒公网暴露风险
