"""执行接口：POST /api/run。

plain 模式 = 把代码当脚本跑，回收 stdout/stderr/回溯；
asgi 模式 = 把代码里的 FastAPI app 当服务打，不占端口、不发网络包。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.probe import detect_calls
from app.sandbox import run_python
from app.schemas import CallsOut, RunRequest, RunResult

router = APIRouter(prefix="/api", tags=["run"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/run", response_model=RunResult)
async def run_code(payload: RunRequest, settings: SettingsDep) -> RunResult:
    """跑一段代码。返回结构里 hint 字段专门解释「为什么没跑出来」。"""
    if len(payload.code) > 20_000:
        raise HTTPException(413, "代码太长，先删减到 2 万字符以内")
    result = await run_python(
        payload.code,
        mode=payload.mode,
        asgi_requests=[req.model_dump(exclude_none=True) for req in payload.requests],
        example_uid=payload.example_uid,
        settings=settings,
    )
    return RunResult(**asdict(result))


@router.post("/calls", response_model=CallsOut, response_model_exclude_none=True)
def guess_calls(payload: RunRequest) -> CallsOut:
    """编辑器改过代码之后，重新识别一遍里面定义了哪些接口。

    response_model_exclude_none：没填的 params/body/headers 别一起吐给前端，
    不然请求清单里全是 null，改起来碍眼。
    """
    return CallsOut(calls=detect_calls(payload.code))
