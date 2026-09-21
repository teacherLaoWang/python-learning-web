"""出入参契约（Pydantic v2）。FastAPI 靠这些模型生成 /docs 里的 schema。"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
)


class LineNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int
    to_line: int = 0
    text: str
    kind: Literal["note", "key", "warn"] = "note"


class ApiCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    signature: str = ""
    kind: str = "api"
    summary: str = ""
    returns: str = ""
    gotcha: str = ""

    # params 在库里是 JSON 字符串，靠下面的校验器转成列表再输出
    params: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("params", mode="before")
    @classmethod
    def _load_params(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return []
        return value or []


class ConceptOut(BaseModel):
    """系统性讲解：章级的基础概念，或例级的模块功能介绍。"""

    model_config = ConfigDict(from_attributes=True)

    title: str
    body: str = ""
    kind: Literal["base", "arch", "flow", "compare"] = "base"
    order: int = 0


class ExampleBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    order: int
    caption: str
    title: str = ""
    lang: str
    runnable: bool
    line_count: int
    probe_status: str | None = None

    @computed_field
    @property
    def display_no(self) -> str:
        """ch07ex02 → 7-2，比裸 uid 好读。"""
        m = re.match(r"ch(\d+)ex(\d+)", self.uid)
        return f"{int(m.group(1))}-{int(m.group(2))}" if m else self.uid

    @computed_field
    @property
    def display_name(self) -> str:
        """优先人工中文标题，其次教程里的文件名标签。"""
        return self.title or self.caption or f"例子 {self.order}"


class ExampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    order: int
    caption: str
    title: str = ""
    heading: str = ""
    lang: str
    runnable: bool
    code: str
    line_count: int
    probe_mode: str | None = None
    probe_status: str | None = None
    probe_output: str = ""
    calls: list[AsgiRequest] = Field(default_factory=list)
    line_notes: list[LineNoteOut] = Field(default_factory=list)
    api_cards: list[ApiCardOut] = Field(default_factory=list)
    concepts: list[ConceptOut] = Field(default_factory=list)

    @computed_field
    @property
    def display_no(self) -> str:
        m = re.match(r"ch(\d+)ex(\d+)", self.uid)
        return f"{int(m.group(1))}-{int(m.group(2))}" if m else self.uid

    @computed_field
    @property
    def display_name(self) -> str:
        return self.title or self.caption or f"例子 {self.order}"

    @field_validator("calls", mode="before")
    @classmethod
    def _load_calls(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return []
        return value or []

    @field_serializer("calls")
    def _dump_calls(self, calls: list[AsgiRequest]) -> list[dict[str, object]]:
        """没填的 params/body/headers 别吐给前端，请求清单里一屏 null 没法读。"""
        return [c.model_dump(exclude_none=True) for c in calls]


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    slug: str
    title: str
    goal: str = ""
    examples: list[ExampleBrief] = Field(default_factory=list)
    concepts: list[ConceptOut] = Field(default_factory=list)

    @computed_field
    @property
    def display_title(self) -> str:
        """统一编号口径。

        教程的 data-title 是 "1 Python 补课"、"10 测试与部署"，但第 0 章是
        "学习路线 · 环境"（不带数字）、附录是 "11 附录 · 速查"——直接拿来显示就会出现
        侧栏没编号、正文有编号、第 0 章两边都不一样的情况。这里统一按 index 重新生成。
        """
        bare = re.sub(r"^\s*\d+\s*[.、·\-]?\s*", "", self.title).strip()
        return f"第 {self.index} 章 · {bare or self.title}"

    @computed_field
    @property
    def short_title(self) -> str:
        """侧栏用的纯名字（去掉数字前缀）。"""
        return re.sub(r"^\s*\d+\s*[.、·\-]?\s*", "", self.title).strip() or self.title


Scalar = str | int | float | bool


class AsgiRequest(BaseModel):
    """asgi 模式下的一个 HTTP 请求：不打网络，直接在进程内喂给 app。

    字段叫 body 而不是 json：BaseModel 自带 .json() 方法，同名会撞车。
    """

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    path: str = "/"
    params: dict[str, Scalar] | None = None
    body: dict | list | None = None
    headers: dict[str, str] | None = None


class RunRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20_000)
    mode: Literal["plain", "asgi"] = "plain"
    requests: list[AsgiRequest] = Field(default_factory=list)
    example_uid: str | None = None


class AsgiResult(BaseModel):
    method: str
    path: str
    status: int | None = None
    body: object = None
    error: str | None = None


class RunResult(BaseModel):
    ok: bool
    mode: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    results: list[AsgiResult] = Field(default_factory=list)
    workdir: str = ""
    peak_rss_mb: int = 0
    hint: str = ""


class MetaOut(BaseModel):
    """顶栏统计：库里有什么 + 沙箱限额是多少，全透明给前端。"""

    chapters: int
    examples: int
    runnable: int
    line_notes: int
    api_cards: int
    concepts: int = 0
    exec_timeout_ms: int
    max_mem_mb: int
    db_path: str = "app.db"
    version: str = "0.1.0"


class CallsOut(BaseModel):
    calls: list[AsgiRequest] = Field(default_factory=list)
