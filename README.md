# LlamaManager

一个极简的 llama.cpp Web 管理工具，通过单页面 WebUI 管理本机 llama-server 进程。

## 功能

- 扫描指定目录下的 GGUF 模型文件
- 从 Hugging Face 下载 GGUF 模型
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

## 配置

所有配置保存在 `settings.json`，也可通过 WebUI 修改。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `llama_server_path` | — | llama-server 二进制路径 |
| `model_dir` | — | GGUF 模型存放目录（递归扫描） |
| `host` | `0.0.0.0` | llama-server 监听地址 |
| `port` | `8083` | llama-server 监听端口 |
| `extra_args` | `""` | 额外启动参数（如 `-c 4096 --n-gpu-layers 99`） |
| `auto_kill_port` | `true` | 端口占用时自动 kill |
| `protected_ports` | `[22]` | 受保护端口（不会被 kill） |

## 安全提示

- 管理后台默认绑定 `0.0.0.0:8082`，可被同一网络内其他设备访问
- 如需公网部署，请自行配置防火墙或反向代理认证
- `protected_ports` 默认包含 `22`，防止误杀 SSH 连接
