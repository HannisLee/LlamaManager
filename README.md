# LlamaManager

一个极简的 llama.cpp Web 管理工具，通过单页面 WebUI 管理本机 llama-server 进程。

## 功能

- 扫描指定目录下的 GGUF 模型文件
- 选择模型并设置启动参数
- 一键启动 / 停止 / 重启 llama-server
- 实时查看运行状态和日志
- 端口被占用时自动 kill 占用进程（保护 SSH 等关键端口）

## 环境搭建

```bash
# 创建 conda 环境
conda create -n llama-manager python=3.12 -y
conda activate llama-manager

# 安装依赖
pip install -r requirements.txt
```

## 启动

```bash
conda activate llama-manager
bash run.sh
```

管理后台访问地址: http://localhost:8082

llama-server 服务地址（启动后）: http://127.0.0.1:8080

## 配置

所有配置保存在 `settings.json`，也可通过 WebUI 修改。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `llama_server_path` | `/home/lihan/llama.cpp/build/bin/llama-server` | llama-server 二进制路径 |
| `model_dir` | `/home/lihan/gguf` | GGUF 模型存放目录（递归扫描） |
| `host` | `127.0.0.1` | llama-server 监听地址 |
| `port` | `8080` | llama-server 监听端口 |
| `ctx_size` | `4096` | 上下文窗口大小 |
| `n_gpu_layers` | `99` | GPU 卸载层数 |
| `threads` | `8` | CPU 线程数 |
| `batch_size` | `512` | 批大小 |
| `ubatch_size` | `512` | 微批大小 |
| `extra_args` | `""` | 额外启动参数 |
| `auto_kill_port` | `true` | 端口占用时自动 kill |
| `protected_ports` | `[22]` | 受保护端口（不会被 kill） |

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 返回 WebUI 页面 |
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 保存配置 |
| GET | `/api/models` | 扫描模型列表 |
| GET | `/api/status` | 当前进程状态 |
| POST | `/api/start` | 启动 llama-server |
| POST | `/api/stop` | 停止进程 |
| POST | `/api/restart` | 重启进程 |
| GET | `/api/logs` | 读取日志尾部 |

## 安全提示

- 管理后台默认绑定 `0.0.0.0:8082`，可被同一网络内其他设备访问
- 如需公网部署，请自行配置防火墙或反向代理认证
- `protected_ports` 默认包含 `22`，防止误杀 SSH 连接
