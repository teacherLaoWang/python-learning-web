"""从代码块里认出「这段代码定义了哪些接口」，并真跑一遍看能不能跑通。

detect_calls() —— 扫 `@app.get("/tasks/{tid}")` 这类装饰器，生成可以直接发给
                  /api/run 的请求列表（含从 Pydantic 模型编出来的请求体样例）。
                  前端用它预填「当服务调用」的请求编辑器。
probe()        —— 种数据时用：先用 plain 模式跑，片段型例子再用 asgi 模式试，
                  结论写进 examples 表，页面上就有「✓ 实测可跑 / ⚠ 依赖上文」的标记。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings
from app.sandbox import RunResult, run_python

# 路径参数 {tid} 需要补样例值才能真的发请求
SAMPLE_BY_NAME: dict[str, Any] = {
    "id": 1,
    "tid": 1,
    "uid": 1,
    "oid": 1,
    "task_id": 1,
    "user_id": 1,
    "name": "demo",
    "key": "demo",
}

DECORATOR_RE = re.compile(
    # 允许行尾注释：教程里的装饰器常常写成 @router.patch("/{id}")   # 路径参数
    # 注意注释部分必须写成 [^\n]*：本正则开了 DOTALL，用 .*$ 会把整段函数体吞掉
    r"^@(?P<obj>\w+)\.(?P<method>get|post|put|patch|delete|head|options)\b\((?P<args>.*?)\)"
    r"\s*(?:#[^\n]*)?$",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
QUOTED_RE = re.compile(r"""["']([^"']+)["']""")
ROUTER_PREFIX_RE = re.compile(
    r"(?P<obj>\w+)\s*=\s*APIRouter\((?P<args>[^)]*)\)", re.DOTALL
)
MODEL_CLASS_RE = re.compile(
    r"^class\s+(?P<name>\w+)\((?:.*\b)?(?:BaseModel|Schema)(?:,.*)?\)\s*:\n(?P<body>(?:[ \t]+.*\n|\n)*)",
    re.MULTILINE,
)
FIELD_RE = re.compile(r"^\s*(?P<name>\w+)\s*:\s*[^=]+(?:=\s*(?P<default>.+?))?\s*$")

BODY_METHODS = {"POST", "PUT", "PATCH"}

# plain 模式报这些错时，值得用 asgi 模式再试一次（多半是「依赖上文定义的 app/router」）
ERROR_FALLBACK = {
    "error:NameError",
    "error:ImportError",
    "error:ModuleNotFoundError",
    "error:AttributeError",
}


def detect_calls(code: str) -> list[dict[str, Any]]:
    """返回 [{"method","path","body"?}, ...]；一段干净代码至少给一个 GET /。"""
    prefixes: dict[str, str] = {}
    for m in ROUTER_PREFIX_RE.finditer(code):
        pm = re.search(r"prefix\s*=\s*[\"']([^\"']*)[\"']", m["args"])
        prefixes[m["obj"]] = pm.group(1) if pm else ""
    models = _model_fields(code)

    calls: list[dict[str, Any]] = []
    for deco in DECORATOR_RE.finditer(code):
        method = deco["method"].upper()
        raw_path = _first_quoted(deco["args"])
        if not raw_path:
            continue
        path = _join(deco["obj"], prefixes, raw_path)
        call: dict[str, Any] = {"method": method, "path": _fill_params(path)}
        body = _body_for(code, deco.end(), models)
        if method in BODY_METHODS and body:
            call["body"] = body
        calls.append(call)

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in calls:
        key = json.dumps(call, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(call)
    return unique or [{"method": "GET", "path": "/"}]


def _first_quoted(args: str) -> str | None:
    m = QUOTED_RE.search(args)
    return m.group(1) if m else None


def _join(obj: str, prefixes: dict[str, str], path: str) -> str:
    prefix = prefixes.get(obj) or ""
    if not prefix or path.startswith(prefix):
        return path
    left = prefix.rstrip("/")
    right = "" if path == "/" else path if path.startswith("/") else "/" + path
    return left + right or "/"


def _fill_params(path: str) -> str:
    return re.sub(
        r"\{(\w+)(?::[^}]+)?\}",
        lambda m: str(SAMPLE_BY_NAME.get(m.group(1), "1")),
        path,
    )


def _model_fields(code: str) -> dict[str, dict[str, Any]]:
    """粗粒度解析 BaseModel 子类的字段，用来编请求体样例。"""
    out: dict[str, dict[str, Any]] = {}
    for m in MODEL_CLASS_RE.finditer(code):
        fields: dict[str, Any] = {}
        for line in m["body"].splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "def ", "@", '"""')):
                continue
            fm = FIELD_RE.match(line)
            if not fm or fm["name"].startswith("_"):
                continue
            fields[fm["name"]] = _sample_value(fm["name"], fm["default"])
        out[m["name"]] = fields
    return out


def _sample_value(name: str, default: str | None) -> Any:
    if default:
        text = default.strip()
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "none":
            return None
        if lowered.startswith("field("):
            return None  # 复杂默认值不猜，交给学习者自己填
        try:
            return json.loads(text)
        except ValueError:
            return text.strip("\"'")
    return SAMPLE_BY_NAME.get(name, "demo")


def _body_for(
    code: str, deco_end: int, models: dict[str, dict[str, Any]]
) -> dict | None:
    """装饰器下面那个函数里，第一个「类型是本块代码定义的模型」的参数就是请求体。"""
    inner = _params_source(code, deco_end)
    if inner is None:
        return None
    for raw in _split_top_level(inner):
        for token in re.findall(r"\b([A-Z]\w*)\b", raw):
            fields = models.get(token)
            if fields:
                sample = {k: v for k, v in fields.items() if v is not None}
                if sample:
                    return sample
                break
    return None


def _params_source(code: str, start: int) -> str | None:
    """取 start 之后第一个 def 的参数串。

    手写括号配对而不是正则：`Annotated[bool, Query()]` 里自带右括号，
    `[^)]*` 那种写法会在 `Query()` 处提前截断。
    """
    m = re.compile(r"(?:async\s+)?def\s+\w+\s*\(").search(code, start)
    if not m:
        return None
    depth = 1
    collected: list[str] = []
    for char in code[m.end() :]:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return "".join(collected)
        collected.append(char)
    return None  # 括号没闭合（代码块被截断）


def _split_top_level(params: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in params:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def summarize(result: RunResult) -> tuple[str, str]:
    """一次执行 → (状态, 给人看的摘要)。"""
    head = [ln for ln in (result.stdout or result.stderr or "").strip().splitlines()]
    if result.timed_out:
        return "timeout", result.hint or "墙钟超时"
    if result.exit_code is not None and result.exit_code < 0:
        return "killed", result.hint or f"被信号 {-result.exit_code} 终止"

    if result.mode == "asgi":
        if not result.results:
            return "error:no_result", "\n".join(head[-3:])[:300]
        codes = " ".join(
            str(
                r.get("status")
                if r.get("status") is not None
                else f"err:{str(r.get('error'))[:28]}"
            )
            for r in result.results[:6]
        )
        got_response = any(r.get("status") is not None for r in result.results)
        if not got_response:  # 一个响应都没拿到，说明根本没跑起来，不算「有 4xx」
            return "error:no_http_result", codes
        bad = any(
            (r.get("status") or 0) >= 400 or r.get("error") for r in result.results
        )
        sample = next(
            (r.get("body") for r in result.results if r.get("status") == 200), None
        )
        detail = (
            json.dumps(sample, ensure_ascii=False)[:260] if sample is not None else ""
        )
        return (
            "asgi_partial" if bad else "asgi_ok"
        ), f"{codes}{(' | ' + detail) if detail else ''}"

    if result.exit_code != 0:
        return f"error:{_exc_name(result.stderr)}", "\n".join(head[-3:])[:300]
    if not result.stdout.strip():
        return (
            "silent",
            "跑通了但没有输出：这类例子改用「当服务调用」，或自己加两行 print",
        )
    return "ok", "\n".join(head[:6])[:400]


EXC_RE = re.compile(
    r"\b([A-Za-z_][\w.]*(?:Error|Exception|Warning|KeyboardInterrupt|SystemExit))\b"
)


def _exc_name(stderr: str) -> str:
    """从回溯里认出异常类型名。

    从最后一行往前找而不是只看最后一行：pydantic 的报错末尾是一串文档链接，
    真正的 `ValidationError: ...` 在上面几行。
    """
    for line in reversed(
        [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    ):
        m = EXC_RE.search(line)
        if m:
            return m.group(1).rsplit(".", 1)[-1]
    return "unknown"


async def probe(code: str, lang: str, settings: Settings) -> tuple[str, str, str]:
    """返回 (mode, status, output)。非 Python 块标成 skipped，不进沙箱。

    什么时候再试 asgi：代码里有 app/router，且 plain 要么报「依赖上文定义的姓名」
    要么「跑通了但一句输出都没有」——这两种情况 asgi 模式才可能给出有意义的结论。
    SyntaxError 不试：那是代码本身的问题，换个模式照样跑不通。
    """
    if lang != "python":
        return "skip", "skipped", ""

    has_http_app = "FastAPI(" in code or "APIRouter" in code
    plain = await run_python(code, settings=settings)
    status, output = summarize(plain)

    if has_http_app and (status in ERROR_FALLBACK or status == "silent"):
        asgi = await run_python(
            code, mode="asgi", asgi_requests=detect_calls(code), settings=settings
        )
        a_status, a_output = summarize(asgi)
        if not a_status.startswith("error"):
            return "asgi", a_status, a_output
        if status != "silent":  # plain 本来就挂了，asgi 的失败原因更具体
            return "asgi", a_status, a_output
    return "plain", status, output
