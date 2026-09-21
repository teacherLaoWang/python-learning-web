"""接口识别与状态判定的单元测试（不起子进程，跑得快）。

detect_calls 决定「⇄ 当服务调用」按钮预填什么；summarize 决定例子标题上那个
实测徽章写什么。这两处错了会直接误导学习者，所以单独钉住。
"""

from __future__ import annotations

from app.probe import _exc_name, _model_fields, detect_calls, summarize
from app.sandbox import RunResult

SNIPPET = """
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str = Field(min_length=1)
    done: bool = False
    priority: int = 3


@router.post("/{tid}", status_code=201)          # 路径参数 + 行尾注释
async def create(tid: int, payload: TaskCreate) -> dict:
    return {"tid": tid}
"""


def test_detect_calls_uses_router_prefix():
    (call,) = [c for c in detect_calls(SNIPPET) if c["method"] == "POST"]
    assert call["path"] == "/tasks/1", "prefix 要和装饰器路径拼起来，{tid} 要补样例值"


def test_detect_calls_builds_body_from_model_fields():
    (call,) = [c for c in detect_calls(SNIPPET) if c["method"] == "POST"]
    # Field(min_length=1) 这种复杂默认值不猜，所以没有 title
    assert call["body"] == {"done": False, "priority": 3}


def test_detect_calls_falls_back_to_root():
    assert detect_calls("print('无路由')") == [{"method": "GET", "path": "/"}]


def test_model_fields_keeps_scalars_and_drops_complex_defaults():
    fields = _model_fields(SNIPPET)["TaskCreate"]
    assert fields["done"] is False and fields["priority"] == 3
    assert fields["title"] is None, "Field(...) 的默认值不猜，留给学习者填"


def test_exc_name_finds_exception_above_doc_link():
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "snippet.py", line 3, in <module>\n    Point(x="abc")\n'
        "pydantic_core._pydantic_core.ValidationError: 1 validation error\n"
        "  For further information visit https://errors.pydantic.dev/2.13/v/missing"
    )
    assert _exc_name(stderr) == "ValidationError"


def _result(**kw) -> RunResult:
    base = {
        "ok": True,
        "mode": "plain",
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 10,
        "stdout": "",
        "stderr": "",
        "truncated": False,
        "results": [],
        "workdir": "",
        "peak_rss_mb": 1,
        "hint": "",
    }
    base.update(kw)
    return RunResult(**base)


def test_summarize_plain_states():
    assert summarize(_result(stdout="hi")) == ("ok", "hi")
    assert summarize(_result())[0] == "silent"
    assert (
        summarize(
            _result(
                mode="plain", exit_code=1, stderr="NameError: name 'app' is not defined"
            )
        )[0]
        == "error:NameError"
    )


def test_summarize_asgi_ok_and_partial():
    ok = _result(
        mode="asgi",
        results=[{"method": "GET", "path": "/tasks", "status": 200, "body": {"n": 1}}],
    )
    status, output = summarize(ok)
    assert status == "asgi_ok" and "200" in output and '{"n": 1}' in output

    partial = _result(
        mode="asgi",
        results=[
            {"method": "GET", "path": "/tasks", "status": 200, "body": {}},
            {"method": "DELETE", "path": "/tasks/1", "status": 405},
        ],
    )
    assert summarize(partial)[0] == "asgi_partial"


def test_summarize_treats_no_http_response_as_error_not_partial():
    """跑都没跑起来（一个 status 都没有）不能标成「接口有 4xx」，那是误导。"""
    dead = _result(
        mode="asgi",
        exit_code=1,
        stderr="SyntaxError: 'await' outside function",
        results=[
            {"method": "-", "path": "-", "error": "snippet.py 执行失败，看上面的回溯"}
        ],
    )
    status, _ = summarize(dead)
    assert status == "error:no_http_result"


def test_summarize_timeout_and_signal():
    assert summarize(_result(timed_out=True, hint="墙钟超时被终止"))[0] == "timeout"
    assert summarize(_result(exit_code=-9, ok=False))[0] == "killed"
