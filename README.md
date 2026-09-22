# Python 逐行解释学习站

把 `../FastAPI-learn/fastapi-入门教程.html` 里的 **12 章 / 60 个代码块**抽成结构化数据，
做成一个「点开任意一行代码 → 看逐行解释 → 直接改直接跑」的学习站。

内容完成度：**60 个代码块全部有逐行注释**（330 条）、章级+例级概念 83 篇、API 卡片 55 张。
第 11 章（附录）暂无章级概念，其余 11 章都有。

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

内容的分工是：**逐行的解释内联在代码里（像注释），右栏只放系统性东西**（模块功能、API 卡片、实测记录），章头再放一节「本章要先搞懂的概念」。

| 动作 | 说明 |
| --- | --- |
| 看代码 | 带彩点的行有解释，解释就印在该行下方；`≡ 行内解释` 可整体开关 |
| 点代码任意一行 | 高亮该行并把它的注释闪一下（注释较长时方便定位） |
| `▶ 运行` | plain 模式：把代码当脚本跑，回收 stdout / stderr / 回溯 / 耗时 / 峰值内存 |
| `⇄ 当服务调用` | asgi 模式：不占端口，用 `httpx.ASGITransport` 在子进程里直接调 app 的端点；请求清单可改 |
| `✎ 改代码` | 改成你自己的版本，跑的是改后的内容；`还原` 回到原文 |
| 拖中缝 / 拖代码底部横条 | 调两栏宽度、调代码区高度（上下方向也能调），尺寸记在 localStorage；顶栏「重排面板」回默认 |
| `标记已学` | 只写 localStorage，不发后端 |
| 顶栏搜索 | 全 12 章搜代码、中文标题、文件名、解释文字、概念正文、API 卡片名 |
| 键盘 | `1`–`0` 跳章，`[` `]` 上/下一章 |

标题旁的徽章来自 `--probe` 的实测结论，不是猜的：`实测跑通` / `跑通·无输出` / `接口有 4xx` /
`片段：依赖上文` / `节选：非完整文件` / `跑超时被拦` / `非 Python`。教程是循序渐进搭一个项目的，
60 个块里只有少数能独立运行，标出来是为了告诉你**该连着看，而不是这块写错了**。

## 目录

```
app/
├── main.py          装配：lifespan 建库/自动播种、中间件、统一异常、首页注入资源版本号
├── config.py        Settings（env 前缀 PLW_，或根目录 .env）
├── db.py            engine / SessionLocal / get_session 依赖
├── models.py        Chapter ─< Example ─< LineNote / ApiCard / Concept，Chapter ─< Concept
├── schemas.py       出参契约（含 display_title / display_no / display_name 计算字段）
├── sandbox.py       受限子进程执行器（三道闸 + 输出截断）
├── probe.py         识别代码里的接口 + 实测例子可运行性
├── seed.py          seed.json + notes/*.json → SQLite（幂等 upsert）
├── api/content.py   GET /api/meta /chapters /chapters/{i} /examples /examples/{uid}
├── api/run.py       POST /api/run /api/calls
└── static/          index.html · style.css · prose.js（笔记渲染）· app.js（无构建、无 CDN、无框架）
tools/extract_tutorial.py   教程 HTML → data/seed.json（只用标准库）
data/
├── seed.json        抽取产物（提交进仓库，别人 clone 即用）
├── notes/*.json     中文标题 + 逐行解释 + 概念 + API 卡片 —— 内容真相源
├── app.db           SQLite（不提交）
└── runs/<uid>/      例子的工作目录（不提交；同一例子的工作目录会复用）
tests/               test_sandbox.py（15）· test_probe.py（9）· test_seed.py（7）· test_api.py（20）
                     js/prose.test.mjs（15，`node --test tests/js/prose.test.mjs`）
```

## 补内容（这是这个站最有价值的部分）

内容全在 `data/notes/*.json`，一个文件可以跨章。四样东西：**中文标题 `title`**（列表和卡片上显示它，
`caption` 里的文件名退成副标题）、**逐行解释 `line_notes`**（内联进代码）、**概念 `concepts`**
（右栏/章头的系统性讲解）、**API 卡片 `api_cards`**。

```jsonc
// data/notes/ch07.json
{
  "_chapters": {                       // 章级内容按 slug 挂，一个文件跨章时靠这个区分
    "ch07": {
      "concepts": [
        { "kind": "base", "title": "ORM 到底替你做了什么", "body": "分段写，空行分段；\n    四个空格开头的行会渲染成代码块；`反引号` 变行内代码。" },
        { "kind": "flow", "title": "一次请求怎么走到数据库", "body": "……" }
      ]
    }
  },

  "ch07ex02": {
    "title": "仓储层：只处理数据，不懂 HTTP",
    "concepts": [
      { "kind": "arch", "title": "这个文件在工程里的位置", "body": "repositories/ 只依赖 models，不 import fastapi……" }
    ],
    "line_notes": [
      { "line": 6, "kind": "key",  "text": "为什么用 select() 而不是 ORM 快捷方式……" },
      { "line": 8, "to": 11, "kind": "warn", "text": "这四行整体是一个坑……" }
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

- `kind`（line_notes）：`note` 普通 / `key` 关键行（绿点绿底）/ `warn` 易错点（黄点黄底）
- `kind`（concepts）：`base` 基础概念 / `arch` 模块与结构 / `flow` 执行流程 / `compare` 对比，四种颜色左边框不同
- `to`：一条注释讲 `line` 到 `to` 这一整段，注释挂在这段的最后一行下方
- 改完 `uv run python -m app.seed` 即可（`--probe` 才会重跑可运行性探测）。重跑不产生重复行，也不丢已有探测结果
- 概念正文支持「空行分段 / 四空格缩进当代码块（保留块内缩进）/ 反引号行内代码 / `**加粗**` / 行首 `- ` 与 `1. ` 列表」；裸写的 `__init__`、`__annotations__` 自动带代码样式。规则都在 `app/static/prose.js`——单独成文件是为了能被 `node --test` 加载测到，它也是页面唯一的 HTML 生成点（转义规则都在那里）。
- **故意不支持** `__粗体__` 与 `*斜体*`：内容里大量是 `__init__`、`allow_methods=["*"]`，支持了就会把代码标识符吃掉。`tests/js/prose.test.mjs` 里钉着这两条回归用例

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

## 工程实录（我自己踩过的坑）

1. **`preexec_fn` 在线程池宿主里不安全**。FastAPI 的同步依赖跑在线程池，`preexec_fn` 会在 fork 后、
   exec 前动父进程状态。改成「子进程自己读 argv 里的限额，先 `setrlimit` 再 `runpy.run_path`」，
   既安全又能让回溯里的行号和编辑器一一对应。
2. **`-I`（isolated）不是「啥包都看不到」**。它忽略 `PYTHONPATH` 和用户级 site-packages，
   venv 自己的 site-packages 仍在——所以子进程能 `import fastapi`，但学习者 `pip install --user`
   装的东西进不去，环境更可预测。
3. **关系集合整体赋值会「先 INSERT 后 DELETE」**。`example.line_notes = 新列表` 第二次灌库时撞
   `(example_id, line_no)` 唯一约束直接崩。`_replace_*` 里先赋空、`flush()`、再赋新值。
4. **SQLite WAL 也有配套的删除**。`--clean` 只删 `app.db` 留下 `-wal`/`-shm`，下一个进程开库就
   「no such table」；服务还开着时删文件则直接 `disk I/O error`。现在 `_wipe()` 连旁文件一起删、
   先 `engine.dispose()`，并在提示里写清「先停服务再 clean」。
5. **静态资源必须有版本号**。改了 `style.css` 加新规则，页面里的开关死活不生效：`fetch(..., no-store)`
   拿到新内容，`<link>` 用的却是内存里的旧副本，`Cache-Control: no-cache` 也没救回来。现在 `/` 由路由
   渲染，把 `style.css?v=<mtime+size 哈希>` 注进 HTML——文件一变 URL 就变，物理上用不到旧副本。
6. **`dict` 里存 JSON 字符串**（`ApiCard.params`）是为了先跑通再迁移；教程第 7 章讲的两类错误
   （字符串当协议 / 提前建一堆表）这里都避开了：`params` 只在出参时被 Pydantic 校验器还原成列表。

## 测试

```bash
uv run pytest              # 51 条 Python：沙箱 15 · 识别与判定 9 · 灌库与内容 lint 7 · 接口 20
uv run ruff check app tools tests
node --test tests/js/prose.test.mjs   # 15 条：笔记渲染器（转义、粗体、列表、代码块缩进）
```

内容 lint（`test_notes_markup_is_balanced`）会挡住「反引号/`**` 不配对」这类写坏的字句——
它们不会报错，只会在页面上露出字面标记，靠眼看很难发现。

沙箱那 15 条是执行器的验收标准：正常代码能跑、回溯定位到行号且不掺引导脚本自己的帧、
死循环 / 卡住 / 吃内存 / 刷屏四种情况都被拦住、asgi 模式真能调到端点并跑 lifespan、
非法 uid 不能穿越目录。灌库那 3 条专门钉住上面第 3、4 个坑。
