"""受限子进程 Python 执行器。

一句话原理：把代码写成临时目录里的 snippet.py，用本项目 venv 的 python 起一个子
进程跑它，跑之前先给自己上 resource 限额，跑完读回收到的 stdout/stderr 文件。

三道闸：
  1. RLIMIT_CPU     —— 死循环烧 CPU，内核直接发 SIGXCPU（实测有效）
  2. RSS 看门狗     —— 父进程每 100ms 采一次常驻内存，超限就 killpg
                       （macOS 不执行 RLIMIT_AS，实测一个例子能吃到 8GB，所以必须父进程盯）
  3. 墙钟 timeout   —— 卡在 sleep / 等网络时由父进程 killpg 整个进程组

外加：stdout/stderr 重定向到文件并按字节数截断（防刷屏撑爆内存），RLIMIT_FSIZE 限制
临时目录里能写多大的文件（实测有效），cwd 固定在临时目录，env 只给最小集合，
HOME/TMPDIR 也指到临时目录。

**这不是安全边界**：子进程仍以你的用户身份运行，能读写你有权访问的文件。所以服务
只监听 127.0.0.1，只给自己用（详见 README「安全说明」）。
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
import psutil

from app.config import Settings, get_settings

Mode = Literal["plain", "asgi"]
DELIM = "@@PLW_ASGI@@"
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# 子进程自己先上限额，再 runpy 跑目标脚本。
# 不用 preexec_fn：那个在多线程宿主里不安全（我们的接口跑在线程池里）。
_CHILD = '''"""沙箱子进程入口：先自缚手脚，再执行学习者提交的代码。"""
import json
import resource
import runpy
import sys
import traceback
from pathlib import Path


def _apply(spec):
    for name, (soft, hard) in spec.items():
        which = getattr(resource, name, None)
        if which is None:
            continue
        cur_soft, cur_hard = resource.getrlimit(which)
        hard_cap = cur_hard if cur_hard not in (0, resource.RLIM_INFINITY) else cur_soft
        try:
            resource.setrlimit(which, (min(soft, hard_cap), min(hard, hard_cap) or hard))
        except (ValueError, OSError):
            pass  # macOS 上某些限额只能收紧不能放宽，失败就跳过


def _is_boilerplate(frame) -> bool:
    name = str(getattr(frame, "filename", "") or "")
    return "_child.py" in name or "runpy" in name or "<frozen" in name


def _print_clean_tb(exc: BaseException) -> None:
    """把引导脚本自己的帧剔掉：回溯应该从 snippet.py 开始，而不是从我们的外壳开始。"""
    te = traceback.TracebackException(type(exc), exc, exc.__traceback__)
    te.stack = traceback.StackSummary.from_list([f for f in te.stack if not _is_boilerplate(f)])
    text = "".join(te.format()).strip()
    print(text or f"{type(exc).__name__}: {exc}", file=sys.stderr)


def main():
    limits = json.loads(sys.argv[1])
    target = sys.argv[2]
    _apply(limits)
    sys.argv = [str(Path(target).name)] + sys.argv[3:]
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit:
        raise                       # 学习者自己 sys.exit() 的退出码要原样传出去
    except BaseException as exc:    # noqa: BLE001 - 这里就是要兜住一切，转成可读回溯
        _print_clean_tb(exc)
        sys.exit(1)
    return 0


sys.exit(main())
'''

# ASGI 模式：不占端口，直接在子进程里把 app 当服务打（教程第 10 章 TestClient 的同款思路）
_ASGI_RUNNER = f'''"""自动生成的调用器：加载 snippet.py 里的 app，按 payload.json 发请求。"""
import asyncio
import importlib.util
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
DELIM = "{DELIM}"


def _load():
    spec = importlib.util.spec_from_file_location("snippet", HERE / "snippet.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["snippet"] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_app(mod):
    from fastapi import APIRouter, FastAPI
    for value in list(vars(mod).values()):
        if isinstance(value, FastAPI):
            return value
    router = next((v for v in vars(mod).values() if isinstance(v, APIRouter)), None)
    if router is None:
        return None
    app = FastAPI()
    app.include_router(router)
    return app


async def _drive(app, reqs):
    import httpx
    out = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for req in reqs:
            method = req.get("method", "GET")
            path = req.get("path", "/")
            try:
                resp = await client.request(
                    method, path,
                    json=req.get("body", req.get("json")),
                                            params=req.get("params"), headers=req.get("headers"),
                )
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text[:4000]
                out.append({{"method": method, "path": path,
                             "status": resp.status_code, "body": body}})
            except Exception as exc:  # noqa: BLE001 - 这里就是要兜住所有异常给学习者看
                out.append({{"method": method, "path": path,
                             "error": f"{{type(exc).__name__}}: {{exc}}"}})
    return out


def _emit(payload):
    print(DELIM)
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def main():
    try:
        mod = _load()
    except Exception:
        traceback.print_exc()
        _emit([{{"method": "-", "path": "-", "error": "snippet.py 执行失败，看上面的回溯"}}])
        return 1
    try:
        app = _find_app(mod)
    except Exception:
        traceback.print_exc()
        app = None
    if app is None:
        _emit([{{"method": "-", "path": "-",
                "error": "这段代码里既没有 FastAPI() 实例，也没有可挂载的 APIRouter"}}])
        return 1
    reqs = json.loads((HERE / "payload.json").read_text(encoding="utf-8"))
    if not reqs:
        reqs = [{{"method": "GET", "path": "/"}}]
    try:
        from asgi_lifespan import LifespanManager

        async def go():
            async with LifespanManager(app):
                return await _drive(app, reqs)

        results = asyncio.run(go())
    except ModuleNotFoundError:  # 没装 asgi-lifespan 就跳过 lifespan
        results = asyncio.run(_drive(app, reqs))
    except Exception:
        traceback.print_exc()
        results = [{{"method": "-", "path": "-", "error": "lifespan 启动失败，看上面的回溯"}}]
    _emit(results)
    return 0


sys.exit(main())
'''


@dataclass(slots=True)
class RunResult:
    ok: bool
    mode: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    results: list[dict[str, Any]] = field(default_factory=list)
    workdir: str = ""
    peak_rss_mb: int = 0
    hint: str = ""


def _hint_for(*, timed_out: bool, mem_killed: bool, exit_code: int | None, truncated: bool) -> str:
    """把"为什么没跑出来"翻译成人话，学习者不用自己猜退出码。"""
    if mem_killed:
        return "常驻内存超上限，被父进程看门狗杀掉（macOS 不执行 RLIMIT_AS，只能靠这里兜）"
    if timed_out:
        return "墙钟超时被终止：大概率卡在 sleep、等网络或死循环"
    if exit_code == -signal.SIGXCPU:
        return "CPU 时间超限，被内核 SIGXCPU 杀掉：典型症状是死循环"
    if exit_code is not None and exit_code < 0:
        return f"被信号 {-exit_code} 终止，不是正常退出"
    if truncated:
        return "输出超过上限被截断：收敛一下 print，只打关键行"
    return ""


def _limits_payload(s: Settings) -> dict[str, list[int]]:
    mem = s.max_mem_mb * 1024 * 1024
    fsize = s.max_fsize_mb * 1024 * 1024
    return {
        "RLIMIT_CPU": [s.cpu_timeout, s.cpu_timeout + 1],
        "RLIMIT_AS": [mem, mem],
        "RLIMIT_FSIZE": [fsize, fsize],
        "RLIMIT_NOFILE": [256, 512],
    }


def _workdir_for(uid: str | None, s: Settings) -> tuple[Path, bool]:
    """带合法 uid 时用可复用的工作目录（例子写的 app.db 下次还在），否则临时目录。"""
    if uid and _UID_RE.match(uid):
        path = s.data_dir / "runs" / uid
        path.mkdir(parents=True, exist_ok=True)
        return path, False
    root = s.data_dir / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="anon-", dir=str(root))), True


def _env_for(workdir: Path) -> dict[str, str]:
    exe_dir = str(Path(sys.executable).parent)
    return {
        "PATH": f"{exe_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "zh_CN.UTF-8",
        "LC_CTYPE": "UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _read_capped(path: Path, limit: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    raw = path.read_bytes()
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n…（输出超过 {limit} 字节，已截断）"
    return text, truncated


def _run_blocking(
    code: str,
    mode: Mode,
    asgi_requests: list[dict[str, Any]],
    uid: str | None,
    s: Settings,
) -> RunResult:
    workdir, ephemeral = _workdir_for(uid, s)
    (workdir / "_child.py").write_text(_CHILD, encoding="utf-8")
    (workdir / "snippet.py").write_text(code.rstrip() + "\n", encoding="utf-8")

    target = "snippet.py"
    if mode == "asgi":
        target = "_asgi_runner.py"
        (workdir / target).write_text(_ASGI_RUNNER, encoding="utf-8")
        (workdir / "payload.json").write_text(
            json.dumps(asgi_requests, ensure_ascii=False), encoding="utf-8"
        )

    argv = [
        sys.executable,
        "-I",  # isolated：忽略 PYTHONPATH 和用户级 site-packages，但保留 venv 的包
        "-u",  # 不缓冲，超时被杀时也能拿到已产出的输出
        "_child.py",
        json.dumps(_limits_payload(s)),
        target,
    ]

    out_path, err_path = workdir / "_stdout.log", workdir / "_stderr.log"
    started = time.perf_counter()
    with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
        proc = subprocess.Popen(
            argv,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            env=_env_for(workdir),
            start_new_session=True,  # 独立进程组，超时时可以整组端掉
        )
        exit_code, timed_out, mem_killed, peak_rss = _supervise(proc, s)
    duration_ms = int((time.perf_counter() - started) * 1000)

    stdout, t1 = _read_capped(out_path, s.max_output_bytes)
    stderr, t2 = _read_capped(err_path, s.max_output_bytes)
    results: list[dict[str, Any]] = []
    if mode == "asgi" and DELIM in stdout:
        stdout, tail = stdout.split(DELIM, 1)
        try:
            parsed = json.loads(tail.strip())
            results = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            stderr += f"\n[沙箱] 无法解析 ASGI 结果：{tail[:200]}"

    if ephemeral:
        shutil.rmtree(workdir, ignore_errors=True)
        workdir_str = "(临时目录，已清理)"
    else:
        workdir_str = str(workdir.relative_to(s.data_dir))

    return RunResult(
        ok=(exit_code == 0 and not timed_out and not mem_killed),
        mode=mode,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout=stdout.strip(),
        stderr=stderr.strip(),
        truncated=t1 or t2,
        results=results,
        workdir=workdir_str,
        peak_rss_mb=peak_rss // (1024 * 1024),
        hint=_hint_for(
            timed_out=timed_out,
            mem_killed=mem_killed,
            exit_code=exit_code,
            truncated=t1 or t2,
        ),
    )


def _tree_rss(proc: psutil.Process) -> int:
    """整棵进程树的常驻内存。fork 出去的子进程也要算进来。"""
    try:
        total = proc.memory_info().rss
    except psutil.Error:
        return 0
    for child in proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            continue
    return total


def _supervise(proc: subprocess.Popen, s: Settings) -> tuple[int | None, bool, bool, int]:
    """盯住子进程，返回 (退出码, 墙钟超时?, 内存超限?, 峰值RSS字节)。

    为什么不用 proc.wait(timeout=) 一把梭：那样只能管到墙钟，管不到内存。
    macOS 上 RLIMIT_AS 不生效（实测子进程能一路分配到 8GB），所以父进程必须自己采样。
    """
    deadline = time.monotonic() + s.exec_timeout
    mem_limit = s.max_mem_mb * 1024 * 1024
    peak = 0
    timed_out = mem_killed = False
    try:
        watched = psutil.Process(proc.pid)
    except psutil.Error:  # pragma: no cover - 进程秒退
        watched = None

    while True:
        code = proc.poll()
        if code is not None:
            return code, False, False, peak
        if watched is not None:
            peak = max(peak, _tree_rss(watched))
            if peak > mem_limit:
                mem_killed = True
                break
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.08)

    _kill_group(proc.pid)
    try:
        code = proc.wait(timeout=3)
    except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况
        proc.kill()
        code = proc.wait()
    return code, timed_out, mem_killed, peak


def _kill_group(pid: int) -> None:
    """端掉整个进程组：子进程再 fork 出来的孙子进程也一起带走。"""
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # 进程已经自己退出

async def run_python(
    code: str,
    *,
    mode: Mode = "plain",
    asgi_requests: list[dict[str, Any]] | None = None,
    example_uid: str | None = None,
    settings: Settings | None = None,
) -> RunResult:
    """异步接口：真正执行丢给线程池，不阻塞事件循环。"""
    s = settings or get_settings()
    return await anyio.to_thread.run_sync(
        functools.partial(
            _run_blocking, code, mode, asgi_requests or [], example_uid, s
        )
    )
