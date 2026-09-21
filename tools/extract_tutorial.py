#!/usr/bin/env python3
"""把 FastAPI 教程单文件 HTML 抽成结构化种子数据 data/seed.json。

只用标准库（html.parser），所以在 uv 装好依赖之前也能跑：

    uv run python tools/extract_tutorial.py            # 默认路径
    uv run python tools/extract_tutorial.py --check    # 只打印统计，不写文件

抽出来的字段里 line_notes / api_cards 一律留空数组：逐行解释和 API 说明是
人工预写内容（放在 data/notes/*.json，见 README「补内容」一节），抽取器只负责
把代码和它所属的章节、小标题、上下文固定下来。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT.parent / "FastAPI-learn" / "fastapi-入门教程.html"
DEFAULT_OUT = ROOT / "data" / "seed.json"

HEADING_TAGS = ("h2", "h3", "h4", "h5")

# 先看块的第一行形状，再看全块关键词。教程里的 <pre> 不带 class，只能猜。
FIRST_LINE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bash", re.compile(r"^\s*(\$ |#{1,2}\s?(uv|pip|docker|alembic|git) )|^(uv|pip|docker|alembic|git|cd|mkdir|curl|ls|export|python3?)\s+\S")),# yapf: skip
    ("dockerfile", re.compile(r"^\s*FROM\s+\w")),
    ("http", re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+/")),
    ("sql", re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|PRAGMA)\b", re.IGNORECASE)),
    ("toml", re.compile(r"^\s*\[[\w.]+\]\s*$")),
    ("json", re.compile(r"^\s*[{[]")),
    ("python", re.compile(r'^\s*(from |import |def |async def |class |@|if __name__|with |for |while |try:|match |return |[A-Za-z_][\w.]*\s*(:?=|->))')),
)

# 目录树 / 终端粘贴 / 表格那类块：没有可执行语义
TREE_LINE = re.compile(r"^\s*(├|└|│|·|-{2,}|\s{2,}\w.*/\s*$|\S+\.\w+\s*($|#))")

# 教程里代码块的小标题长这样："main.py python"、"core/config.py — 只读一次 python"，
# 末尾的语言标签是装饰用的，抽出来当 caption 时去掉。
LANG_TAIL = re.compile(r"\s+(python|py|bash|sh|shell|json|sql|toml|ya?ml|dockerfile|http)\s*$", re.IGNORECASE)


@dataclass
class Example:
    uid: str
    order: int
    code: str
    lang: str
    runnable: bool
    heading: str = ""
    context: str = ""
    line_notes: list[dict[str, str]] = field(default_factory=list)
    api_cards: list[dict[str, object]] = field(default_factory=list)


@dataclass
class Chapter:
    index: int
    title: str
    goal: str
    slug: str
    examples: list[Example] = field(default_factory=list)


class TutorialParser(HTMLParser):
    """扫 HTML，按 <section class="chapter"> 归章，按 <pre> 收代码块。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chapters: list[Chapter] = []
        self.current: Chapter | None = None
        self.headings: dict[str, str] = {}
        self.text_parts: list[str] = []  # 正在累积的正文
        self.last_text: str = ""  # 最近一个完整段落，当例子的上下文
        self._pre_lines: list[str] = []
        self._in_pre = False
        self._in_heading: str | None = None
        self._heading_buf: list[str] = []

    # ---- 标签进出 -------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""

        if tag == "section" and "chapter" in cls:
            idx = len(self.chapters)
            title = (a.get("data-title") or "").strip() or f"第 {idx} 章"
            goal = (a.get("data-goal") or "").strip()
            self.current = Chapter(
                index=idx,
                title=title,
                goal=goal,
                slug=f"ch{idx:02d}",
            )
            self.chapters.append(self.current)
            self.headings.clear()
            self.text_parts.clear()
            self.last_text = ""
            return

        if tag in HEADING_TAGS:
            self._in_heading = tag
            self._heading_buf.clear()
            return

        if tag == "pre":
            self._in_pre = True
            self._pre_lines = [""]
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in HEADING_TAGS and self._in_heading == tag:
            text = _clean(" ".join(self._heading_buf))
            if text:
                self.headings[tag] = text
                # 高级标题出现时，低一级的作废
                order = list(HEADING_TAGS)
                for lower in order[order.index(tag) + 1 :]:
                    self.headings.pop(lower, None)
            self._in_heading = None
            self.text_parts.clear()
            self.last_text = ""
            return

        if tag == "pre" and self._in_pre:
            self._in_pre = False
            self._flush_example()
            return

        if tag in ("p", "li", "tr", "div") and not self._in_pre:
            merged = _clean(" ".join(self.text_parts))
            if merged:
                self.last_text = merged
            self.text_parts.clear()

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self._pre_lines[-1] += data
        elif self._in_heading:
            self._heading_buf.append(data)
        else:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    # ---- 产出 -----------------------------------------------------------
    def _flush_example(self) -> None:
        if self.current is None:
            return
        raw = "\n".join(self._pre_lines)
        code = _dedent_block(_trim_blank(raw))
        if not code:
            return
        lang, runnable = _detect_lang(code)
        n = len(self.current.examples) + 1
        self.current.examples.append(
            Example(
                uid=f"{self.current.slug}ex{n:02d}",
                order=n,
                code=code,
                lang=lang,
                runnable=runnable,
                heading=_current_heading(self.headings),
                context=LANG_TAIL.sub("", self.last_text)[:240],
            )
        )
        self.text_parts.clear()


def _current_heading(headings: dict[str, str]) -> str:
    for tag in HEADING_TAGS:
        if tag in headings:
            return headings[tag]
    return ""


def _detect_lang(code: str) -> tuple[str, bool]:
    """返回 (语言, 是否可运行)。只有 Python 块交给后端沙箱执行。"""
    if _looks_like_tree(code):
        return "text", False

    lines = [ln for ln in code.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    for lang, pat in FIRST_LINE_RULES:
        if pat.search(first):
            if lang == "json" and not _is_json(code):
                lang = "python"  # Python 字典/推导式长得像 JSON
            return lang, lang == "python"

    body = "\n".join(lines[:6])
    if re.search(r"^\s*(from|import|def|class|async def)\b", body, re.MULTILINE):
        return "python", True
    return "text", False


def _is_json(code: str) -> bool:
    try:
        json.loads(code)
    except (ValueError, TypeError):
        return False
    return True


def _looks_like_tree(code: str) -> bool:
    lines = [ln for ln in code.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    if re.search(r"^\s*(from|import|def|class|async def|@)\b", "\n".join(lines[:3]), re.MULTILINE):
        return False
    hits = sum(1 for ln in lines if TREE_LINE.search(ln) or ln.rstrip().endswith("/"))
    return hits / len(lines) >= 0.6


def _dedent_block(text: str) -> str:
    lines = text.splitlines()
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    if not indents:
        return ""
    strip = min(indents)
    return "\n".join(ln[strip:].rstrip() if ln.strip() else "" for ln in lines)


def _trim_blank(text: str) -> str:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_payload(src: Path) -> dict[str, object]:
    html = src.read_text(encoding="utf-8")
    parser = TutorialParser()
    parser.feed(html)
    parser.close()

    total = sum(len(c.examples) for c in parser.chapters)
    return {
        "source": {
            "file": str(src),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()[:16],
            "chapter_count": len(parser.chapters),
            "example_count": total,
        },
        "chapters": [
            {
                "index": c.index,
                "slug": c.slug,
                "title": c.title,
                "goal": c.goal,
                "examples": [
                    {
                        "uid": e.uid,
                        "order": e.order,
                        "heading": e.heading,
                        "context": e.context,
                        "lang": e.lang,
                        "runnable": e.runnable,
                        "code": e.code,
                        "line_notes": e.line_notes,
                        "api_cards": e.api_cards,
                    }
                    for e in c.examples
                ],
            }
            for c in parser.chapters
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="教程 HTML 路径")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 seed.json 路径")
    ap.add_argument("--check", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args(argv)

    if not args.src.exists():
        print(f"找不到教程文件：{args.src}", file=sys.stderr)
        return 1

    payload = build_payload(args.src)
    src = payload["source"]
    print(f"章节 {src['chapter_count']} · 代码块 {src['example_count']} · sha {src['sha256']}")
    for ch in payload["chapters"]:  # type: ignore[index]
        langs: dict[str, int] = {}
        for ex in ch["examples"]:
            langs[ex["lang"]] = langs.get(ex["lang"], 0) + 1
        runnable = sum(1 for ex in ch["examples"] if ex["runnable"])
        lang_txt = " ".join(f"{k}:{v}" for k, v in sorted(langs.items()))
        print(f"  ch{ch['index']:02d} {ch['title']:<16} {len(ch['examples']):>2} 块  可跑 {runnable:>2}  {lang_txt}")

    if args.check:
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
