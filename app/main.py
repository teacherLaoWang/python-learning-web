"""站点入口。

    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8100

结构刻意跟着教程走：lifespan 建库/自动播种、Annotated 依赖、中间件加请求号与耗时、
全局异常处理返回统一响应体、静态前端挂在最后（API 路由优先匹配）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import content, run
from app.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.models import Example
from app.seed import load_notes, sync

# 这三个文件改完要立刻见效，不给浏览器缓存机会
NO_CACHE_PATHS = frozenset({"/", "/index.html", "/app.js", "/style.css"})


def assets_version(static_dir: Path) -> str:
    """按 (mtime, size) 给前端资源算一个短版本号。

    为什么非要版本号：光靠 Cache-Control 治不住所有情况——实测改完 style.css，
    `/style.css` 走 fetch(no-store) 拿到的是新内容，但 <link> 仍用内存里的旧副本，
    页面上的新规则死活不生效。URL 变了就彻底没这个问题，而且能长期缓存内容本身。
    """
    digest = hashlib.sha1()
    for name in ("style.css", "prose.js", "app.js"):
        path = static_dir / name
        if path.exists():
            stat = path.stat()
            digest.update(f"{name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return digest.hexdigest()[:8]


def _ensure_seeded(settings: Settings) -> str:
    """库里没内容就自动灌一次（不含探测，秒级完成）。"""
    with SessionLocal() as session:
        if (
            session.scalar(select(func.count()).select_from(Example))
            or not settings.seed_path.exists()
        ):
            return "已就绪"
        payload = json.loads(settings.seed_path.read_text(encoding="utf-8"))
        notes, chapter_notes = load_notes(settings.notes_dir)
        stats = sync(
            session,
            payload,
            notes,
            do_probe=False,
            only_chapter=None,
            settings=settings,
            chapter_notes=chapter_notes,
        )
        session.commit()
    return f"自动灌入 {stats['chapters']} 章 / {stats['examples']} 例"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    app.state.started_at = time.time()
    print(
        f"[learn] 数据库 {settings.db_path.name} · {_ensure_seeded(settings)}",
        flush=True,
    )
    yield
    print("[learn] 关闭", flush=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Python 逐行解释学习站",
        version="0.1.0",
        description=(
            "左侧是 FastAPI 教程里的 60 个代码块，点开任意一行看逐行解释；"
            "代码可以直接改、直接跑。plain 模式当脚本跑，asgi 模式把 app 当服务打。"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def add_request_id_and_timing(request: Request, call_next) -> Response:
        """每个请求带一个短 id 和耗时头，对着日志找问题很方便（教程第 9 章）。"""
        started = time.perf_counter()
        request.state.request_id = uuid.uuid4().hex[:8]
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Process-Time-ms"] = (
            f"{(time.perf_counter() - started) * 1000:.1f}"
        )
        if request.url.path in NO_CACHE_PATHS:
            # 前端三件套改完必须立刻见效。踩过的坑：改了 style.css 加新规则，
            # 页面上的开关死活不生效——查出来是浏览器拿的是旧副本（no-cache 允许
            # 带 ETag 回来，本机工具直接 no-store 更省心）。
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """兜底：不把回溯原样吐给浏览器，但本地排错要看得到异常类型。"""
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "服务端异常，请看 uvicorn 日志",
                "type": type(exc).__name__,
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """统一响应体：404 也长成 {detail: ...}，前端只认一种形状。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", None),
            },
            headers=getattr(exc, "headers", None),
        )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "uptime_s": round(
                time.time() - getattr(app.state, "started_at", time.time())
            ),
            "host": settings.host,
            "port": settings.port,
            "timeout_ms": int(settings.exec_timeout * 1000),
        }

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        """首页：把资源版本号注进 index.html，保证改完前端刷新就见效。"""
        html = (settings.static_dir / "index.html").read_text(encoding="utf-8")
        html = html.replace("__ASSET_V__", assets_version(settings.static_dir))
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.include_router(content.router)
    app.include_router(run.router)

    static_dir = Path(settings.static_dir)
    if static_dir.exists():
        # 挂在 "/" 必须放最后：API 路由先匹配，剩下的才交给静态文件
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


app = create_app()
