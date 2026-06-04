"""
LlamaManager - 轻量级 llama.cpp Web 管理工具
通过单页面 WebUI 管理 llama-server 进程
"""

import csv
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

import httpx
import psutil
import tqdm as _tqdm_module
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

# ── 路径常量 ──────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "settings.json"
MODEL_PARAMS_PATH = APP_DIR / "model_params.json"

# ── 全局进程状态 ──────────────────────────────────────────
_current_process: Optional[subprocess.Popen] = None
_current_command: Optional[list] = None
_current_model: Optional[str] = None
_current_port: Optional[int] = None
_current_host: Optional[str] = None
_process_lock = threading.Lock()

# ── 下载状态 ────────────────────────────────────────────
_download_running: bool = False
_download_done: bool = False
_download_repo: Optional[str] = None
_download_filename: Optional[str] = None
_download_error: Optional[str] = None
_download_lock = threading.Lock()

# ── 下载进度追踪 ──────────────────────────────────────
_download_progress_n: int = 0
_download_progress_total: int = 0


class _DownloadTqdm(_tqdm_module.tqdm):
    """自定义 tqdm，将下载进度写入全局变量供 API 查询"""

    def __init__(self, *args, **kwargs):
        global _download_progress_n, _download_progress_total
        super().__init__(*args, **kwargs)
        _download_progress_total = self.total or 0
        _download_progress_n = 0

    def update(self, n=1):
        global _download_progress_n
        result = super().update(n)
        _download_progress_n = self.n
        return result

app = FastAPI(title="LlamaManager")

# ── 反向代理 ────────────────────────────────────────────
_proxy_client: Optional[httpx.AsyncClient] = None


def _get_proxy_client() -> httpx.AsyncClient:
    """获取或创建 llama-server 代理客户端"""
    global _proxy_client
    settings = _load_settings()
    host = settings.get("host", "0.0.0.0")
    port = settings.get("port", 8083)
    base_url = f"http://127.0.0.1:{port}"
    if _proxy_client is None or str(_proxy_client.base_url) != base_url:
        if _proxy_client is not None:
            # 基地址变了，关闭旧的
            import asyncio
            try:
                asyncio.get_event_loop().create_task(_proxy_client.aclose())
            except Exception:
                pass
        _proxy_client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=5.0),
        )
    return _proxy_client


# ── 工具函数 ──────────────────────────────────────────────

def _load_settings() -> dict:
    """读取 settings.json，文件不存在或解析失败返回空 dict"""
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            # 展开 ~ 路径
            for key in ("llama_server_path", "model_dir", "log_file"):
                if key in data and isinstance(data[key], str):
                    data[key] = str(Path(data[key]).expanduser())
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_settings(data: dict) -> dict:
    """原子写入 settings.json，返回写入后的完整 settings"""
    existing = _load_settings()
    existing.update(data)
    # 原子写入：先写临时文件再 rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(APP_DIR), suffix=".json.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(SETTINGS_PATH))
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return existing


def _validate_extra_args(extra: str) -> Optional[str]:
    """
    校验 extra_args，返回错误信息或 None（表示通过）。
    """
    if not extra or not extra.strip():
        return None
    try:
        tokens = shlex.split(extra)
    except ValueError as e:
        return f"Extra args 解析失败: {e}"
    # 禁止 shell 注入字符
    dangerous = {"|", ">", "<", ";", "&", "`", "$", "(", ")", "#"}
    for t in tokens:
        if any(c in t for c in dangerous):
            return f"Extra args 包含不允许的字符: '{t}'"
    return None


def _load_model_params() -> dict:
    """读取所有模型的启动参数，格式: {model_path: {port, extra_args}}"""
    try:
        if MODEL_PARAMS_PATH.exists():
            return json.loads(MODEL_PARAMS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_model_param(model: str, port: int, extra_args: str):
    """保存单个模型的启动参数"""
    params = _load_model_params()
    params[model] = {"port": port, "extra_args": extra_args}
    try:
        MODEL_PARAMS_PATH.write_text(
            json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _to_int(value, default=0) -> int:
    """将 nvidia-smi 的数值字段转为 int，N/A 或空值返回默认值"""
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _parse_csv_lines(output: str) -> list:
    """解析 nvidia-smi CSV 输出"""
    if not output.strip():
        return []
    return [
        [cell.strip() for cell in row]
        for row in csv.reader(output.splitlines(), skipinitialspace=True)
        if row
    ]


def _run_nvidia_smi(args: list) -> tuple[Optional[list], Optional[str]]:
    """执行 nvidia-smi 查询，返回 CSV 行或错误信息"""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None, "未找到 nvidia-smi，请确认已安装 NVIDIA 驱动和工具"

    try:
        proc = subprocess.run(
            [nvidia_smi, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi 查询超时"
    except OSError as e:
        return None, f"nvidia-smi 执行失败: {e}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return None, detail or f"nvidia-smi 返回错误码 {proc.returncode}"

    return _parse_csv_lines(proc.stdout), None


def _model_name_from_value(value: str, require_model_hint: bool = False) -> Optional[str]:
    """从模型参数值中提取可读模型名"""
    if not value:
        return None
    text = value.strip().strip("'\"")
    if not text or text.startswith("-"):
        return None
    if text.lower().endswith(".gguf"):
        return Path(text).name
    if "/" in text or "\\" in text:
        name = Path(text).name
        if name and (
            not require_model_hint
            or re.search(r"(model|gguf|llama|qwen|deepseek|mistral|mixtral|gemma|yi|hy-)", name, re.I)
        ):
            return name
        return None
    if re.search(r"(model|gguf|llama|qwen|deepseek|mistral|mixtral|gemma|yi|hy-)", text, re.I):
        return text
    return None


def _infer_model_name(cmdline: list) -> str:
    """从进程命令行推断模型名，无法识别返回 Unknown"""
    if not cmdline:
        return "Unknown"

    tokens = [str(t) for t in cmdline if str(t).strip()]
    model_flags = {"--model", "-m", "--model-path", "--model_name", "--model-name"}
    model_prefixes = (
        "--model=",
        "--model-path=",
        "--model_name=",
        "--model-name=",
    )

    for i, token in enumerate(tokens):
        for prefix in model_prefixes:
            if token.startswith(prefix):
                name = _model_name_from_value(token[len(prefix):])
                if name:
                    return name
        if token in model_flags and i + 1 < len(tokens):
            name = _model_name_from_value(tokens[i + 1])
            if name:
                return name

    for token in tokens:
        if token.lower().endswith(".gguf"):
            return Path(token.strip("'\"")).name

    for token in tokens:
        name = _model_name_from_value(token, require_model_hint=True)
        if name:
            return name

    return "Unknown"


def _get_process_detail(pid: int) -> dict:
    """读取进程用户名和命令行，权限不足时返回兜底值"""
    detail = {
        "username": "Unknown",
        "command": "",
        "cmdline": [],
    }
    try:
        proc = psutil.Process(pid)
        try:
            detail["username"] = proc.username() or "Unknown"
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
        try:
            cmdline = proc.cmdline()
            detail["cmdline"] = cmdline
            detail["command"] = shlex.join(cmdline) if cmdline else ""
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    except (psutil.AccessDenied, psutil.NoSuchProcess, ValueError):
        pass
    return detail


def _collect_gpu_status() -> dict:
    """采集 GPU 和进程信息"""
    gpu_fields = [
        "index",
        "name",
        "driver_version",
        "uuid",
        "pci.bus_id",
        "utilization.gpu",
        "memory.used",
        "memory.total",
        "temperature.gpu",
    ]
    gpu_rows, gpu_error = _run_nvidia_smi([
        f"--query-gpu={','.join(gpu_fields)}",
        "--format=csv,noheader,nounits",
    ])
    if gpu_error:
        return {"ok": False, "error": gpu_error, "gpus": []}

    gpus = []
    uuid_to_gpu = {}
    bus_id_to_gpu = {}
    for row in gpu_rows or []:
        if len(row) < len(gpu_fields):
            continue
        gpu = {
            "index": _to_int(row[0]),
            "name": row[1],
            "driver_version": row[2],
            "uuid": row[3],
            "bus_id": row[4],
            "gpu_util": _to_int(row[5]),
            "used_mem": _to_int(row[6]),
            "total_mem": _to_int(row[7]),
            "temperature": _to_int(row[8]),
            "process_count": 0,
            "users": [],
            "processes": [],
        }
        gpus.append(gpu)
        if gpu["uuid"]:
            uuid_to_gpu[gpu["uuid"]] = gpu
        if gpu["bus_id"]:
            bus_id_to_gpu[gpu["bus_id"]] = gpu

    process_fields = ["gpu_uuid", "gpu_bus_id", "pid", "used_memory", "process_name"]
    process_rows, process_error = _run_nvidia_smi([
        f"--query-compute-apps={','.join(process_fields)}",
        "--format=csv,noheader,nounits",
    ])

    if not process_error:
        for row in process_rows or []:
            if len(row) < len(process_fields):
                continue
            gpu = uuid_to_gpu.get(row[0]) or bus_id_to_gpu.get(row[1])
            if gpu is None:
                continue

            pid = _to_int(row[2], default=-1)
            proc_detail = _get_process_detail(pid) if pid > 0 else {
                "username": "Unknown",
                "command": "",
                "cmdline": [],
            }
            process = {
                "pid": pid if pid > 0 else None,
                "used_mem": _to_int(row[3]),
                "process_name": row[4],
                "username": proc_detail["username"],
                "command": proc_detail["command"],
                "model_name": _infer_model_name(proc_detail["cmdline"]),
            }
            gpu["processes"].append(process)

    for gpu in gpus:
        users = sorted({
            p["username"]
            for p in gpu["processes"]
            if p.get("username") and p.get("username") != "Unknown"
        })
        gpu["process_count"] = len(gpu["processes"])
        gpu["users"] = users

    return {"ok": True, "error": process_error, "gpus": gpus}


def _build_command(settings: dict, overrides: dict) -> list:
    """拼接 llama-server 启动命令：基础参数 + extra_args"""
    s = {**settings, **overrides}
    cmd = [s["llama_server_path"], "-m", s["model"]]
    cmd += ["--host", str(s.get("host", "0.0.0.0"))]
    cmd += ["--port", str(s.get("port", 8080))]

    extra = s.get("extra_args", "").strip()
    if extra:
        cmd += shlex.split(extra)

    return cmd


def _kill_port_occupant(port: int, protected: list) -> Optional[dict]:
    """
    杀掉占用指定端口的进程。
    返回被杀进程信息 {"pid": ..., "name": ...} 或 None。
    """
    if port in protected:
        return None

    killed = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
            pid = conn.pid
            if pid is None or pid == 1:
                continue
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                killed.append({"pid": pid, "name": proc_name})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    return killed[0] if killed else None


def _stop_process_internal() -> Optional[str]:
    """停止当前管理的 llama-server 进程，返回停止信息或 None"""
    global _current_process, _current_command, _current_model, _current_port, _current_host

    if _current_process is None or _current_process.poll() is not None:
        _current_process = None
        _current_command = None
        _current_model = None
        _current_port = None
        _current_host = None
        return None

    info = f"PID {_current_process.pid}"
    _current_process.terminate()
    try:
        _current_process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _current_process.kill()
        _current_process.wait(timeout=2)

    _current_process = None
    _current_command = None
    _current_model = None
    _current_port = None
    _current_host = None

    # 停止后清空日志文件
    try:
        settings = _load_settings()
        log_file = settings.get("log_file", "./logs/llama-server.log")
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = APP_DIR / log_path
        if log_path.exists():
            open(log_path, "w", encoding="utf-8").close()
    except OSError:
        pass

    return info


# ── API 端点 ──────────────────────────────────────────────

@app.get("/")
async def index():
    """返回 index.html"""
    return FileResponse(APP_DIR / "index.html")


@app.get("/icon.png")
async def get_icon():
    """返回网站图标"""
    return FileResponse(APP_DIR / "icon.png", media_type="image/png")


@app.get("/api/settings")
async def get_settings():
    """读取 settings.json"""
    return JSONResponse(_load_settings())


@app.post("/api/settings")
async def save_settings(data: dict):
    """保存 settings.json"""
    try:
        saved = _save_settings(data)
        return JSONResponse({"ok": True, "data": saved})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/models")
async def get_models():
    """扫描 model_dir 下所有 .gguf 文件（递归）"""
    settings = _load_settings()
    model_dir = settings.get("model_dir", "")
    models = []

    if model_dir and Path(model_dir).is_dir():
        for p in sorted(Path(model_dir).rglob("*.gguf")):
            try:
                stat = p.stat()
                models.append({
                    "name": p.name,
                    "path": str(p),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except OSError:
                pass

    return JSONResponse({"models": models, "model_dir": model_dir})


@app.get("/api/status")
async def get_status():
    """获取当前 llama-server 运行状态"""
    with _process_lock:
        running = (
            _current_process is not None
            and _current_process.poll() is None
        )
        url = None
        if running and _current_host and _current_port:
            url = f"http://{_current_host}:{_current_port}"

        return JSONResponse({
            "running": running,
            "pid": _current_process.pid if running else None,
            "command": " ".join(_current_command) if running and _current_command else None,
            "model": _current_model if running else None,
            "host": _current_host if running else None,
            "port": _current_port if running else None,
            "url": url,
        })


@app.get("/api/gpus")
async def get_gpus():
    """获取 GPU 状态和 GPU 进程列表"""
    return JSONResponse(_collect_gpu_status())


@app.post("/api/start")
async def start_server(body: dict = None):
    """启动 llama-server"""
    global _current_process, _current_command, _current_model, _current_port, _current_host

    if body is None:
        body = {}

    with _process_lock:
        # 如果已有进程在运行，先停止
        if _current_process is not None and _current_process.poll() is None:
            _stop_process_internal()

        settings = _load_settings()

        # 合并参数
        model = body.get("model") or settings.get("model", "")
        host = body.get("host") or settings.get("host", "0.0.0.0")
        port = body.get("port") or settings.get("port", 8080)
        port = int(port)
        extra_args = body.get("extra_args", "")

        # 校验 extra_args
        err = _validate_extra_args(extra_args)
        if err:
            raise HTTPException(status_code=400, detail=err)

        llama_server_path = settings.get("llama_server_path", "")
        auto_kill_port = body.get("auto_kill_port", settings.get("auto_kill_port", True))
        protected_ports = settings.get("protected_ports", [22])

        # 校验 llama-server 路径
        if not llama_server_path or not Path(llama_server_path).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"llama-server 不存在: {llama_server_path}"
            )

        # 校验模型文件
        if not model or not Path(model).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"模型文件不存在: {model}"
            )

        # 处理端口占用
        killed_info = None
        if auto_kill_port:
            killed_info = _kill_port_occupant(port, protected_ports)

        # 构建启动命令
        overrides = {
            "model": model,
            "host": host,
            "port": port,
            "extra_args": extra_args,
        }

        cmd = _build_command(settings, overrides)

        # 准备日志目录
        log_file = settings.get("log_file", "./logs/llama-server.log")
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = APP_DIR / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 启动进程
        try:
            log_fh = open(log_path, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"启动失败: {str(e)}"
            )

        _current_process = proc
        _current_command = cmd
        _current_model = model
        _current_port = port
        _current_host = host

        # 按模型保存启动参数
        _save_model_param(model, port, extra_args)

        result = {
            "ok": True,
            "pid": proc.pid,
            "url": f"http://{host}:{port}",
            "command": " ".join(cmd),
            "killed": killed_info,
        }
        return JSONResponse(result)


@app.post("/api/stop")
async def stop_server():
    """停止 llama-server"""
    with _process_lock:
        info = _stop_process_internal()
        if info is None:
            return JSONResponse({"ok": True, "status": "already_stopped"})
        return JSONResponse({"ok": True, "status": "stopped", "detail": f"Stopped {info}"})


@app.post("/api/restart")
async def restart_server():
    """重启 llama-server（使用上一次的参数）"""
    with _process_lock:
        # 停止当前进程
        _stop_process_internal()

    # 从内存状态获取上次参数
    if not _current_model:
        raise HTTPException(
            status_code=400,
            detail="没有可重启的参数（之前未启动过）"
        )

    # 从 model_params 获取该模型的参数
    params = _load_model_params()
    model_param = params.get(_current_model, {})
    last = {
        "model": _current_model,
        "port": model_param.get("port", 8083),
        "extra_args": model_param.get("extra_args", ""),
    }

    return await start_server(last)


@app.get("/api/model-params")
async def get_model_params():
    """获取所有模型的启动参数"""
    return JSONResponse(_load_model_params())


@app.get("/api/logs")
async def get_logs():
    """读取日志文件尾部"""
    settings = _load_settings()
    log_file = settings.get("log_file", "./logs/llama-server.log")
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = APP_DIR / log_path

    if not log_path.exists():
        return JSONResponse({"logs": ""})

    try:
        lines = log_path.read_text(errors="replace").splitlines()
        return JSONResponse({"logs": "\n".join(lines[-100:])})
    except Exception as e:
        return JSONResponse({"logs": f"读取日志失败: {str(e)}"})


# ── 下载 API ────────────────────────────────────────────

@app.post("/api/download")
async def download_model(body: dict = None):
    """从 Hugging Face 下载模型"""
    global _download_running, _download_done, _download_repo, _download_filename, _download_error
    global _download_progress_n, _download_progress_total

    if body is None:
        body = {}

    repo = body.get("repo", "").strip()
    filename = body.get("filename", "").strip()
    force_download = body.get("force_download", False)

    if not repo:
        raise HTTPException(status_code=400, detail="请输入仓库名")
    if not filename:
        raise HTTPException(status_code=400, detail="请输入文件名")

    # 校验仓库格式：owner/repo
    if not re.match(r'^[a-zA-Z0-9_.\-]+\/[a-zA-Z0-9_.\-]+$', repo):
        raise HTTPException(status_code=400, detail="仓库名格式错误，应为 owner/repo（如 tencent/Hy-MT2-7B-GGUF）")
    # 校验文件名：必须 .gguf 结尾
    if not re.match(r'^[a-zA-Z0-9_.\-]+\.gguf$', filename):
        raise HTTPException(status_code=400, detail="文件名格式错误，必须以 .gguf 结尾")

    with _download_lock:
        # 检查是否正在下载
        if _download_running:
            raise HTTPException(status_code=409, detail="已有下载任务在运行")

        settings = _load_settings()
        model_dir = settings.get("model_dir", "")
        if not model_dir:
            raise HTTPException(status_code=400, detail="未设置模型目录")

        # 确保 model_dir 存在
        Path(model_dir).mkdir(parents=True, exist_ok=True)

        # 准备下载日志
        download_log = APP_DIR / "logs" / "download.log"
        download_log.parent.mkdir(parents=True, exist_ok=True)

        _download_repo = repo
        _download_filename = filename
        _download_done = False
        _download_running = True
        _download_error = None
        _download_progress_n = 0
        _download_progress_total = 0

        # 获取远程文件大小
        remote_size = None
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            model_info = api.model_info(repo_id=repo, files_metadata=True)
            for sibling in (model_info.siblings or []):
                if sibling.rfilename == filename:
                    remote_size = sibling.size
                    break
        except Exception:
            pass

        def _do_download():
            """后台线程执行下载"""
            global _download_done, _download_running, _download_error
            global _download_progress_n, _download_progress_total
            try:
                from huggingface_hub import hf_hub_download
                size_info = f" ({remote_size / 1024 / 1024:.1f} MB)" if remote_size else ""
                log_msg = f"[{repo}/{filename}] 开始下载到 {model_dir}{size_info}\n"
                download_log.write_text(log_msg, encoding="utf-8")

                path = hf_hub_download(
                    repo_id=repo,
                    filename=filename,
                    local_dir=model_dir,
                    force_download=force_download,
                    tqdm_class=_DownloadTqdm,
                )

                with open(download_log, "a", encoding="utf-8") as f:
                    f.write(f"[{repo}/{filename}] 下载完成: {path}\n")
                _download_done = True
            except Exception as e:
                _download_error = str(e)
                with open(download_log, "a", encoding="utf-8") as f:
                    f.write(f"[{repo}/{filename}] 下载失败: {e}\n")
                _download_done = True
            finally:
                _download_running = False

        threading.Thread(target=_do_download, daemon=True).start()

        return JSONResponse({
            "ok": True,
            "repo": repo,
            "filename": filename,
            "message": "下载已启动",
        })


@app.get("/api/download/status")
async def download_status():
    """查询下载状态"""
    with _download_lock:
        # 构建进度信息
        progress = None
        if _download_progress_total > 0:
            pct = _download_progress_n / _download_progress_total * 100
            progress = {
                "downloaded": _download_progress_n,
                "total": _download_progress_total,
                "percentage": round(pct, 1),
                "downloaded_mb": round(_download_progress_n / 1024 / 1024, 1),
                "total_mb": round(_download_progress_total / 1024 / 1024, 1),
            }
        return JSONResponse({
            "running": _download_running,
            "done": _download_done,
            "repo": _download_repo,
            "filename": _download_filename,
            "error": _download_error,
            "progress": progress,
        })


@app.post("/api/download/cancel")
async def cancel_download():
    """取消当前下载（标记取消，线程会自行结束）"""
    global _download_running, _download_done

    with _download_lock:
        if not _download_running:
            return JSONResponse({"ok": True, "status": "no_download_running"})

        _download_running = False
        _download_done = True

        download_log = APP_DIR / "logs" / "download.log"
        try:
            with open(download_log, "a", encoding="utf-8") as f:
                f.write(f"[{_download_repo}/{_download_filename}] 下载已取消\n")
        except OSError:
            pass

        return JSONResponse({"ok": True, "status": "cancelled"})


@app.get("/api/download/logs")
async def get_download_logs():
    """读取下载日志尾部"""
    download_log = APP_DIR / "logs" / "download.log"

    if not download_log.exists():
        return JSONResponse({"logs": ""})

    try:
        lines = download_log.read_text(errors="replace").splitlines()
        return JSONResponse({"logs": "\n".join(lines[-100:])})
    except Exception as e:
        return JSONResponse({"logs": f"读取日志失败: {str(e)}"})


# ── 反向代理 llama-server ──────────────────────────────

@app.api_route("/llama/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_to_llama(path: str, request: Request):
    """反向代理到 llama-server，支持流式响应"""
    client = _get_proxy_client()

    # 构建转发 headers，去掉 host
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    body = await request.body()

    try:
        # 判断是否需要流式响应（SSE）
        accept = request.headers.get("accept", "")
        need_stream = "text/event-stream" in accept

        if need_stream:
            async def stream_response():
                async with client.stream(
                    method=request.method,
                    url=f"/{path}",
                    headers=headers,
                    content=body if body else None,
                    params=request.query_params,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(
                stream_response(),
                status_code=200,
                media_type="text/event-stream",
            )
        else:
            resp = await client.request(
                method=request.method,
                url=f"/{path}",
                headers=headers,
                content=body if body else None,
                params=request.query_params,
            )
            excluded = {"content-length", "content-encoding", "transfer-encoding"}
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={k: v for k, v in resp.headers.items() if k.lower() not in excluded},
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="llama-server 未运行或无法连接")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"代理请求失败: {str(e)}")
