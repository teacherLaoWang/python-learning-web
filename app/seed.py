"""把 data/seed.json（抽取器产出）+ data/notes/*.json（人工逐行解释）灌进 SQLite。

    uv run python -m app.seed                 # 只灌内容，不跑代码
    uv run python -m app.seed --probe         # 顺手把每个 Python 例子真跑一遍
    uv run python -m app.seed --probe --chapter 7   # 只探测某一章
    uv run python -m app.seed --clean         # 先删库重建（改表结构后用）

幂等：按 examples.uid 做 upsert，重复跑不会出现重复行；seed.json 里已删除的例子
会从库里连带 line_notes / api_cards 一起级联删掉。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, SessionLocal, engine, init_db
from app.models import ApiCard, Chapter, Concept, Example, LineNote
from app.probe import detect_calls, probe


def load_notes(
    notes_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """读 data/notes/*.json，拆成（例子内容, 章级内容）两张表。

    约定：文件里以 `ch\\d+ex\\d+` 为键的是例子；特殊的 `_chapters` 键下按 slug
    （ch07 这种）挂章级内容，长这样：

        {"_chapters": {"ch07": {"concepts": [{"title": "ORM 替你做了什么", "body": "…"}]}}}

    一个文件可以跨章（比如 ch02-ch05.json），所以章级内容必须显式写 slug，不能猜。
    """
    examples: dict[str, dict[str, Any]] = {}
    chapters: dict[str, dict[str, Any]] = {}
    if not notes_dir.exists():
        return examples, chapters
    for path in sorted(notes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"跳过 {path.name}：{exc}", file=sys.stderr)
            continue
        for key, payload in data.items():
            if key == "_chapters":
                for slug, meta in (payload or {}).items():
                    chapters.setdefault(slug, {}).update(meta)
                continue
            if key in examples:
                print(
                    f"警告：{key} 在多个 notes 文件里重复，后一个覆盖前一个",
                    file=sys.stderr,
                )
            examples[key] = payload
    return examples, chapters


def _build_concepts(items: list[dict[str, Any]]) -> list[Concept]:
    built: list[Concept] = []
    for n, item in enumerate(items):
        if not item.get("title"):
            continue
        built.append(
            Concept(
                order=n,
                title=str(item["title"]).strip(),
                body=str(item.get("body", "")).strip(),
                kind=item.get("kind", "base"),
            )
        )
    return built


def _replace_concepts(
    session: Session,
    owner: Chapter | Example,
    items: list[dict[str, Any]],
    *,
    chapter_id: int | None = None,
    example_id: int | None = None,
) -> None:
    """章级概念挂 chapter_id，例级模块说明挂 example_id。"""
    built = _build_concepts(items)
    for one in built:
        one.chapter_id = chapter_id
        one.example_id = example_id
    owner.concepts = []
    session.flush()
    owner.concepts = built


def _wipe() -> None:
    """删表 + 删文件。

    必须连 WAL 的两个旁文件一起删：只删 app.db 的话，残留的 app.db-wal / app.db-shm
    会让下一个打开新库的进程按旧索引找页，报「no such table: chapters」。
    """
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    db = get_settings().db_path
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)


def sync(
    session: Session,
    payload: dict[str, Any],
    notes: dict[str, dict[str, Any]],
    *,
    do_probe: bool,
    only_chapter: int | None,
    settings: Settings,
    chapter_notes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """核心导入。返回统计。"""
    stats = {
        "chapters": 0,
        "examples": 0,
        "notes": 0,
        "cards": 0,
        "concepts": 0,
        "probed": 0,
        "pruned": 0,
    }
    chapter_notes = chapter_notes or {}
    seen_uids: list[str] = []

    for ch_data in payload["chapters"]:
        if only_chapter is not None and ch_data["index"] != only_chapter:
            continue
        chapter = session.scalar(select(Chapter).where(Chapter.slug == ch_data["slug"]))
        if chapter is None:
            chapter = Chapter(
                index=ch_data["index"],
                slug=ch_data["slug"],
                title=ch_data["title"],
                goal=ch_data.get("goal", ""),
            )
            session.add(chapter)
        else:
            chapter.index = ch_data["index"]
            chapter.title = ch_data["title"]
            chapter.goal = ch_data.get("goal", "")
        session.flush()  # 先拿到 chapter.id，下面挂例子要用
        stats["chapters"] += 1

        meta = chapter_notes.get(ch_data["slug"], {})
        _replace_concepts(
            session, chapter, meta.get("concepts", []), chapter_id=chapter.id
        )
        stats["concepts"] += len(meta.get("concepts", []))

        for ex_data in ch_data["examples"]:
            seen_uids.append(ex_data["uid"])
            example = session.scalar(
                select(Example).where(Example.uid == ex_data["uid"])
            )
            if example is None:
                example = Example(uid=ex_data["uid"], chapter_id=chapter.id)
                session.add(example)
            example.chapter_id = chapter.id
            example.order = ex_data["order"]
            example.code = ex_data["code"]
            example.lang = ex_data["lang"]
            example.runnable = ex_data["runnable"]
            example.caption = ex_data.get("context", "")
            example.heading = ex_data.get("heading", "")
            example.line_count = len(ex_data["code"].splitlines())
            example.calls = json.dumps(
                detect_calls(ex_data["code"]) if ex_data["lang"] == "python" else [],
                ensure_ascii=False,
            )

            note = notes.get(ex_data["uid"], {})
            example.title = note.get("title", "")
            _replace_notes(session, example, note.get("line_notes", []))
            _replace_cards(session, example, note.get("api_cards", []))
            _replace_concepts(
                session, example, note.get("concepts", []), example_id=example.id
            )
            stats["notes"] += len(note.get("line_notes", []))
            stats["cards"] += len(note.get("api_cards", []))
            stats["concepts"] += len(note.get("concepts", []))

            if do_probe:
                mode, status, output = asyncio.run(
                    probe(ex_data["code"], ex_data["lang"], settings)
                )
                example.probe_mode, example.probe_status, example.probe_output = (
                    mode,
                    status,
                    output,
                )
                stats["probed"] += 1
            stats["examples"] += 1
        session.flush()

    if only_chapter is None and seen_uids:
        kept = session.scalars(
            select(Example).where(Example.uid.not_in(seen_uids))
        ).all()
        for stale in kept:
            session.delete(stale)
        stats["pruned"] = len(kept)
    return stats


def _replace_notes(
    session: Session, example: Example, notes: list[dict[str, Any]]
) -> None:
    """整段重建比 diff 简单可靠：notes 文件是真相源。

    坑在这里：直接 `example.line_notes = 新列表` 时，SQLAlchemy 会先 INSERT 新行、
    后 DELETE 旧行，于是撞上 (example_id, line_no) 唯一约束（第二次灌库必炸）。
    所以先清空并 flush，把删除真正落到库里，再写新内容。
    """
    built: list[LineNote] = []
    for item in notes:
        line = int(item.get("line", 0))
        if line < 1 or not item.get("text"):
            continue
        built.append(
            LineNote(
                line_no=line,
                to_line=int(item.get("to", 0)) or 0,
                text=str(item["text"]).strip(),
                kind=item.get("kind", "note"),
            )
        )
    example.line_notes = []
    session.flush()
    example.line_notes = built


def _replace_cards(
    session: Session, example: Example, cards: list[dict[str, Any]]
) -> None:
    built = [
        ApiCard(
            name=str(item["name"]),
            signature=str(item.get("signature", "")),
            kind=str(item.get("kind", "api")),
            summary=str(item.get("summary", "")),
            params=json.dumps(item.get("params", []), ensure_ascii=False),
            returns=str(item.get("returns", "")),
            gotcha=str(item.get("gotcha", "")),
        )
        for item in cards
        if item.get("name")
    ]
    example.api_cards = []
    session.flush()
    example.api_cards = built


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--probe", action="store_true", help="每个 Python 例子真跑一遍，记录实测状态"
    )
    ap.add_argument("--chapter", type=int, default=None, help="只处理第 N 章")
    ap.add_argument("--clean", action="store_true", help="先删表删库再建")
    ap.add_argument("--src", type=Path, default=None, help="seed.json 路径，默认取配置")
    args = ap.parse_args(argv)

    settings = get_settings()
    src = args.src or settings.seed_path
    if not src.exists():
        print(
            f"找不到 {src}，先跑 uv run python tools/extract_tutorial.py",
            file=sys.stderr,
        )
        return 1

    if args.clean:
        print(
            "提示：--clean 会删掉 app.db 及 WAL 旁文件。"
            "如果 uvicorn 还在跑，它仍握着旧 inode，之后就会 disk I/O error —— 先停服务再 clean。",
            file=sys.stderr,
        )
        _wipe()
        print("已删除旧库")
    init_db()

    payload = json.loads(src.read_text(encoding="utf-8"))
    notes, chapter_notes = load_notes(settings.notes_dir)
    print(
        f"导入：{payload['source']['chapter_count']} 章 / "
        f"{payload['source']['example_count']} 例，notes {len(notes)} 例 · "
        f"章级内容 {len(chapter_notes)} 章" + ("，并探测可运行性" if args.probe else "")
    )

    with SessionLocal() as session:
        stats = sync(
            session,
            payload,
            notes,
            do_probe=args.probe,
            only_chapter=args.chapter,
            settings=settings,
            chapter_notes=chapter_notes,
        )
        session.commit()

    print(
        f"完成：章节 {stats['chapters']} · 例子 {stats['examples']} · "
        f"逐行解释 {stats['notes']} 条 · API 卡片 {stats['cards']} 张 · "
        f"概念 {stats['concepts']} 篇 · 探测 {stats['probed']} · 清理 {stats['pruned']}"
    )
    if args.probe:
        _print_status_table()
    return 0


def _print_status_table() -> None:
    with SessionLocal() as session:
        rows = session.scalars(select(Example).order_by(Example.uid)).all()
        bucket: dict[str, int] = {}
        for ex in rows:
            key = (ex.probe_status or "?").split(":")[0]
            bucket[key] = bucket.get(key, 0) + 1
        print(
            "探测结果分布：", "  ".join(f"{k}={v}" for k, v in sorted(bucket.items()))
        )
        for ex in rows:
            if ex.probe_status and ex.probe_status.startswith(
                ("error", "timeout", "killed")
            ):
                print(
                    f"  {ex.uid} [{ex.probe_mode}/{ex.probe_status}] {ex.probe_output[:90]}"
                )


if __name__ == "__main__":
    raise SystemExit(main())
