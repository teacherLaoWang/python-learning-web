"""内容接口：读 chapters / examples / line_notes / api_cards。

全部用 Annotated[Session, Depends(get_session)] 的写法（教程第 5 章推荐的风格）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import ApiCard, Chapter, Example, LineNote
from app.schemas import ChapterOut, ExampleOut, MetaOut

router = APIRouter(prefix="/api", tags=["content"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/meta", response_model=MetaOut)
def read_meta(db: SessionDep, settings: SettingsDep) -> MetaOut:
    """站点统计 + 沙箱限额，前端用它显示顶栏。"""
    return MetaOut(
        chapters=db.scalar(select(func.count()).select_from(Chapter)) or 0,
        examples=db.scalar(select(func.count()).select_from(Example)) or 0,
        runnable=db.scalar(
            select(func.count()).select_from(Example).where(Example.runnable.is_(True))
        )
        or 0,
        line_notes=db.scalar(select(func.count()).select_from(LineNote)) or 0,
        api_cards=db.scalar(select(func.count()).select_from(ApiCard)) or 0,
        exec_timeout_ms=int(settings.exec_timeout * 1000),
        max_mem_mb=settings.max_mem_mb,
        db_path=str(settings.db_path.name),
    )


@router.get("/chapters", response_model=list[ChapterOut])
def list_chapters(db: SessionDep) -> list[ChapterOut]:
    """一次查出所有章节及其例子摘要，供顶部 tab 渲染。"""
    rows = db.scalars(select(Chapter).order_by(Chapter.index)).all()
    return [ChapterOut.model_validate(ch) for ch in rows]


@router.get("/chapters/{index}", response_model=ChapterOut)
def read_chapter(index: int, db: SessionDep) -> ChapterOut:
    chapter = db.scalar(select(Chapter).where(Chapter.index == index))
    if chapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"没有第 {index} 章；已有章节请调 GET /api/chapters",
        )
    return chapter


@router.get("/examples", response_model=list[ExampleOut])
def list_examples(
    db: SessionDep,
    chapter: Annotated[int | None, Query(description="只看某章")] = None,
    only_runnable: Annotated[bool, Query(description="只要能进沙箱跑的")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ExampleOut]:
    stmt = select(Example).order_by(Example.uid).limit(limit)
    if chapter is not None:
        stmt = stmt.where(Example.chapter.has(index=chapter))
    if only_runnable:
        stmt = stmt.where(Example.runnable.is_(True))
    return list(db.scalars(stmt).all())


@router.get("/examples/{uid}", response_model=ExampleOut)
def read_example(uid: str, db: SessionDep) -> ExampleOut:
    example = db.scalar(select(Example).where(Example.uid == uid))
    if example is None:
        raise HTTPException(status_code=404, detail=f"没有例子 {uid}")
    return example
