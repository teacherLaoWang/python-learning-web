"""接口层测试。

用 TestClient：不起端口、不占 8100，进程内直接调 ASGI app（和沙箱 asgi 模式同一招）。
`with TestClient(app)` 会跑 lifespan，所以第一次跑会把库建好并自动灌数据。

跑法：uv run pytest tests/test_api.py -v
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_meta_reports_content(client):
    body = client.get("/api/meta").json()
    assert body["chapters"] == 12
    assert body["examples"] == 60
    assert body["runnable"] > 40
    assert body["exec_timeout_ms"] > 0


def test_chapters_list_carries_example_briefs(client):
    chapters = client.get("/api/chapters").json()
    assert [c["index"] for c in chapters] == list(range(12))
    assert chapters[7]["slug"] == "ch07"
    assert chapters[7]["examples"], "第 7 章应该有例子"
    assert {"uid", "caption", "lang", "runnable", "probe_status"} <= set(chapters[7]["examples"][0])


def test_filter_examples_by_chapter(client):
    rows = client.get("/api/examples", params={"chapter": 3, "limit": 100}).json()
    assert rows and all(ex["uid"].startswith("ch03") for ex in rows)


def test_only_runnable_filter(client):
    rows = client.get("/api/examples", params={"only_runnable": True, "limit": 200}).json()
    assert rows and all(ex["runnable"] for ex in rows)


def test_example_detail_has_notes_and_cards(client):
    ex = client.get("/api/examples/ch02ex01").json()
    assert "FastAPI" in ex["code"]
    assert isinstance(ex["line_notes"], list)
    assert isinstance(ex["api_cards"], list)
    assert isinstance(ex["calls"], list)


def test_missing_example_is_404_with_readable_detail(client):
    resp = client.get("/api/examples/ch99ex99")
    assert resp.status_code == 404
    assert "ch99ex99" in resp.json()["detail"]


def test_run_plain(client):
    result = client.post("/api/run", json={"code": "print(6 * 7)"}).json()
    assert result["ok"] and result["stdout"] == "42"
    assert result["mode"] == "plain" and result["hint"] == ""


def test_run_reports_traceback_and_hint(client):
    result = client.post("/api/run", json={"code": "import os\nos.kill(os.getpid(), 9)"}).json()
    assert not result["ok"]
    assert result["exit_code"] == -9 and "信号" in result["hint"]


def test_run_asgi_hits_the_endpoint_in_process(client):
    code = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.get("/ping")\nasync def ping():\n    return {"pong": True}\n'
    )
    result = client.post(
        "/api/run",
        json={"code": code, "mode": "asgi", "requests": [{"method": "GET", "path": "/ping"}]},
    ).json()
    assert result["ok"], result["stderr"]
    assert result["results"][0]["status"] == 200
    assert result["results"][0]["body"] == {"pong": True}


def test_run_rejects_overlong_code(client):
    resp = client.post("/api/run", json={"code": "x" * 20_001})
    assert resp.status_code in {413, 422}  # 422：Pydantic max_length 先拦下


def test_calls_endpoint_guesses_routes(client):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.post("/items")\nasync def create(item: Item):\n    return item\n'
    )
    calls = client.post("/api/calls", json={"code": code}).json()["calls"]
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/items"


def test_static_frontend_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Python 逐行解释学习站" in resp.text
    assert 'src="app.js"' in resp.text


def test_openapi_and_docs_available(client):
    spec = client.get("/openapi.json").json()
    assert "/api/run" in spec["paths"]
    assert client.get("/docs").status_code == 200


def test_request_id_and_timing_headers(client):
    resp = client.get("/api/health")
    assert len(resp.headers["x-request-id"]) == 8
    assert float(resp.headers["x-process-time-ms"]) >= 0


def test_health_reports_limits(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["host"] == "127.0.0.1"
    assert json.dumps(body)  # 确保可序列化
