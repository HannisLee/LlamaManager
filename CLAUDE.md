# CLAUDE.md — LlamaManager 项目指令

## 项目信息

- **项目名**: LlamaManager
- **仓库**: https://github.com/HannisLee/LlamaManager
- **架构文档**: 参见 [spec.md](spec.md)
- **变更记录**: 参见 [version.md](version.md)
- **环境**: conda 环境 `llama-manager`，Python 3.12
- **启动**: `conda activate llama-manager && bash run.sh`（管理后台 `0.0.0.0:8082`）

## 代码规范

- 后端：Python + FastAPI，所有 API 返回 JSON
- 前端：单个 index.html，原生 HTML/CSS/JS，无框架
- 配置：settings.json 存储所有配置，无数据库
- 语言：代码注释和文档全部使用中文

## 工作流程

**每次对话修改完成后，必须执行以下步骤：**

1. **更新 version.md**：在文件末尾追加本次修改内容，格式为 `### v版本号 — 日期`，列出具体变更点
2. **本地 git 提交**：`git add . && git commit -m "简短中文描述"`
3. **推送到远程**：`git push origin main`

不得跳过任何步骤。

## 关键约束

- 不要引入数据库、前端框架、构建工具
- 不要修改 run.sh 中的端口号（8082）和绑定地址（0.0.0.0），除非用户明确要求
- settings.json 中的路径使用 `~` 时，后端需要 `Path.expanduser()` 展开
- extra_args 必须经过 `_validate_extra_args()` 校验
- 所有新增 API 端点必须在 spec.md 中同步更新
