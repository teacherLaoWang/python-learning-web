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
from app.models import ApiCard, Chapter, Example, LineNote
from app.probe import detect_calls, probe

STATUS_ORDER = [
    "ok",
    "asgi_ok",
    "silent",
    "asgi_partial",
    "timeout",
    "killed",
    "skipped",
]


def load_notes(notes_dir: Path) -> dict[str, dict[str, Any]]:
    """data/notes/*.json 合并成 {example_uid: {line_notes, api_cards}}。"""
    merged: dict[str, dict[str, Any]] = {}
    if not notes_dir.exists():
        return merged
    for path in sorted(notes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"跳过 {path.name}：{exc}", file=sys.stderr)
            continue
        for uid, payload in data.items():
            if uid in merged:
                print(f"警告：{uid} 在多个 notes 文件里重复，后一个覆盖前一个", file=sys.stderr)
            merged[uid] = payload
    return merged


def _wipe() -> None:
    Base.metadata.drop_all(bind=engine)
    (get_settings().db_path).unlink(missing_ok=True)


def sync(
    session: Session,
    payload: dict[str, Any],
    notes: dict[str, dict[str, Any]],
    *,
    do_probe: bool,
    only_chapter: int | None,
    settings: Settings,
) -> dict[str, int]:
    """核心导入。返回统计。"""
    stats = {"chapters": 0, "examples": 0, "notes": 0, "cards": 0, "probed": 0, "pruned": 0}
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

        for ex_data in ch_data["examples"]:
            seen_uids.append(ex_data["uid"])
            example = session.scalar(select(Example).where(Example.uid == ex_data["uid"]))
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
            _replace_notes(session, example, note.get("line_notes", []))
            _replace_cards(session, example, note.get("api_cards", []))
            stats["notes"] += len(note.get("line_notes", []))
            stats["cards"] += len(note.get("api_cards", []))

            if do_probe:
                mode, status, output = asyncio.run(
                    probe(ex_data["code"], ex_data["lang"], settings)
                )
                example.probe_mode, example.probe_status, example.probe_output = mode, status, output
                stats["probed"] += 1
            stats["examples"] += 1
        session.flush()

    if only_chapter is None and seen_uids:
        kept = session.scalars(select(Example).where(Example.uid.not_in(seen_uids))).all()
        for stale in kept:
            session.delete(stale)
        stats["pruned"] = len(kept)
    return stats


def _replace_notes(session: Session, example: Example, notes: list[dict[str, Any]]) -> None:
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


def _replace_cards(session: Session, example: Example, cards: list[dict[str, Any]]) -> None:
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
    ap.add_argument("--probe", action="store_true", help="每个 Python 例子真跑一遍，记录实测状态")
    ap.add_argument("--chapter", type=int, default=None, help="只处理第 N 章")
    ap.add_argument("--clean", action="store_true", help="先删表删库再建")
    ap.add_argument("--src", type=Path, default=None, help="seed.json 路径，默认取配置")
    args = ap.parse_args(argv)

    settings = get_settings()
    src = args.src or settings.seed_path
    if not src.exists():
        print(f"找不到 {src}，先跑 uv run python tools/extract_tutorial.py", file=sys.stderr)
        return 1

    if args.clean:
        _wipe()
        print("已删除旧库")
    init_db()

    payload = json.loads(src.read_text(encoding="utf-8"))
    notes = load_notes(settings.notes_dir)
    print(
        f"导入：{payload['source']['chapter_count']} 章 / "
        f"{payload['source']['example_count']} 例，notes {len(notes)} 例"
        + ("，并探测可运行性" if args.probe else "")
    )

    with SessionLocal() as session:
        stats = sync(
            session,
            payload,
            notes,
            do_probe=args.probe,
            only_chapter=args.chapter,
            settings=settings,
        )
        session.commit()

    print(
        f"完成：章节 {stats['chapters']} · 例子 {stats['examples']} · "
        f"逐行解释 {stats['notes']} 条 · API 卡片 {stats['cards']} 张 · "
        f"探测 {stats['probed']} · 清理 {stats['pruned']}"
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
        print("探测结果分布：", "  ".join(f"{k}={v}" for k, v in sorted(bucket.items())))
        for ex in rows:
            if ex.probe_status and ex.probe_status.startswith(("error", "timeout", "killed")):
                print(f"  {ex.uid} [{ex.probe_mode}/{ex.probe_status}] {ex.probe_output[:90]}")


if __name__ == "__main__":
    raise SystemExit(main())
