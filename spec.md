# LlamaManager 架构文档

## 项目概述

LlamaManager 是一个极简的 llama.cpp Web 管理工具，通过单页面 WebUI 管理本机 llama-server 进程的启动、停止、重启，并支持从 Hugging Face 下载 GGUF 模型。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI |
| 前端 | 原生 HTML/CSS/JS（无框架） |
| 进程管理 | subprocess + psutil |
| 模型下载 | huggingface_hub（`hf` CLI） |
| 配置存储 | settings.json（无数据库） |
| 运行环境 | conda 环境 `llama-manager` |

## 项目结构

```
LlamaManager/
├── app.py              # FastAPI 后端主程序
├── index.html          # 单页面 WebUI
├── settings.json       # 持久化配置
├── last_launch.json    # 上次启动参数（自动生成）
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
_download_process   # subprocess.Popen 实例
_download_repo      # 下载的仓库名
_download_filename  # 下载的文件名
_download_done      # 是否下载完成
_download_lock      # 下载操作互斥锁
```

### 工具函数

| 函数 | 功能 |
|------|------|
| `_load_settings()` | 读取 settings.json，自动展开 `~` 路径 |
| `_save_settings(data)` | 原子写入 settings.json（先写临时文件再 rename） |
| `_validate_extra_args(extra)` | 校验 extra_args，禁止 shell 注入字符 |
| `_load_last_launch()` | 读取上次启动参数 |
| `_save_last_launch(data)` | 保存启动参数到 last_launch.json |
| `_build_command(settings, overrides)` | 拼接 llama-server 启动命令 |
| `_kill_port_occupant(port, protected)` | 杀掉占用端口的进程（跳过受保护端口和 PID 1） |
| `_stop_process_internal()` | 停止当前 llama-server 进程（terminate → wait 3s → kill） |

### API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 返回 index.html |
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 保存配置 |
| GET | `/api/models` | 递归扫描 model_dir 下 .gguf 文件 |
| GET | `/api/status` | 当前 llama-server 进程状态 |
| POST | `/api/start` | 启动 llama-server |
| POST | `/api/stop` | 停止 llama-server |
| POST | `/api/restart` | 使用上次参数重启 |
| GET | `/api/last-launch` | 获取上次启动参数 |
| GET | `/api/logs` | 读取日志尾部 200 行 |
| POST | `/api/download` | 从 Hugging Face 下载模型 |
| GET | `/api/download/status` | 查询下载状态 |
| POST | `/api/download/cancel` | 取消下载 |

### 核心流程

**启动 llama-server：**
1. 读取 settings.json 合并请求参数
2. 校验 extra_args（shlex 解析 + 危险字符过滤）
3. 校验 llama-server 二进制和模型文件存在
4. 检查端口占用 → auto_kill_port 时自动 kill
5. 拼接命令：`llama-server -m <model> --host 0.0.0.0 --port <port> <extra_args>`
6. subprocess.Popen 启动，stdout/stderr 重定向到日志文件
7. 保存启动参数到 last_launch.json

**下载模型：**
1. 校验仓库名和文件名不为空
2. 检查是否有下载任务在运行
3. 拼接命令：`hf download <repo> <filename> --local-dir <model_dir>`
4. subprocess.Popen 启动，后台线程监控完成状态
5. 前端轮询 `/api/download/status` 获取进度

**端口冲突处理：**
- 使用 `psutil.net_connections(kind="inet")` 查找 LISTEN 状态连接
- `protected_ports`（默认 `[22]`）中的端口不会被 kill
- PID 1（init）不会被 kill
- terminate → 等待 3 秒 → kill

## 前端架构（index.html）

### 页面布局

单页面，暗色主题，max-width 900px 居中。五个卡片区块纵向排列：

1. **状态区** — 运行状态徽章、PID、模型、URL、命令、操作按钮
2. **设置区** — llama-server 路径、模型目录、Save/Rescan 按钮
3. **下载区** — HF 仓库ID、文件名、Download/Cancel 按钮、状态徽章
4. **启动区** — 模型下拉、Port、Extra Args、auto_kill 开关、Start 按钮
5. **日志区** — Refresh 按钮、readonly textarea

### JavaScript 架构

所有 JS 内联在 `<script>` 标签中，无模块化。

**核心函数：**

| 函数 | 功能 |
|------|------|
| `api(method, path, body)` | 通用 API 调用封装，统一错误处理 |
| `showError(msg)` | 显示顶部错误横幅（5 秒自动消失） |
| `loadSettings()` | 加载配置并填充表单 |
| `loadModels()` | 扫描模型并填充下拉框 |
| `loadLastLaunch()` | 加载上次启动参数回填表单 |
| `loadStatus()` | 获取进程状态并更新 UI |
| `startServer()` | 收集表单参数调用 /api/start |
| `stopServer()` | 调用 /api/stop |
| `restartServer()` | 调用 /api/restart |
| `startDownload()` | 调用 /api/download |
| `cancelDownload()` | 调用 /api/download/cancel |
| `loadDownloadStatus()` | 轮询下载状态并更新 UI |

**自动刷新：**
- 状态：每 3 秒
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
| `port` | int | `8080` | llama-server 端口 |
| `extra_args` | string | `""` | 额外启动参数 |
| `log_file` | string | `./logs/llama-server.log` | 日志文件路径 |
| `auto_kill_port` | bool | `true` | 端口占用时自动 kill |
| `protected_ports` | array | `[22]` | 受保护端口 |

### last_launch.json

自动生成，记录上次成功启动的参数：model、host、port、extra_args。

## 安全设计

- extra_args 经过 shlex 解析和危险字符过滤（`|><;&`$()#`）
- protected_ports 防止误杀 SSH（端口 22）
- PID 1 永远不会被 kill
- settings.json 使用原子写入防止损坏
- 管理后台绑定 `0.0.0.0:8082`，README 中提醒公网暴露风险
