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
| 模型下载 | huggingface_hub Python API（`hf_hub_download`） |
| 配置存储 | settings.json（无数据库） |
| 运行环境 | conda 环境 `llama-manager` |

## 项目结构

```
LlamaManager/
├── app.py              # FastAPI 后端主程序
├── index.html          # 单页面 WebUI
├── settings.json       # 持久化配置
├── model_params.json   # 按模型存储的启动参数（自动生成）
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
_current_process   # subprocess.Popen 实例
_current_command   # 启动命令列表
_current_model     # 当前模型路径
_current_port      # 当前端口
_current_host      # 当前绑定地址
_process_lock      # 进程操作互斥锁
```

**下载任务状态：**

```python
_download_running    # 是否正在下载
_download_done       # 是否下载完成
_download_repo       # 下载的仓库名
_download_filename   # 下载的文件名
_download_error      # 下载错误信息
_download_lock       # 下载操作互斥锁
```

**下载进度追踪：**

```python
_download_progress_n      # 已下载字节
_download_progress_total  # 总字节数
_DownloadTqdm             # 自定义 tqdm 类，将进度写入全局变量
```

### 工具函数

| 函数 | 功能 |
|------|------|
| `_load_settings()` | 读取 settings.json，自动展开 `~` 路径 |
| `_save_settings(data)` | 原子写入 settings.json（先写临时文件再 rename） |
| `_validate_extra_args(extra)` | 校验 extra_args，禁止 shell 注入字符 |
| `_load_model_params()` | 读取所有模型的启动参数（model_params.json） |
| `_save_model_param(model, port, extra_args)` | 保存单个模型的启动参数 |
| `_collect_gpu_status()` | 调用 nvidia-smi 采集 GPU 与进程信息 |
| `_infer_model_name(cmdline)` | 从进程命令行推断模型名 |
| `_build_command(settings, overrides)` | 拼接 llama-server 启动命令 |
| `_kill_port_occupant(port, protected)` | 杀掉占用端口的进程（跳过受保护端口和 PID 1） |
| `_stop_process_internal()` | 停止当前 llama-server 进程（terminate → wait 3s → kill） |

### API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 返回 index.html |
| GET | `/icon.png` | 返回网站图标 |
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 保存配置 |
| GET | `/api/models` | 递归扫描 model_dir 下 .gguf 文件 |
| GET | `/api/status` | 当前 llama-server 进程状态 |
| GET | `/api/gpus` | 当前 GPU 状态和 GPU 进程列表 |
| POST | `/api/start` | 启动 llama-server |
| POST | `/api/stop` | 停止 llama-server（同时清空日志） |
| POST | `/api/restart` | 使用该模型上次参数重启 |
| GET | `/api/model-params` | 获取所有模型的启动参数 |
| GET | `/api/logs` | 读取日志尾部 100 行 |
| POST | `/api/download` | 从 Hugging Face 下载模型（支持 `force_download`） |
| GET | `/api/download/status` | 查询下载状态（含进度信息） |
| GET | `/api/download/logs` | 读取下载日志尾部 100 行 |
| POST | `/api/download/cancel` | 取消下载 |
| GET/POST/... | `/llama/{path}` | 反向代理到 llama-server |

### `/api/gpus` 返回结构

```json
{
  "ok": true,
  "error": null,
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
4. 检查端口占用 → auto_kill_port 时自动 kill
5. 拼接命令：`llama-server -m <model> --host 0.0.0.0 --port <port> <extra_args>`
6. subprocess.Popen 启动，stdout/stderr 重定向到日志文件
7. 按模型保存启动参数到 model_params.json

**下载模型：**
1. 校验仓库名（`owner/repo`）和文件名（`.gguf` 结尾）
2. 检查是否有下载任务在运行
3. 预获取远程文件大小（`HfApi().model_info()`）
4. 后台线程调用 `hf_hub_download()` 下载，自定义 `_DownloadTqdm` 追踪进度
5. `force_download=False`（默认）时，HF 通过 ETag 校验已有文件，命中缓存则跳过
6. 前端轮询 `/api/download/status` 获取进度和百分比

**停止服务：**
1. terminate 进程 → 等待 3 秒 → kill
2. 清空服务日志文件（截断为空）

**GPU 监控：**
1. 调用 `nvidia-smi --query-gpu=index,name,driver_version,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits`
2. 调用 `nvidia-smi --query-compute-apps=gpu_uuid,gpu_bus_id,pid,used_memory,process_name --format=csv,noheader,nounits`
3. 进程归属优先按 `gpu_uuid` 映射到 GPU，失败时按 `gpu_bus_id` 映射；仍无法映射的进程行会被忽略
4. 使用 `psutil.Process(pid)` 补充 `username` 和完整命令行，权限不足或进程退出时使用 `Unknown` / 空字符串兜底
5. 从命令行参数 `--model`、`-m`、`--model-path`、`--model_name`、`--model-name`、`.gguf` 路径和常见模型目录名推断 `model_name`
6. `nvidia-smi` 不存在、驱动不可用或查询超时时，接口返回 JSON：`{"ok": false, "error": "...", "gpus": []}`

**端口冲突处理：**
- 使用 `psutil.net_connections(kind="inet")` 查找 LISTEN 状态连接
- `protected_ports`（默认 `[22]`）中的端口不会被 kill
- PID 1（init）不会被 kill
- terminate → 等待 3 秒 → kill

## 前端架构（index.html）

### 页面布局

单页面，暗色主题，max-width 1200px 居中。七个卡片区块纵向排列：

1. **状态区** — 运行状态徽章、PID、模型、URL、命令、操作按钮
2. **GPU 监控区** — 多 GPU 卡片、每排卡片数设置、GPU 进程表
3. **启动区** — 模型下拉（切换时自动加载该模型上次参数）、Port、Extra Args、auto_kill 开关、Start 按钮
4. **服务日志** — Refresh 按钮、readonly textarea
5. **下载区** — HF 仓库ID、文件名、Download/Cancel 按钮、状态徽章、进度条、强制重新下载复选框
6. **下载日志** — Refresh 按钮、readonly textarea
7. **设置区** — llama-server 路径、模型目录、Save/Rescan 按钮

GPU 监控区使用 CSS Grid 横向展示 GPU 卡片：
- `Auto`：`repeat(auto-fit, minmax(240px, 1fr))`
- `2 / 3 / 4`：固定每排 GPU 卡片数
- 窄屏下自动退化为单列
- 设置保存到 `localStorage.gpu_cards_per_row`

GPU 进程表由 `gpus[].processes[]` 展平，字段为 GPU、GPU Name、GPU Util、PID、Used Mem、Total Mem、Temp、Model Name。

### JavaScript 架构

所有 JS 内联在 `<script>` 标签中，无模块化。

**核心函数：**

| 函数 | 功能 |
|------|------|
| `api(method, path, body)` | 通用 API 调用封装，统一错误处理 |
| `showError(msg)` | 显示顶部错误横幅（5 秒自动消失） |
| `loadSettings()` | 加载配置并填充表单 |
| `loadModels()` | 扫描模型并填充下拉框 |
| `loadModelParams()` | 加载所有模型参数存入 `modelParams` |
| `onModelChange()` | 模型下拉切换时自动回填 port/extra_args |
| `loadStatus()` | 获取进程状态并更新 UI |
| `loadGpuStatus()` | 获取 GPU 状态和进程列表并更新 UI |
| `startServer()` | 收集表单参数调用 /api/start |
| `stopServer()` | 调用 /api/stop，清空日志显示 |
| `restartServer()` | 调用 /api/restart |
| `startDownload()` | 调用 /api/download（含 force_download） |
| `cancelDownload()` | 调用 /api/download/cancel |
| `loadDownloadStatus()` | 轮询下载状态，更新进度条和 UI |

**自动刷新：**
- 状态：每 3 秒
- GPU 监控：每 3 秒
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

## 安全设计

- extra_args 经过 shlex 解析和危险字符过滤（`|><;&`$()#`）
- protected_ports 防止误杀 SSH（端口 22）
- PID 1 永远不会被 kill
- settings.json 使用原子写入防止损坏
- 管理后台绑定 `0.0.0.0:8082`，README 中提醒公网暴露风险
