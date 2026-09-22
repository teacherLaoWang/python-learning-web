"""种子导入的回归测试。

这条用例是踩过坑才加的：第二次灌库时 SQLAlchemy 会「先 INSERT 新行、后 DELETE 旧行」，
撞上 line_notes 的 (example_id, line_no) 唯一约束，整个 seed --probe 直接崩。
现在 _replace_* 里先清空并 flush，所以重复灌库必须幂等。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ApiCard, Chapter, Concept, Example, LineNote
from app.seed import load_notes, sync

NOTES = {
    "ch02ex01": {
        "title": "最小可跑服务",
        "concepts": [
            {"kind": "arch", "title": "这块代码在工程里的位置", "body": "入口文件"}
        ],
        "line_notes": [
            {"line": 1, "text": "第一行"},
            {"line": 3, "to": 5, "kind": "warn", "text": "三到五行一个坑"},
        ],
        "api_cards": [
            {"name": "FastAPI", "signature": "FastAPI()", "summary": "应用对象"}
        ],
    }
}
CHAPTER_NOTES = {
    "ch02": {"concepts": [{"kind": "base", "title": "ASGI 是什么", "body": "协议"}]}
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
    examples, chapters = load_notes(get_settings().notes_dir)
    assert "ch01ex01" in examples and "ch05ex02" in examples
    assert "ch01" in chapters, "_chapters 键下的章级内容要单独归一张表"
    assert examples["ch01ex01"]["line_notes"][0]["text"]
    assert chapters["ch01"]["concepts"], "第 1 章应该有基础概念"


def test_broken_notes_file_fails_loudly(tmp_path):
    """notes 文件语法错必须响，不能「跳过 + 报成功」——那会静默吞掉整章解释。"""
    (tmp_path / "好的.json").write_text('{"ch00ex01": {"title": "x"}}', encoding="utf-8")
    (tmp_path / "坏括号.json").write_text('{"ch01ex01": {"title": "x"', encoding="utf-8")
    with pytest.raises(SystemExit) as boom:
        load_notes(tmp_path)
    assert "坏括号.json" in str(boom.value)


def test_every_chapter_has_some_content():
    """内容进度哨兵：哪一章整体空着，这里先响，而不是等页面上看到一片空白。"""
    examples, chapters = load_notes(get_settings().notes_dir)
    by_chapter: dict[str, int] = {}
    for uid in examples:
        by_chapter[uid[:4]] = by_chapter.get(uid[:4], 0) + 1
    assert len(by_chapter) == 12, f"12 章都该有内容，现在只有 {sorted(by_chapter)}"
    assert set(chapters) >= {"ch01", "ch02", "ch03", "ch04", "ch05", "ch06", "ch07"}


def test_seeding_twice_is_idempotent():
    init_db()
    payload = json.loads(get_settings().seed_path.read_text(encoding="utf-8"))
    settings = get_settings()

    with SessionLocal() as session:
        sync(
            session,
            payload,
            NOTES,
            do_probe=False,
            only_chapter=None,
            settings=settings,
            chapter_notes=CHAPTER_NOTES,
        )
        session.flush()
        first = (
            session.scalar(select(func.count()).select_from(Example)),
            session.scalar(select(func.count()).select_from(LineNote)),
            session.scalar(select(func.count()).select_from(ApiCard)),
            session.scalar(select(func.count()).select_from(Concept)),
        )
        # 第二次灌：曾经在这里抛 IntegrityError
        sync(
            session,
            payload,
            NOTES,
            do_probe=False,
            only_chapter=None,
            settings=settings,
            chapter_notes=CHAPTER_NOTES,
        )
        session.flush()
        second = (
            session.scalar(select(func.count()).select_from(Example)),
            session.scalar(select(func.count()).select_from(LineNote)),
            session.scalar(select(func.count()).select_from(ApiCard)),
            session.scalar(select(func.count()).select_from(Concept)),
        )
        example = session.scalar(select(Example).where(Example.uid == "ch02ex01"))
        chapter = session.scalar(select(Chapter).where(Chapter.index == 2))
        assert [n.line_no for n in example.line_notes] == [1, 3]
        assert example.line_notes[1].to_line == 5
        assert example.title == "最小可跑服务"
        assert [c.title for c in example.concepts] == ["这块代码在工程里的位置"]
        assert [c.title for c in chapter.concepts] == ["ASGI 是什么"], (
            "章级概念挂在 chapter_id 上"
        )
        assert all(c.example_id is None for c in chapter.concepts)
        session.rollback()  # 不污染开发库

    assert first == second, "重复灌库不该改变行数"
    assert first[3] >= 2, "例级 + 章级概念都要落库"


def test_duplicate_line_note_is_named():
    """同一行写了两条解释要指名道姓地报错，而不是抛 SQLAlchemy 回溯。"""
    notes = {"ch02ex01": {"line_notes": [
        {"line": 3, "text": "第一条"},
        {"line": 3, "text": "第二条撞车"},
    ]}}
    init_db()
    payload = json.loads(get_settings().seed_path.read_text(encoding="utf-8"))
    with SessionLocal() as session:
        with pytest.raises(SystemExit) as boom:
            sync(session, payload, notes, do_probe=False, only_chapter=2,
                 settings=get_settings())
        session.rollback()
    assert "ch02ex01" in str(boom.value) and "第 3 行" in str(boom.value)


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
