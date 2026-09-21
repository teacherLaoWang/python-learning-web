"""种子导入的回归测试。

这条用例是踩过坑才加的：第二次灌库时 SQLAlchemy 会「先 INSERT 新行、后 DELETE 旧行」，
撞上 line_notes 的 (example_id, line_no) 唯一约束，整个 seed --probe 直接崩。
现在 _replace_* 里先清空并 flush，所以重复灌库必须幂等。
"""

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ApiCard, Chapter, Example, LineNote
from app.seed import load_notes, sync

NOTES = {
    "ch02ex01": {
        "line_notes": [
            {"line": 1, "text": "第一行"},
            {"line": 3, "to": 5, "kind": "warn", "text": "三到五行一个坑"},
        ],
        "api_cards": [{"name": "FastAPI", "signature": "FastAPI()", "summary": "应用对象"}],
    }
}


def _counts() -> tuple[int, int, int, int]:
    with SessionLocal() as s:
        return (
            s.scalar(select(func.count()).select_from(Chapter)) or 0,
            s.scalar(select(func.count()).select_from(Example)) or 0,
            s.scalar(select(func.count()).select_from(LineNote)) or 0,
            s.scalar(select(func.count()).select_from(ApiCard)) or 0,
        )


def test_notes_files_are_discoverable():
    """data/notes/*.json 应该被合并进 load_notes，文件名随意起。"""
    merged = load_notes(get_settings().notes_dir)
    assert "ch01ex01" in merged and "ch05ex02" in merged
    assert merged["ch01ex01"]["line_notes"][0]["text"]


def test_seeding_twice_is_idempotent():
    init_db()
    payload = json.loads(get_settings().seed_path.read_text(encoding="utf-8"))
    settings = get_settings()

    with SessionLocal() as session:
        sync(session, payload, NOTES, do_probe=False, only_chapter=None, settings=settings)
        session.flush()
        first = (
            session.scalar(select(func.count()).select_from(Example)),
            session.scalar(select(func.count()).select_from(LineNote)),
            session.scalar(select(func.count()).select_from(ApiCard)),
        )
        # 第二次灌：曾经在这里抛 IntegrityError
        sync(session, payload, NOTES, do_probe=False, only_chapter=None, settings=settings)
        session.flush()
        second = (
            session.scalar(select(func.count()).select_from(Example)),
            session.scalar(select(func.count()).select_from(LineNote)),
            session.scalar(select(func.count()).select_from(ApiCard)),
        )
        example = session.scalar(select(Example).where(Example.uid == "ch02ex01"))
        assert [n.line_no for n in example.line_notes] == [1, 3]
        assert example.line_notes[1].to_line == 5
        session.rollback()  # 不污染开发库

    assert first == second, "重复灌库不该改变行数"


def test_stale_examples_get_pruned():
    """seed.json 里删掉的例子，库里也应该跟着消失（按 uid 对账）。"""
    payload = json.loads(get_settings().seed_path.read_text(encoding="utf-8"))
    trimmed = {
        "chapters": [
            {**ch, "examples": ch["examples"][:1]}
            for ch in payload["chapters"]
            if ch["examples"]
        ]
    }
    with SessionLocal() as session:
        stats = sync(
            session,
            {"chapters": trimmed["chapters"]},
            {},
            do_probe=False,
            only_chapter=None,
            settings=get_settings(),
        )
        session.flush()  # 我们的 session 是 autoflush=False，不显式 flush 数出来还是旧的
        remaining = session.scalar(select(func.count()).select_from(Example))
        session.rollback()
    assert stats["pruned"] > 0
    assert remaining == sum(len(ch["examples"]) for ch in trimmed["chapters"])
