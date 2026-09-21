"""数据模型（SQLAlchemy 2.0 `Mapped[]` 写法）。

  chapters 1 ──┬── n examples 1 ──┬── n line_notes   （逐行：这行在干什么）
               │                  ├── n api_cards    （API：签名/参数/返回/易错点）
               │                  └── n concepts     （模块功能与系统性说明）
               └── n concepts                        （章级基础概念）

examples 存代码原文与探测结果。concepts 两种挂法：挂章上是「这章要先懂什么」，
挂例上是「这段代码属于哪个模块、它管什么」。内容真相源都在 data/notes/*.json。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    index: Mapped[int] = mapped_column(Integer, unique=True, comment="第几章，0 起")
    slug: Mapped[str] = mapped_column(
        String(32), unique=True, comment="URL 用，如 ch07"
    )
    title: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str] = mapped_column(
        String(200), default="", comment="这章学完能干什么"
    )

    examples: Mapped[list[Example]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        order_by="Example.order",
        lazy="selectin",
    )
    concepts: Mapped[list[Concept]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        order_by="Concept.order",
        foreign_keys="Concept.chapter_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Chapter {self.index} {self.title!r} ({len(self.examples)}例)>"


class Example(Base):
    __tablename__ = "examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    uid: Mapped[str] = mapped_column(
        String(32), unique=True, comment="稳定 ID，如 ch07ex02"
    )
    order: Mapped[int] = mapped_column(Integer, comment="章内第几个代码块")

    code: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(16), default="python")
    runnable: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="只有 python 块能进沙箱"
    )
    caption: Mapped[str] = mapped_column(
        String(300), default="", comment="教程里这块代码的文件名标签"
    )
    title: Mapped[str] = mapped_column(
        String(200), default="", comment="人工起的中文小标题，优先于 caption"
    )
    heading: Mapped[str] = mapped_column(
        String(200), default="", comment="所属小节标题"
    )
    line_count: Mapped[int] = mapped_column(Integer, default=0)

    # ---- 探测结果：seed 时真跑一遍，用事实告诉学习者这块能不能独立跑 ----
    probe_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    probe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    probe_output: Mapped[str] = mapped_column(Text, default="")
    calls: Mapped[str] = mapped_column(
        Text, default="[]", comment="JSON：从代码里识别出的接口调用建议"
    )

    chapter: Mapped[Chapter] = relationship(back_populates="examples")
    line_notes: Mapped[list[LineNote]] = relationship(
        back_populates="example",
        cascade="all, delete-orphan",
        order_by="LineNote.line_no",
        lazy="selectin",
    )
    api_cards: Mapped[list[ApiCard]] = relationship(
        back_populates="example",
        cascade="all, delete-orphan",
        order_by="ApiCard.id",
        lazy="selectin",
    )
    concepts: Mapped[list[Concept]] = relationship(
        back_populates="example",
        cascade="all, delete-orphan",
        order_by="Concept.order",
        foreign_keys="Concept.example_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Example {self.uid} {self.lang} {self.line_count}L>"


class LineNote(Base):
    """一行（或一段连续行）代码的解释。line_no 从 1 开始。"""

    __tablename__ = "line_notes"
    __table_args__ = (UniqueConstraint("example_id", "line_no", name="uq_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    example_id: Mapped[int] = mapped_column(
        ForeignKey("examples.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    to_line: Mapped[int] = mapped_column(
        Integer, default=0, comment="0=只讲 line_no 这一行"
    )
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(
        String(16), default="note", comment="note | key | warn"
    )

    example: Mapped[Example] = relationship(back_populates="line_notes")


class ApiCard(Base):
    """这段代码用到的 API / 概念：签名、参数、返回、易错点。"""

    __tablename__ = "api_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    example_id: Mapped[int] = mapped_column(
        ForeignKey("examples.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    signature: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(
        String(24), default="api", comment="api | param | model | concept"
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[str] = mapped_column(
        Text, default="[]", comment="JSON 数组：[{name,type,note}]"
    )
    returns: Mapped[str] = mapped_column(Text, default="")
    gotcha: Mapped[str] = mapped_column(Text, default="")

    example: Mapped[Example] = relationship(back_populates="api_cards")


class Concept(Base):
    """系统性讲解，跟逐行解释分工不同。

    逐行的「这行在干什么」放 line_notes（前端把它内联进代码）；这里放的是要连着读
    才讲清楚的东西：章级的基础概念（ORM 是什么、事件循环为什么存在），以及例级的
    模块功能介绍（这个文件在工程里管什么、和邻居怎么配合）。

    挂载方式二选一：chapter_id 非空 = 整章共用；example_id 非空 = 跟着某个例子走。
    """

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    example_id: Mapped[int | None] = mapped_column(
        ForeignKey("examples.id", ondelete="CASCADE"), nullable=True, index=True
    )
    order: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(
        String(16),
        default="base",
        comment="base 基础概念 | arch 结构与分工 | flow 执行流程 | compare 对比",
    )

    chapter: Mapped[Chapter | None] = relationship(
        back_populates="concepts", foreign_keys=[chapter_id]
    )
    example: Mapped[Example | None] = relationship(
        back_populates="concepts", foreign_keys=[example_id]
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Concept {self.kind} {self.title!r}>"
