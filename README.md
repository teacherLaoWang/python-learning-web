# Python 逐行解释学习站

把 `../FastAPI-learn/fastapi-入门教程.html` 里的 **12 章 / 60 个代码块**抽成结构化数据，
做成一个「点开任意一行代码 → 看逐行解释 → 直接改直接跑」的学习站。

- 后端：FastAPI + SQLAlchemy 2.0 + SQLite，负责内容与执行
- 前端：纯静态 HTML/CSS/JS，**没有构建步骤、不引任何 CDN**
- 执行：真·本机 CPython（项目的 `.venv`），在受限子进程里跑

这个仓库本身就是教程第 2/3/4/5/6/7/9 章的活样本：`lifespan`、`Annotated[..., Depends()]`、
`response_model`、分层结构、SQLAlchemy 2.0 `Mapped[]`、统一异常响应体，全都在用。

## 跑起来

```bash
uv sync                    # 装依赖（索引已配成阿里云，见 pyproject.toml）
uv run python -m app.seed --probe   # 建库 + 灌内容 + 实测每个例子能不能跑
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8100
```

打开 <http://127.0.0.1:8100> 。第一次启动时若库里没内容，`lifespan` 会自动灌一次（不含探测）。

- 站点首页：`/`　·　后端 API 文档：`/docs`　·　OpenAPI：`/openapi.json`
- 只想灌内容不探测：`uv run python -m app.seed`
- 只重跑某一章的探测：`uv run python -m app.seed --probe --chapter 7`

## 怎么用

| 动作 | 说明 |
| --- | --- |
| 点代码任意一行 | 右侧显示这一行的解释；没写解释的行会告诉你去哪个文件补 |
| `▶ 运行` | plain 模式：把代码当脚本跑，回收 stdout / stderr / 回溯 / 耗时 / 峰值内存 |
| `⇄ 当服务调用` | asgi 模式：不占端口，用 `httpx.ASGITransport` 在子进程里直接调 app 的端点；请求清单可改 |
| `✎ 改代码` | 改成你自己的版本，跑的是改后的内容；`还原` 回到原文 |
| `标记已学` | 只写 localStorage，不发后端 |
| 顶栏搜索 | 全 12 章搜代码、标题、解释文字、API 卡片名 |
| 键盘 | `1`–`0` 跳章，`[` `]` 上/下一章 |

例子标题旁的标记来自 `--probe` 的实测结论，不是猜的：`实测跑通` / `跑通但无输出` /
`接口有 4xx` / `实测跑不通` / `非 Python`。片段型例子（依赖上文定义的 `router`、`Page`）
本来就跑不通，标出来是为了让你知道**该连着看而不是单看这一块**。

## 目录

```
app/
├── main.py          装配：lifespan 建库/自动播种、中间件、统一异常、静态挂载
├── config.py        Settings（env 前缀 PLW_，或根目录 .env）
├── db.py            engine / SessionLocal / get_session 依赖
├── models.py        Chapter ─< Example ─< LineNote / ApiCard
├── schemas.py       出入参契约（Pydantic v2）
├── sandbox.py       受限子进程执行器（三道闸 + 输出截断）
├── probe.py         识别代码里的接口 + 实测例子可运行性
├── seed.py          seed.json + notes/*.json → SQLite（幂等 upsert）
├── api/content.py   GET /api/meta /chapters /examples /examples/{uid}
├── api/run.py       POST /api/run /api/calls
└── static/          index.html · style.css · app.js
tools/extract_tutorial.py   教程 HTML → data/seed.json（只用标准库）
data/
├── seed.json        抽取产物（提交进仓库，别人 clone 即用）
├── notes/*.json     人工逐行解释 + API 卡片 —— 内容真相源
├── app.db           SQLite（不提交）
└── runs/<uid>/      例子的工作目录（不提交；同一例子的工作目录会复用）
tests/               test_sandbox.py（14）· test_probe.py（9）· test_seed.py（3）· test_api.py（15）
```

## 补内容（这是这个站最有价值的部分）

解释和 API 卡片写成 JSON 放在 `data/notes/`，一个文件可以管几个例子，按章命名即可：

```jsonc
// data/notes/ch07.json
{
  "ch07ex02": {
    "line_notes": [
      { "line": 6, "kind": "key",  "text": "为什么用 select() 而不是 ORM 快捷方式……" },
      { "line": "8", "to": 11, "kind": "warn", "text": "这段整体讲一个坑……" }
    ],
    "api_cards": [
      {
        "name": "session.execute()",
        "signature": "session.execute(select(Task)).scalars().all()",
        "kind": "api",
        "summary": "2.0 的统一入口，传语句对象而不是字符串。",
        "params": [{ "name": "statement", "type": "Select | Insert | text()", "note": "字符串要先包 text()" }],
        "returns": "Result；取标量用 .scalars()",
        "gotcha": "忘了 .scalars() 会拿到 Row 元组，报错信息很迷惑。"
      }
    ]
  }
}
```

- `kind`：`note`（普通解释）/ `key`（关键行，绿点）/ `warn`（易错点，黄点）
- `to`：把 `line` 到 `to` 当成一段来讲（讲多行结构时很有用）
- 改完跑 `uv run python -m app.seed`，刷新页面即生效。重跑不会丢探测结果，也不会产生重复行

想批量看某个例子的原文和 uid：`uv run python -m app.seed --probe --chapter 7` 的结果表，
或者直接 `curl 127.0.0.1:8100/api/examples/ch07ex02 | uv run python -m json.tool`。

## 安全说明（重要）

`POST /api/run` 会在你的机器上执行任意 Python。所以：

- **只监听 `127.0.0.1`**。别用 `--host 0.0.0.0` 把它暴露到局域网，那等于给别人一个你机器上的代码执行入口
- 子进程是**受限**而不是**隔离**：它以你的用户身份运行，能读写你有权访问的文件。防的是误伤，不防恶意
- 三道闸：`RLIMIT_CPU`（死循环）· 父进程 RSS 看门狗（内存）· 墙钟超时 + `killpg` 进程组（卡住）
  外加 `RLIMIT_FSIZE` 限临时目录写文件大小、stdout/stderr 按字节截断
- 实测注记：**macOS 不执行 `RLIMIT_AS`**（一个例子能一路分配到 8GB 而不报错），所以内存只能靠父进程采样；
  这条限制由 `tests/test_sandbox.py::test_memory_hog_hit_by_watchdog` 兜着
- 限额可调：`PLW_EXEC_TIMEOUT=15 PLW_MAX_MEM_MB=2048 uv run uvicorn ...`

## 工程实录（我自己踩过的三个坑）

1. **`preexec_fn` 在线程池宿主里不安全**。FastAPI 的同步依赖跑在线程池，`preexec_fn` 会在 fork 后、
   exec 前动父进程状态。改成「子进程自己读 argv 里的限额，先 `setrlimit` 再 `runpy.run_path`」，
   既安全又能让回溯里的行号和编辑器一一对应。
2. **`-I`（isolated）不是「啥包都看不到」**。它忽略 `PYTHONPATH` 和用户级 site-packages，
   venv 自己的 site-packages 仍在——所以子进程能 `import fastapi`，但学习者 `pip install --user`
   装的东西进不去，环境更可预测。
3. **`dict` 里存 JSON 字符串**（`ApiCard.params`）是为了先跑通再迁移；真要查参数就走 `--probe`
   那套「重新灌库」的路子。教程第 7 章讲的两类错误（字符串当协议 / 提前建一堆表）这里都避开了：
   `params` 只在出参时被 Pydantic 校验器还原成列表。

## 测试

```bash
uv run pytest              # 41 条：沙箱 14 · 识别与判定 9 · 灌库 3 · 接口 15
uv run ruff check app tools tests
```

沙箱那 14 条是这个项目的验收标准：正常代码能跑、回溯能定位到行号、
死循环 / 卡住 / 吃内存 / 刷屏四种情况都被拦住、asgi 模式真能调到端点、非法 uid 不能穿越目录。
