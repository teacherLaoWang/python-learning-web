"""沙箱行为测试。

这几条用例就是「受限执行」的验收标准：正常代码能跑、报错能定位到行号、
死循环/卡住/吃内存/刷屏四种情况都必须被拦住，ASGI 模式要能真的调用到端点。

跑法：uv run pytest tests/test_sandbox.py -v
"""

from __future__ import annotations

import asyncio

from app.sandbox import run_python


def _run(code: str, settings, **kw):
    return asyncio.run(run_python(code, settings=settings, **kw))


# ---------- 正常路径 -----------------------------------------------------
def test_plain_run_captures_stdout_and_exit_zero(fast_settings):
    r = _run("print('hi 明兴')\nprint(sum(range(10)))", fast_settings)
    assert r.ok and r.exit_code == 0
    assert r.stdout.splitlines() == ["hi 明兴", "45"]
    assert r.stderr == "" and r.hint == ""


def test_traceback_maps_to_snippet_line_numbers(fast_settings):
    code = "def f(x):\n    return 1 / x\n\n\nf(0)"
    r = _run(code, fast_settings)
    assert not r.ok and r.exit_code == 1
    assert "ZeroDivisionError" in r.stderr
    assert 'File "snippet.py", line 2' in r.stderr  # 行号和编辑器里一致
    # 引导脚本自己的帧不该出现在学习者的回溯里
    assert "_child.py" not in r.stderr and "runpy" not in r.stderr
    assert r.stderr.startswith("Traceback (most recent call last):")
    assert r.hint == ""


def test_explicit_sysexit_code_is_preserved(fast_settings):
    r = _run("import sys\nprint('before')\nsys.exit(3)", fast_settings)
    assert not r.ok and r.exit_code == 3
    assert r.stdout == "before" and r.stderr == ""


def test_venv_packages_are_visible_in_child(fast_settings):
    r = _run("import fastapi, sqlalchemy, pydantic\nprint('ok')", fast_settings)
    assert r.ok and r.stdout == "ok"


# ---------- 四种必须被拦住的情况 ------------------------------------------
def test_infinite_loop_hit_by_cpu_limit(fast_settings):
    r = _run("while True:\n    pass", fast_settings)
    assert not r.ok
    assert r.duration_ms < fast_settings.exec_timeout * 1000 + 1500
    assert "CPU" in r.hint


def test_blocking_sleep_hit_by_wall_clock_timeout(fast_settings):
    r = _run("import time\ntime.sleep(30)\nprint('never')", fast_settings)
    assert r.timed_out and not r.ok
    assert "never" not in r.stdout
    assert "墙钟" in r.hint


def test_memory_hog_hit_by_watchdog(fast_settings):
    """macOS 不执行 RLIMIT_AS，这条测的就是父进程那道 RSS 看门狗。"""
    hog = "\n".join(
        [
            "chunks = []",
            f"for _ in range({fast_settings.max_mem_mb * 4}):",
            "    chunks.append(bytearray(1024 * 1024))",
            "    chunks[-1][:] = b'\\x01' * (1024 * 1024)",
            "print('should never get here')",
        ]
    )
    r = _run(hog, fast_settings)
    assert not r.ok
    assert r.peak_rss_mb >= fast_settings.max_mem_mb
    assert "内存" in r.hint and "should never get here" not in r.stdout


def test_flooded_output_is_truncated(fast_settings):
    r = _run("for i in range(200000):\n    print('x' * 80)", fast_settings)
    assert r.truncated
    assert len(r.stdout) <= fast_settings.max_output_bytes + 200


# ---------- ASGI 模式 ---------------------------------------------------
FASTAPI_SNIPPET = """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class Task(BaseModel):
    title: str
    done: bool = False


@app.get("/health")
def health() -> dict:
    return {"ok": True, "n": 3}


@app.post("/tasks")
def create(task: Task) -> dict:
    return {"echo": task.model_dump(), "id": 1}
"""


def test_asgi_calls_endpoints_without_opening_a_port(fast_settings):
    r = _run(
        FASTAPI_SNIPPET,
        fast_settings,
        mode="asgi",
        asgi_requests=[
            {"method": "GET", "path": "/health"},
            {"method": "POST", "path": "/tasks", "body": {"title": "写沙箱"}},
        ],
    )
    assert r.ok, r.stderr
    assert [x["status"] for x in r.results] == [200, 200]
    assert r.results[0]["body"] == {"ok": True, "n": 3}
    assert r.results[1]["body"]["echo"] == {"title": "写沙箱", "done": False}


def test_asgi_reports_validation_error_body(fast_settings):
    r = _run(
        FASTAPI_SNIPPET,
        fast_settings,
        mode="asgi",
        asgi_requests=[{"method": "POST", "path": "/tasks", "body": {"nope": 1}}],
    )
    assert r.results[0]["status"] == 422
    assert "title" in str(r.results[0]["body"])  # FastAPI 的校验错误明细


def test_asgi_mounts_a_bare_router(fast_settings):
    code = (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter(prefix='/tasks')\n\n"
        "@router.get('/all')\nasync def list_all():\n    return [{'id': 1}]\n"
    )
    r = _run(
        code,
        fast_settings,
        mode="asgi",
        asgi_requests=[{"method": "GET", "path": "/tasks/all"}],
    )
    assert r.ok and r.results[0]["status"] == 200
    assert r.results[0]["body"] == [{"id": 1}]


def test_asgi_without_app_reports_readable_error(fast_settings):
    r = _run("print('just code')", fast_settings, mode="asgi")
    assert not r.ok
    assert "没有 FastAPI() 实例" in str(r.results)


def test_asgi_runs_lifespan(fast_settings):
    code = (
        "from contextlib import asynccontextmanager\n"
        "from fastapi import FastAPI\n\n"
        "@asynccontextmanager\n"
        "async def lifespan(app):\n"
        "    print('startup 干活了', flush=True)\n"
        "    yield\n"
        "    print('shutdown 收尾了', flush=True)\n\n"
        "app = FastAPI(lifespan=lifespan)\n\n"
        '@app.get("/")\nasync def root():\n    return {"ok": True}\n'
    )
    r = _run(code, fast_settings, mode="asgi", asgi_requests=[{"path": "/"}])
    assert r.ok, r.stderr
    assert "startup 干活了" in r.stdout and "shutdown 收尾了" in r.stdout


# ---------- 工作目录语义 -------------------------------------------------
def test_same_example_uid_reuses_workdir(fast_settings):
    _run("open('app.db', 'w').write('data')", fast_settings, example_uid="ch07ex01")
    r = _run("print(open('app.db').read())", fast_settings, example_uid="ch07ex01")
    assert r.stdout == "data"
    assert (fast_settings.data_dir / "runs" / "ch07ex01" / "app.db").exists()


def test_path_traversal_uid_is_rejected(fast_settings):
    _run(
        "import os; open('pwn', 'w').write(os.getcwd())",
        fast_settings,
        example_uid="../../escape",
    )
    assert not (fast_settings.data_dir.parent / "escape").exists()
    runs = sorted(p.name for p in (fast_settings.data_dir / "runs").iterdir())
    assert runs and all(not name.startswith("escape") for name in runs)
