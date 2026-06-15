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
import time
import uuid
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
GPU_HISTORY_PATH = APP_DIR / "gpu_history.json"
CUSTOM_SERVICES_PATH = APP_DIR / "custom_services.json"
MANAGED_PROCESS_RECORDS_PATH = APP_DIR / "managed_processes.json"

# ── 全局进程状态 ──────────────────────────────────────────
_current_process: Optional[subprocess.Popen] = None
_current_command: Optional[list] = None
_current_model: Optional[str] = None
_current_port: Optional[int] = None
_current_host: Optional[str] = None
_managed_processes: dict[int, dict] = {}
_managed_process_records: dict[int, dict] = {}
_managed_process_records_loaded: bool = False
_process_lock = threading.Lock()

# ── GPU 历史状态 ───────────────────────────────────────
_gpu_history_lock = threading.Lock()
_last_gpu_sample_ts: float = 0
GPU_SAMPLE_INTERVAL_SECONDS = 5

# ── 下载状态 ────────────────────────────────────────────
_download_running: bool = False
_download_done: bool = False
_download_repo: Optional[str] = None
_download_filename: Optional[str] = None
_download_target_dir: Optional[str] = None
_download_error: Optional[str] = None
_download_lock = threading.Lock()

# ── 下载进度追踪 ──────────────────────────────────────
_download_progress_n: int = 0
_download_progress_total: int = 0


class _DownloadTqdm(_tqdm_module.tqdm):
    """自定义 tqdm，将字节下载进度写入全局变量供 API 查询。

    snapshot_download 内部会创建两个进度条实例：字节进度条（unit='B'）和
    文件数进度条（thread_map，无 unit='B'）。只追踪前者，避免文件数进度污染。
    """

    def __init__(self, *args, **kwargs):
        global _download_progress_n
        super().__init__(*args, **kwargs)
        self._track = kwargs.get("unit") == "B"
        if self._track:
            _download_progress_n = 0

    def update(self, n=1):
        global _download_progress_n
        result = super().update(n)
        if self._track:
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


def _load_custom_services() -> dict:
    """读取自定义服务注册表，格式: {service_id: service_config}"""
    try:
        if CUSTOM_SERVICES_PATH.exists():
            data = json.loads(CUSTOM_SERVICES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_custom_services(data: dict):
    """原子写入自定义服务注册表"""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(APP_DIR), suffix=".custom_services.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(CUSTOM_SERVICES_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_managed_process_records_file() -> dict[int, dict]:
    """读取受管进程持久化记录，格式: {pid: record}"""
    try:
        if MANAGED_PROCESS_RECORDS_PATH.exists():
            data = json.loads(MANAGED_PROCESS_RECORDS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                records = data.get("processes", [])
            elif isinstance(data, list):
                records = data
            else:
                records = []
            result = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                try:
                    pid = int(record.get("pid"))
                except (TypeError, ValueError):
                    continue
                result[pid] = record
            return result
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_managed_process_records_file():
    """原子写入受管进程持久化记录"""
    records = sorted(
        _managed_process_records.values(),
        key=lambda item: item.get("started_at") or 0,
        reverse=True,
    )
    payload = {"processes": records}
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(APP_DIR), suffix=".managed_processes.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(MANAGED_PROCESS_RECORDS_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _process_create_time(pid: int) -> Optional[float]:
    """读取进程创建时间，用于避免服务重启后误认复用 PID"""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def _process_matches_record(proc: psutil.Process, record: dict) -> bool:
    """判断当前 PID 是否仍是记录中的受管进程"""
    expected = record.get("process_create_time")
    if expected is None:
        return False
    try:
        return abs(float(proc.create_time()) - float(expected)) < 1
    except (TypeError, ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _record_from_item(pid: int, item: dict, running: bool = True) -> dict:
    """从内存受管进程生成可持久化记录"""
    model_value = item.get("model") or ""
    display_name = item.get("display_name") or Path(model_value).name
    return {
        "pid": pid,
        "model": item.get("model"),
        "model_name": display_name,
        "display_name": display_name,
        "service_kind": item.get("service_kind", "llama"),
        "service_type": item.get("service_type", "llama"),
        "service_id": item.get("service_id"),
        "host": item.get("host"),
        "port": item.get("port"),
        "url": f"http://{item.get('host')}:{item.get('port')}",
        "proxy_url": f"/llama-process/{pid}/",
        "command": shlex.join(item.get("command", [])),
        "command_tokens": item.get("command", []),
        "extra_args": item.get("extra_args", ""),
        "gpu_indexes": item.get("gpu_indexes", []),
        "log_file": item.get("log_file"),
        "started_at": item.get("started_at"),
        "process_create_time": item.get("process_create_time"),
        "running": running,
    }


def _restore_managed_process_records():
    """从持久化记录恢复仍存活的受管进程"""
    global _managed_process_records_loaded
    if _managed_process_records_loaded:
        return

    records = _load_managed_process_records_file()
    for pid, record in records.items():
        record = dict(record)
        record["pid"] = pid
        record["running"] = False
        try:
            proc = psutil.Process(pid)
            if _process_matches_record(proc, record):
                command = record.get("command_tokens")
                if not isinstance(command, list) or not command:
                    command_text = record.get("command", "")
                    try:
                        command = shlex.split(command_text) if command_text else []
                    except ValueError:
                        command = []
                _managed_processes[pid] = {
                    "process": proc,
                    "command": command,
                    "model": record.get("model"),
                    "display_name": record.get("display_name") or record.get("model_name"),
                    "service_kind": record.get("service_kind", "llama"),
                    "service_type": record.get("service_type", "llama"),
                    "service_id": record.get("service_id"),
                    "host": record.get("host", "0.0.0.0"),
                    "port": record.get("port"),
                    "extra_args": record.get("extra_args", ""),
                    "gpu_indexes": record.get("gpu_indexes", []),
                    "log_file": record.get("log_file"),
                    "started_at": record.get("started_at"),
                    "process_create_time": record.get("process_create_time"),
                }
                record["running"] = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass
        _managed_process_records[pid] = record

    _managed_process_records_loaded = True
    _sync_current_from_managed(persist=False)


def _command_tokens(command: str) -> list:
    """解析自定义服务命令"""
    if not command or not command.strip():
        raise HTTPException(status_code=400, detail="启动命令不能为空")
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"启动命令解析失败: {e}")
    if not tokens:
        raise HTTPException(status_code=400, detail="启动命令不能为空")
    forbidden = {"|", ">", "<", ";", "&", "`", "$", "(", ")", "#"}
    for token in tokens:
        if token in forbidden:
            raise HTTPException(status_code=400, detail=f"启动命令包含不支持的 shell 符号: {token}")
    return tokens


def _get_option_value(tokens: list, names: set) -> Optional[str]:
    """从命令参数中读取 --key value 或 --key=value"""
    for i, token in enumerate(tokens):
        for name in names:
            if token == name and i + 1 < len(tokens):
                return tokens[i + 1]
            prefix = f"{name}="
            if token.startswith(prefix):
                return token[len(prefix):]
    return None


def _set_option_value(tokens: list, name: str, value: str) -> list:
    """设置或追加命令参数 --key value"""
    result = list(tokens)
    for i, token in enumerate(result):
        if token == name:
            if i + 1 < len(result):
                result[i + 1] = value
            else:
                result.append(value)
            return result
        prefix = f"{name}="
        if token.startswith(prefix):
            result[i] = f"{name}={value}"
            return result
    result.extend([name, value])
    return result


def _infer_custom_service_name(tokens: list, command: str) -> str:
    """从自定义命令中推断服务名"""
    model_value = _get_option_value(tokens, {"--model", "-m", "--model-path", "--model_name", "--model-name"})
    if model_value:
        name = _model_name_from_value(model_value) or Path(model_value).name
        if name:
            return name
    for token in reversed(tokens):
        if token.startswith("-"):
            continue
        if "/" in token or "\\" in token:
            name = Path(token).name
            if name:
                return name
    return Path(tokens[0]).name if tokens else command[:32]


def _normalize_custom_service(raw: dict) -> dict:
    """校验并标准化自定义服务注册数据"""
    command = str(raw.get("command", "")).strip()
    tokens = _command_tokens(command)
    service_type = str(raw.get("service_type") or "custom").strip() or "custom"
    port_value = _get_option_value(tokens, {"--port"})
    if port_value in (None, ""):
        raise HTTPException(status_code=400, detail="自定义服务命令必须包含 --port")
    try:
        port = int(port_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="端口必须是数字")
    if port <= 0 or port > 65535:
        raise HTTPException(status_code=400, detail="端口范围必须是 1-65535")
    name = str(raw.get("name") or "").strip() or _infer_custom_service_name(tokens, command)
    service_id = str(raw.get("id") or f"svc_{uuid.uuid4().hex[:12]}")
    return {
        "id": service_id,
        "name": name,
        "service_type": service_type,
        "command": command,
        "port": port,
        "created_at": raw.get("created_at") or time.time(),
    }


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


def _get_gpu_history_hours(settings: Optional[dict] = None) -> float:
    """读取 GPU 历史小时数设置，默认 2 小时"""
    if settings is None:
        settings = _load_settings()
    try:
        hours = float(settings.get("gpu_history_hours", 2))
    except (TypeError, ValueError):
        hours = 2
    return min(max(hours, 0.25), 168)


def _load_gpu_history() -> dict:
    """读取本地 GPU 历史文件"""
    try:
        if GPU_HISTORY_PATH.exists():
            data = json.loads(GPU_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("samples"), list):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"samples": []}


def _save_gpu_history(data: dict):
    """原子写入 GPU 历史文件"""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(APP_DIR), suffix=".gpu_history.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, str(GPU_HISTORY_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_gpu_history_sample(gpus: list, history_hours: float):
    """按最小采样间隔写入 GPU util 历史"""
    global _last_gpu_sample_ts

    now = time.time()
    with _gpu_history_lock:
        if now - _last_gpu_sample_ts < GPU_SAMPLE_INTERVAL_SECONDS:
            return

        history = _load_gpu_history()
        sample = {
            "timestamp": now,
            "gpus": [
                {
                    "index": gpu["index"],
                    "name": gpu.get("name", ""),
                    "gpu_util": gpu.get("gpu_util", 0),
                }
                for gpu in gpus
            ],
        }
        keep_seconds = (max(history_hours, 2) + 1) * 3600
        cutoff = now - keep_seconds
        samples = [
            s for s in history.get("samples", [])
            if float(s.get("timestamp", 0) or 0) >= cutoff
        ]
        samples.append(sample)
        _save_gpu_history({"samples": samples})
        _last_gpu_sample_ts = now


def _history_by_gpu(history_hours: float) -> dict:
    """按 GPU index 整理最近 X 小时 util 历史"""
    cutoff = time.time() - history_hours * 3600
    history = _load_gpu_history()
    by_gpu = {}
    for sample in history.get("samples", []):
        ts = float(sample.get("timestamp", 0) or 0)
        if ts < cutoff:
            continue
        for gpu in sample.get("gpus", []):
            index = _to_int(gpu.get("index"), default=-1)
            if index < 0:
                continue
            by_gpu.setdefault(index, []).append({
                "timestamp": ts,
                "gpu_util": _to_int(gpu.get("gpu_util")),
            })
    return by_gpu


def _gpus_from_history(history_hours: float) -> list:
    """nvidia-smi 不可用时，从本地历史文件恢复每卡波形数据"""
    history = _load_gpu_history()
    by_gpu = _history_by_gpu(history_hours)
    latest = {}
    for sample in history.get("samples", []):
        ts = float(sample.get("timestamp", 0) or 0)
        for gpu in sample.get("gpus", []):
            index = _to_int(gpu.get("index"), default=-1)
            if index < 0:
                continue
            if index not in latest or ts >= latest[index]["timestamp"]:
                latest[index] = {
                    "timestamp": ts,
                    "name": gpu.get("name", f"GPU {index}"),
                    "gpu_util": _to_int(gpu.get("gpu_util")),
                }

    gpus = []
    for index in sorted(by_gpu):
        info = latest.get(index, {})
        gpus.append({
            "index": index,
            "name": info.get("name", f"GPU {index}"),
            "driver_version": "",
            "uuid": "",
            "bus_id": "",
            "gpu_util": info.get("gpu_util", 0),
            "used_mem": None,
            "total_mem": None,
            "temperature": None,
            "process_count": 0,
            "users": [],
            "processes": [],
            "history": by_gpu.get(index, []),
            "stale": True,
        })
    return gpus


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


def _is_process_running(proc) -> bool:
    """判断 Popen 或 psutil.Process 是否仍在运行"""
    if proc is None:
        return False
    if isinstance(proc, subprocess.Popen):
        return proc.poll() is None
    if isinstance(proc, psutil.Process):
        try:
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    return False


def _sync_current_from_managed(persist: bool = True):
    """用最新存活的受管实例同步旧版单实例状态"""
    global _current_process, _current_command, _current_model, _current_port, _current_host

    _restore_managed_process_records()
    live_items = [
        item for item in _managed_processes.values()
        if _is_process_running(item.get("process"))
    ]
    dead_pids = [
        pid for pid, item in _managed_processes.items()
        if not _is_process_running(item.get("process"))
    ]
    for pid in dead_pids:
        if pid in _managed_process_records:
            _managed_process_records[pid]["running"] = False
        _managed_processes.pop(pid, None)

    for pid, item in _managed_processes.items():
        _managed_process_records[pid] = _record_from_item(pid, item, running=True)

    if not live_items:
        _current_process = None
        _current_command = None
        _current_model = None
        _current_port = None
        _current_host = None
        if persist:
            _save_managed_process_records_file()
        return

    latest = max(live_items, key=lambda item: item.get("started_at", 0))
    _current_process = latest["process"]
    _current_command = latest["command"]
    _current_model = latest["model"]
    _current_port = latest["port"]
    _current_host = latest["host"]
    if persist:
        _save_managed_process_records_file()


def _managed_process_snapshot() -> dict[int, dict]:
    """获取当前存活受管进程快照"""
    with _process_lock:
        _sync_current_from_managed()
        result = {}
        for pid, item in _managed_processes.items():
            proc = item["process"]
            if not _is_process_running(proc):
                continue
            model_value = item.get("model") or ""
            display_name = item.get("display_name") or Path(model_value).name
            result[pid] = {
                "pid": pid,
                "model": item.get("model"),
                "model_name": display_name,
                "display_name": display_name,
                "service_kind": item.get("service_kind", "llama"),
                "service_type": item.get("service_type", "llama"),
                "service_id": item.get("service_id"),
                "host": item["host"],
                "port": item["port"],
                "url": f"http://{item['host']}:{item['port']}",
                "proxy_url": f"/llama-process/{pid}/",
                "command": shlex.join(item["command"]),
                "command_tokens": item["command"],
                "extra_args": item.get("extra_args", ""),
                "gpu_indexes": item.get("gpu_indexes", []),
                "log_file": item.get("log_file"),
                "started_at": item.get("started_at"),
                "process_create_time": item.get("process_create_time"),
                "running": True,
            }
        return result


def _managed_process_records_snapshot() -> list:
    """获取当前运行期所有已知受管进程记录"""
    live = _managed_process_snapshot()
    records = {pid: dict(record) for pid, record in _managed_process_records.items()}
    for pid, record in live.items():
        records[pid] = {**records.get(pid, {}), **record, "running": True}
    return sorted(records.values(), key=lambda item: item.get("started_at") or 0, reverse=True)


def _managed_process_pid_map(managed: dict[int, dict]) -> dict[int, int]:
    """建立受管 PID 及其子进程 PID 到受管根 PID 的映射"""
    result = {}
    for root_pid in managed:
        result[root_pid] = root_pid
        try:
            root_proc = psutil.Process(root_pid)
            for child in root_proc.children(recursive=True):
                result[child.pid] = root_pid
        except (psutil.AccessDenied, psutil.NoSuchProcess, ValueError):
            continue
    return result


def _create_process_log_path(service_kind: str, display_name: str, port: int) -> Path:
    """为受管进程生成日志文件路径"""
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", display_name).strip("_") or service_kind
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_dir = APP_DIR / "logs" / "services"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{service_kind}-{safe_name}-{port}-{ts}.log"


def _parse_gpu_indexes(value) -> list[int]:
    """解析前端传入的 GPU index 列表"""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, list):
        parts = value
    else:
        raise HTTPException(status_code=400, detail="GPU 选择格式错误")

    result = []
    for part in parts:
        try:
            index = int(part)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"GPU index 非法: {part}")
        if index < 0:
            raise HTTPException(status_code=400, detail=f"GPU index 非法: {part}")
        if index not in result:
            result.append(index)
    return result


def _managed_process_on_port(port: int) -> Optional[int]:
    """返回占用端口的受管进程 PID"""
    for pid, item in _managed_processes.items():
        if item.get("port") == port and _is_process_running(item.get("process")):
            return pid
    return None


def _collect_gpu_status() -> dict:
    """采集 GPU 和进程信息"""
    settings = _load_settings()
    history_hours = _get_gpu_history_hours(settings)
    managed = _managed_process_snapshot()
    managed_pid_map = _managed_process_pid_map(managed)
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
        history_gpus = _gpus_from_history(history_hours)
        return {
            "ok": bool(history_gpus),
            "error": gpu_error,
            "stale": bool(history_gpus),
            "history_hours": history_hours,
            "managed_processes": list(managed.values()),
            "gpus": history_gpus,
        }

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

    _append_gpu_history_sample(gpus, history_hours)
    history = _history_by_gpu(history_hours)
    for gpu in gpus:
        gpu["history"] = history.get(gpu["index"], [])

    process_fields = ["gpu_uuid", "gpu_bus_id", "pid", "used_memory", "process_name"]
    process_rows, process_error = _run_nvidia_smi([
        f"--query-compute-apps={','.join(process_fields)}",
        "--format=csv,noheader,nounits",
    ])

    managed_rows = {}

    def _merge_managed_gpu_row(root_pid: int, gpu: dict, process: dict):
        """将同一个受管服务在多张 GPU 上的占用合并成一行"""
        managed_info = managed.get(root_pid, {})
        row = managed_rows.setdefault(root_pid, {
            **managed_info,
            "gpu": [],
            "gpu_name": [],
            "gpu_util": 0,
            "used_mem": 0,
            "total_mem": 0,
            "temperature": None,
            "username": process.get("username", "Unknown"),
            "process_name": process.get("process_name", ""),
            "gpu_process_pids": [],
        })
        if gpu["index"] not in row["gpu"]:
            row["gpu"].append(gpu["index"])
        if gpu["name"] and gpu["name"] not in row["gpu_name"]:
            row["gpu_name"].append(gpu["name"])
        row["gpu_util"] = max(_to_int(row.get("gpu_util")), _to_int(gpu.get("gpu_util")))
        row["used_mem"] = _to_int(row.get("used_mem")) + _to_int(process.get("used_mem"))
        if gpu["index"] not in row.setdefault("_total_mem_indexes", []):
            row["_total_mem_indexes"].append(gpu["index"])
            row["total_mem"] = _to_int(row.get("total_mem")) + _to_int(gpu.get("total_mem"))
        temp = gpu.get("temperature")
        if temp is not None:
            row["temperature"] = temp if row["temperature"] is None else max(row["temperature"], temp)
        if process.get("username") and row["username"] == "Unknown":
            row["username"] = process["username"]
        if process.get("process_name") and not row["process_name"]:
            row["process_name"] = process["process_name"]
        gpu_pid = process.get("gpu_pid")
        if gpu_pid and gpu_pid not in row["gpu_process_pids"]:
            row["gpu_process_pids"].append(gpu_pid)
    if not process_error:
        for row in process_rows or []:
            if len(row) < len(process_fields):
                continue
            gpu = uuid_to_gpu.get(row[0]) or bus_id_to_gpu.get(row[1])
            if gpu is None:
                continue

            gpu_pid = _to_int(row[2], default=-1)
            root_pid = managed_pid_map.get(gpu_pid)
            if root_pid not in managed:
                continue

            proc_detail = _get_process_detail(gpu_pid) if gpu_pid > 0 else {
                "username": "Unknown",
                "command": "",
                "cmdline": [],
            }
            managed_info = managed.get(root_pid, {})
            process = {
                "pid": root_pid,
                "gpu_pid": gpu_pid if gpu_pid > 0 else None,
                "used_mem": _to_int(row[3]),
                "process_name": row[4],
                "username": proc_detail["username"],
                "command": managed_info.get("command") or proc_detail["command"],
                "model_name": managed_info.get("model_name") or _infer_model_name(proc_detail["cmdline"]),
                "model": managed_info.get("model"),
                "host": managed_info.get("host"),
                "port": managed_info.get("port"),
                "url": managed_info.get("url"),
                "proxy_url": managed_info.get("proxy_url"),
                "gpu_indexes": managed_info.get("gpu_indexes", []),
                "started_at": managed_info.get("started_at"),
            }
            gpu["processes"].append(process)
            _merge_managed_gpu_row(root_pid, gpu, process)

    for gpu in gpus:
        users = sorted({
            p["username"]
            for p in gpu["processes"]
            if p.get("username") and p.get("username") != "Unknown"
        })
        gpu["process_count"] = len(gpu["processes"])
        gpu["users"] = users

    managed_processes = []
    for pid, item in managed.items():
        row = managed_rows.get(pid, {})
        if not row:
            fallback_gpus = [
                gpu for gpu in gpus
                if gpu["index"] in (item.get("gpu_indexes") or [])
            ]
            if fallback_gpus:
                row = {
                    "gpu": [gpu["index"] for gpu in fallback_gpus],
                    "gpu_name": [gpu["name"] for gpu in fallback_gpus if gpu.get("name")],
                    "gpu_util": max(_to_int(gpu.get("gpu_util")) for gpu in fallback_gpus),
                    "used_mem": None,
                    "total_mem": sum(_to_int(gpu.get("total_mem")) for gpu in fallback_gpus),
                    "temperature": max(_to_int(gpu.get("temperature")) for gpu in fallback_gpus),
                }
        if row:
            row = dict(row)
            row.pop("_total_mem_indexes", None)
            if isinstance(row.get("gpu"), list):
                row["gpu"] = ", ".join(str(v) for v in row["gpu"])
            if isinstance(row.get("gpu_name"), list):
                row["gpu_name"] = ", ".join(row["gpu_name"])
        managed_processes.append({
            **item,
            "gpu": None,
            "gpu_name": "",
            "gpu_util": None,
            "used_mem": None,
            "total_mem": None,
            "temperature": None,
            "username": "Unknown",
            "process_name": "",
            **row,
        })

    return {
        "ok": True,
        "error": process_error,
        "stale": False,
        "history_hours": history_hours,
        "managed_processes": managed_processes,
        "gpus": gpus,
    }


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


def _build_custom_command(service: dict) -> list:
    """构建自定义服务启动命令，按注册命令原样执行"""
    return _command_tokens(service["command"])


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


def _stop_one_process(item: dict) -> str:
    """停止单个受管进程"""
    proc = item["process"]
    info = f"PID {proc.pid}"
    processes = []
    try:
        root = psutil.Process(proc.pid)
        processes = root.children(recursive=True) + [root]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    if processes:
        for child in processes:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        gone, alive = psutil.wait_procs(processes, timeout=3)
        for child in alive:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(alive, timeout=2)
    elif _is_process_running(proc):
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=2)
    _managed_processes.pop(proc.pid, None)
    if proc.pid in _managed_process_records:
        _managed_process_records[proc.pid]["running"] = False
    return info


def _clear_service_log():
    """清空服务日志文件"""
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


def _stop_process_internal(pid: Optional[int] = None, clear_log: bool = True) -> Optional[str]:
    """停止受管 llama-server 进程，pid 为空时停止全部"""
    _sync_current_from_managed()
    stopped = []

    if pid is not None:
        item = _managed_processes.get(pid)
        if item is None:
            return None
        stopped.append(_stop_one_process(item))
    else:
        for item in list(_managed_processes.values()):
            stopped.append(_stop_one_process(item))

    _sync_current_from_managed()
    if clear_log and stopped:
        _clear_service_log()

    return ", ".join(stopped) if stopped else None


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


@app.get("/api/model-repositories")
async def get_model_repositories():
    """扫描 model_dir 下的全量仓库目录"""
    settings = _load_settings()
    model_dir = settings.get("model_dir", "")
    repositories = []

    if model_dir and Path(model_dir).is_dir():
        for p in sorted(Path(model_dir).iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            try:
                stat = p.stat()
                repositories.append({
                    "name": p.name,
                    "display_name": p.name.replace("--", "/"),
                    "path": str(p),
                    "modified": stat.st_mtime,
                    "has_config": (p / "config.json").is_file(),
                    "has_model_files": any(
                        child.is_file() and child.suffix.lower() in {".safetensors", ".bin", ".gguf"}
                        for child in p.iterdir()
                    ),
                })
            except OSError:
                pass

    return JSONResponse({"repositories": repositories, "model_dir": model_dir})


@app.get("/api/custom-services")
async def get_custom_services():
    """读取自定义服务注册表"""
    services = sorted(
        _load_custom_services().values(),
        key=lambda item: item.get("created_at", 0),
    )
    return JSONResponse({"services": services})


@app.post("/api/custom-services")
async def save_custom_service(data: dict):
    """注册或更新自定义服务"""
    services = _load_custom_services()
    payload = dict(data or {})
    service_id = str(payload.get("id") or "")
    if service_id and service_id in services:
        payload = {**services[service_id], **payload}
    service = _normalize_custom_service(payload)
    services[service["id"]] = service
    _save_custom_services(services)
    return JSONResponse({"ok": True, "service": service})


@app.delete("/api/custom-services/{service_id}")
async def delete_custom_service(service_id: str):
    """删除自定义服务注册项"""
    services = _load_custom_services()
    if service_id not in services:
        raise HTTPException(status_code=404, detail=f"自定义服务不存在: {service_id}")
    service = services.pop(service_id)
    _save_custom_services(services)
    return JSONResponse({"ok": True, "service": service})


@app.get("/api/status")
async def get_status():
    """获取当前 llama-server 运行状态"""
    with _process_lock:
        _sync_current_from_managed()
        running = _is_process_running(_current_process)
        url = None
        if running and _current_host and _current_port:
            url = f"http://{_current_host}:{_current_port}"

        result = {
            "running": running,
            "pid": _current_process.pid if running else None,
            "command": shlex.join(_current_command) if running and _current_command else None,
            "model": _current_model if running else None,
            "host": _current_host if running else None,
            "port": _current_port if running else None,
            "url": url,
        }

    result["processes"] = list(_managed_process_snapshot().values())
    return JSONResponse(result)


@app.get("/api/gpus")
async def get_gpus():
    """获取 GPU 状态和 GPU 进程列表"""
    return JSONResponse(_collect_gpu_status())


@app.get("/api/managed-processes")
async def get_managed_processes():
    """获取当前运行期已知受管进程记录"""
    return JSONResponse({"processes": _managed_process_records_snapshot()})


@app.post("/api/start")
async def start_server(body: dict = None):
    """启动 llama-server"""
    global _current_process, _current_command, _current_model, _current_port, _current_host

    if body is None:
        body = {}

    settings = _load_settings()

    # 合并参数
    model = body.get("model") or settings.get("model", "")
    service_kind = "custom" if isinstance(model, str) and model.startswith("custom:") else "llama"
    service_id = model.split(":", 1)[1] if service_kind == "custom" else None
    custom_service = None
    if service_kind == "custom":
        custom_service = _load_custom_services().get(service_id)
        if custom_service is None:
            raise HTTPException(status_code=400, detail=f"自定义服务不存在: {service_id}")
    host = body.get("host") or settings.get("host", "0.0.0.0")
    if service_kind == "custom":
        port = int(custom_service["port"])
    else:
        port = int(body.get("port") or settings.get("port", 8080))
    extra_args = body.get("extra_args", "")
    incremental_start = body.get("incremental_start", True) is not False
    gpu_indexes = _parse_gpu_indexes(body.get("gpu_indexes", []))

    # 校验 extra_args
    if service_kind == "llama":
        err = _validate_extra_args(extra_args)
        if err:
            raise HTTPException(status_code=400, detail=err)

    llama_server_path = settings.get("llama_server_path", "")
    auto_kill_port = body.get("auto_kill_port", settings.get("auto_kill_port", True))
    protected_ports = settings.get("protected_ports", [22])

    # 校验 llama-server 路径
    if service_kind == "llama" and (not llama_server_path or not Path(llama_server_path).is_file()):
        raise HTTPException(
            status_code=400,
            detail=f"llama-server 不存在: {llama_server_path}"
        )

    # 校验模型文件
    if service_kind == "llama" and (not model or not Path(model).is_file()):
        raise HTTPException(
            status_code=400,
            detail=f"模型文件不存在: {model}"
        )

    killed_info = None
    with _process_lock:
        _sync_current_from_managed()

        if not incremental_start:
            _stop_process_internal(clear_log=True)
        else:
            existing_pid = _managed_process_on_port(port)
            if existing_pid is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"端口 {port} 已被受管进程 PID {existing_pid} 使用"
                )

        # 处理端口占用
        if auto_kill_port:
            killed_info = _kill_port_occupant(port, protected_ports)

        # 构建启动命令
        if service_kind == "custom":
            display_name = custom_service["name"]
            service_type = custom_service.get("service_type", "custom")
            cmd = _build_custom_command(custom_service)
            model_for_record = f"custom:{custom_service['id']}"
        else:
            display_name = Path(model).name
            service_type = "llama"
            overrides = {
                "model": model,
                "host": host,
                "port": port,
                "extra_args": extra_args,
            }
            cmd = _build_command(settings, overrides)
            model_for_record = model

        # 准备日志目录
        log_path = _create_process_log_path(service_type, display_name, port)

        env = os.environ.copy()
        if gpu_indexes:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in gpu_indexes)

        # 启动进程
        try:
            log_fh = open(log_path, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"启动失败: {str(e)}"
            )

        process_create_time = _process_create_time(proc.pid)
        _managed_processes[proc.pid] = {
            "process": proc,
            "command": cmd,
            "model": model_for_record,
            "display_name": display_name,
            "service_kind": service_kind,
            "service_type": service_type,
            "service_id": service_id,
            "host": host,
            "port": port,
            "extra_args": extra_args if service_kind == "llama" else "",
            "gpu_indexes": gpu_indexes,
            "log_file": str(log_path),
            "started_at": time.time(),
            "process_create_time": process_create_time,
        }
        _managed_process_records[proc.pid] = _record_from_item(proc.pid, _managed_processes[proc.pid], running=True)
        _save_managed_process_records_file()

        _current_process = proc
        _current_command = cmd
        _current_model = model_for_record
        _current_port = port
        _current_host = host

    # 按模型保存启动参数
    if service_kind == "llama":
        _save_model_param(model, port, extra_args)

    result = {
        "ok": True,
        "pid": proc.pid,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "proxy_url": f"/llama-process/{proc.pid}/",
        "command": " ".join(cmd),
        "service_kind": service_kind,
        "service_type": service_type,
        "display_name": display_name,
        "log_file": str(log_path),
        "gpu_indexes": gpu_indexes,
        "incremental_start": incremental_start,
        "killed": killed_info,
    }
    return JSONResponse(result)


@app.post("/api/stop")
async def stop_server(body: dict = None):
    """停止 llama-server"""
    if body is None:
        body = {}
    pid = body.get("pid")
    pid = int(pid) if pid not in (None, "") else None

    with _process_lock:
        info = _stop_process_internal(pid=pid, clear_log=(pid is None))
        if info is None:
            return JSONResponse({"ok": True, "status": "already_stopped"})
        return JSONResponse({"ok": True, "status": "stopped", "detail": f"Stopped {info}"})


@app.post("/api/restart")
async def restart_server(body: dict = None):
    """重启 llama-server（使用指定 PID 或最新实例的参数）"""
    if body is None:
        body = {}
    pid = body.get("pid")
    pid = int(pid) if pid not in (None, "") else None

    with _process_lock:
        _sync_current_from_managed()
        if pid is not None:
            item = _managed_processes.get(pid)
        else:
            item = _managed_processes.get(_current_process.pid) if _current_process else None

        if item is None:
            raise HTTPException(
                status_code=400,
                detail="没有可重启的参数（进程不存在或已停止）"
            )

        last = {
            "model": item["model"],
            "host": item["host"],
            "port": item["port"],
            "extra_args": item.get("extra_args", ""),
            "gpu_indexes": item.get("gpu_indexes", []),
            "incremental_start": True,
        }
        _stop_process_internal(pid=item["process"].pid, clear_log=False)

    # 使用原参数重新追加启动一个实例
    last = {
        **last,
        **{k: v for k, v in body.items() if k in {"port", "extra_args", "gpu_indexes"}},
    }
    return await start_server(last)


@app.get("/api/model-params")
async def get_model_params():
    """获取所有模型的启动参数"""
    return JSONResponse(_load_model_params())


@app.get("/api/logs")
async def get_logs(pid: Optional[int] = None):
    """读取服务日志尾部，pid 为空时读取最新受管进程日志"""
    log_path = None
    if pid is not None:
        record = {item["pid"]: item for item in _managed_process_records_snapshot()}.get(pid)
        if record is None:
            raise HTTPException(status_code=404, detail=f"受管进程不存在: {pid}")
        log_file = record.get("log_file")
        if log_file:
            log_path = Path(log_file)
    else:
        records = _managed_process_records_snapshot()
        if records and records[0].get("log_file"):
            log_path = Path(records[0]["log_file"])

    if log_path is None:
        settings = _load_settings()
        log_file = settings.get("log_file", "./logs/llama-server.log")
        log_path = Path(log_file)

    if not log_path.is_absolute():
        log_path = APP_DIR / log_path

    if not log_path.exists():
        return JSONResponse({"logs": "", "path": str(log_path)})

    try:
        lines = log_path.read_text(errors="replace").splitlines()
        return JSONResponse({"logs": "\n".join(lines[-200:]), "path": str(log_path)})
    except Exception as e:
        return JSONResponse({"logs": f"读取日志失败: {str(e)}", "path": str(log_path)})


# ── 下载 API ────────────────────────────────────────────

@app.post("/api/download")
async def download_model(body: dict = None):
    """从 Hugging Face 下载模型"""
    global _download_running, _download_done, _download_repo, _download_filename
    global _download_target_dir, _download_error
    global _download_progress_n, _download_progress_total

    if body is None:
        body = {}

    repo = body.get("repo", "").strip()
    filename = body.get("filename", "").strip()
    force_download = body.get("force_download", False)

    if not repo:
        raise HTTPException(status_code=400, detail="请输入仓库名")

    # 校验仓库格式：owner/repo
    if not re.match(r'^[a-zA-Z0-9_.\-]+\/[a-zA-Z0-9_.\-]+$', repo):
        raise HTTPException(status_code=400, detail="仓库名格式错误，应为 owner/repo（如 tencent/Hy-MT2-7B-GGUF）")
    # 指定文件名时校验：必须 .gguf 结尾（留空则全量下载整个仓库）
    if filename and not re.match(r'^[a-zA-Z0-9_.\-]+\.gguf$', filename):
        raise HTTPException(status_code=400, detail="文件名格式错误，必须以 .gguf 结尾（留空则全量下载）")

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

        # 全量下载目标目录为 model_dir/owner--repo，单文件为 model_dir
        if filename:
            target_dir = model_dir
        else:
            target_dir = os.path.join(model_dir, repo.replace("/", "--"))
        Path(target_dir).mkdir(parents=True, exist_ok=True)

        _download_repo = repo
        _download_filename = filename or None
        _download_target_dir = target_dir
        _download_done = False
        _download_running = True
        _download_error = None
        _download_progress_n = 0
        _download_progress_total = 0

        # 预获取远程文件总大小
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            if filename:
                # 单文件：找匹配的 sibling
                model_info = api.model_info(repo_id=repo, files_metadata=True)
                for sibling in (model_info.siblings or []):
                    if sibling.rfilename == filename:
                        _download_progress_total = sibling.size or 0
                        break
            else:
                # 全量：所有文件大小求和
                info = api.repo_info(repo_id=repo, files_metadata=True)
                _download_progress_total = sum(
                    s.size for s in (info.siblings or []) if s.size
                )
        except Exception:
            pass

        def _do_download():
            """后台线程执行下载"""
            global _download_done, _download_running, _download_error
            try:
                tag = f"[{repo}/{filename}]" if filename else f"[{repo} 全量]"
                size_info = f" ({_download_progress_total / 1024 / 1024:.1f} MB)" if _download_progress_total else ""
                log_msg = f"{tag} 开始下载到 {target_dir}{size_info}\n"
                download_log.write_text(log_msg, encoding="utf-8")

                if filename:
                    from huggingface_hub import hf_hub_download
                    path = hf_hub_download(
                        repo_id=repo,
                        filename=filename,
                        local_dir=target_dir,
                        force_download=force_download,
                        tqdm_class=_DownloadTqdm,
                    )
                else:
                    from huggingface_hub import snapshot_download
                    path = snapshot_download(
                        repo_id=repo,
                        local_dir=target_dir,
                        force_download=force_download,
                        tqdm_class=_DownloadTqdm,
                    )

                with open(download_log, "a", encoding="utf-8") as f:
                    f.write(f"{tag} 下载完成: {path}\n")
                _download_done = True
            except Exception as e:
                _download_error = str(e)
                tag = f"[{repo}/{filename}]" if filename else f"[{repo} 全量]"
                with open(download_log, "a", encoding="utf-8") as f:
                    f.write(f"{tag} 下载失败: {e}\n")
                _download_done = True
            finally:
                _download_running = False

        threading.Thread(target=_do_download, daemon=True).start()

        return JSONResponse({
            "ok": True,
            "repo": repo,
            "filename": filename or None,
            "target_dir": target_dir,
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
            "target_dir": _download_target_dir,
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


async def _proxy_request_to_base(base_url: str, path: str, request: Request):
    """将请求转发到指定 llama-server 基地址"""
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.body()
    accept = request.headers.get("accept", "")
    need_stream = "text/event-stream" in accept

    if need_stream:
        async def stream_response():
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(300.0, connect=5.0),
            ) as client:
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

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(300.0, connect=5.0),
    ) as client:
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


@app.api_route("/llama-process/{pid}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.api_route("/llama-process/{pid}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_to_managed_llama(pid: int, request: Request, path: str = ""):
    """按 PID 反向代理到受管 llama-server 实例"""
    with _process_lock:
        _sync_current_from_managed()
        item = _managed_processes.get(pid)
        if item is None:
            raise HTTPException(status_code=404, detail=f"受管进程不存在: {pid}")
        base_url = f"http://127.0.0.1:{item['port']}"

    try:
        return await _proxy_request_to_base(base_url, path, request)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="llama-server 未运行或无法连接")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"代理请求失败: {str(e)}")


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
