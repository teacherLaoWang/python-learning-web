"""接口层测试。

用 TestClient：不起端口、不占 8100，进程内直接调 ASGI app（和沙箱 asgi 模式同一招）。
`with TestClient(app)` 会跑 lifespan，所以第一次跑会把库建好并自动灌数据。

跑法：uv run pytest tests/test_api.py -v
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app, assets_version


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_meta_reports_content(client):
    body = client.get("/api/meta").json()
    assert body["chapters"] == 12
    assert body["examples"] == 60
    assert body["runnable"] > 40
    assert body["line_notes"] > 30
    assert body["concepts"] >= 13, "章级 + 例级概念都要计入"
    assert body["exec_timeout_ms"] > 0


def test_chapter_numbering_is_normalized(client):
    """教程原始标题第 0 章不带数字、其余带，展示层必须统一成同一口径。"""
    chapters = client.get("/api/chapters").json()
    titles = [c["display_title"] for c in chapters]
    assert titles[0].startswith("第 0 章 · ")
    assert titles[1] == "第 1 章 · Python 补课"
    assert titles[10] == "第 10 章 · 测试与部署"
    assert all(t.startswith(f"第 {i} 章 · ") for i, t in enumerate(titles)), (
        "每章都要有统一编号"
    )
    # 侧栏用的短名不带数字前缀
    assert chapters[1]["short_title"] == "Python 补课"
    assert chapters[0]["short_title"] == "学习路线 · 环境"


def test_chapter_concepts_are_returned(client):
    ch = client.get("/api/chapters/1").json()
    kinds = [c["kind"] for c in ch["concepts"]]
    assert kinds, "第 1 章应该有基础概念"
    assert "base" in kinds
    assert all(c["body"] and c["title"] for c in ch["concepts"])


def test_chapters_list_carries_example_briefs(client):
    chapters = client.get("/api/chapters").json()
    assert [c["index"] for c in chapters] == list(range(12))
    assert chapters[7]["slug"] == "ch07"
    assert chapters[7]["examples"], "第 7 章应该有例子"
    brief = chapters[7]["examples"][0]
    assert {
        "uid",
        "caption",
        "lang",
        "runnable",
        "probe_status",
        "display_no",
        "display_name",
    } <= set(brief)
    assert brief["display_no"] == "7-1"


def test_example_display_name_prefers_chinese_title(client):
    ex = client.get("/api/examples/ch01ex02").json()
    assert ex["title"], "notes 里给这个例子起了中文名"
    assert ex["display_name"] == ex["title"]
    assert ex["caption"] == "model_compare.py"  # 文件名标签还在，只是不当主标题用


def test_example_detail_has_notes_and_cards(client):
    ex = client.get("/api/examples/ch02ex01").json()
    assert "FastAPI" in ex["code"]
    assert isinstance(ex["line_notes"], list) and ex["line_notes"]
    assert isinstance(ex["api_cards"], list) and ex["api_cards"]
    assert isinstance(ex["calls"], list)
    assert ex["concepts"], "例级概念（模块功能介绍）应该在出参里"
    assert all(c["kind"] in {"base", "arch", "flow", "compare"} for c in ex["concepts"])


def test_line_notes_cover_ranges(client):
    """to_line 让一条注释讲一整个代码块（第 1 章 dataclass 那段的 4–7 行）。"""
    ex = client.get("/api/examples/ch01ex02").json()
    ranges = {(n["line_no"], n["to_line"]) for n in ex["line_notes"]}
    assert (4, 7) in ranges
    assert all(n["text"] for n in ex["line_notes"])


def test_filter_examples_by_chapter(client):
    rows = client.get("/api/examples", params={"chapter": 3, "limit": 100}).json()
    assert rows and all(ex["uid"].startswith("ch03") for ex in rows)


def test_only_runnable_filter(client):
    rows = client.get(
        "/api/examples", params={"only_runnable": True, "limit": 200}
    ).json()
    assert rows and all(ex["runnable"] for ex in rows)


def test_missing_example_is_404_with_readable_detail(client):
    resp = client.get("/api/examples/ch99ex99")
    assert resp.status_code == 404
    assert "ch99ex99" in resp.json()["detail"]


def test_run_plain(client):
    result = client.post("/api/run", json={"code": "print(6 * 7)"}).json()
    assert result["ok"] and result["stdout"] == "42"
    assert result["mode"] == "plain" and result["hint"] == ""


def test_run_reports_traceback_and_hint(client):
    result = client.post(
        "/api/run", json={"code": "import os\nos.kill(os.getpid(), 9)"}
    ).json()
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
        json={
            "code": code,
            "mode": "asgi",
            "requests": [{"method": "GET", "path": "/ping"}],
        },
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


def test_static_frontend_is_served_with_asset_version(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Python 逐行解释学习站" in resp.text
    # 首页由路由渲染：占位符必须被换成真实版本号，否则 <link> 会指向不存在的 URL
    assert "__ASSET_V__" not in resp.text
    assert re.search(r'src="app\.js\?v=[0-9a-f]{8}"', resp.text)
    assert re.search(r'href="style\.css\?v=([0-9a-f]{8})"', resp.text)
    assert resp.headers["cache-control"] == "no-store"


def test_asset_version_tracks_file_changes(tmp_path):
    """改样式必须换 URL —— 否则浏览器拿旧副本，新规则根本不生效（踩过）。

    用临时目录喂纯函数，别拿真实 style.css 做 mtime 往返：测试之间读文件会动 atime，
    单跑能过、全量跑就飘。
    """
    (tmp_path / "style.css").write_text(".a{color:red}", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    before = assets_version(tmp_path)

    (tmp_path / "style.css").write_text(".a{color:red}.b{color:blue}", encoding="utf-8")
    assert assets_version(tmp_path) != before, "内容变了版本号就得变"

    missing = tmp_path / "没有这个目录"
    assert len(assets_version(missing)) == 8, "文件不在也不该崩"


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
