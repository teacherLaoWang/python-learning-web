/* 学习站前端：无框架、无构建、无 CDN。
 *
 * 数据流：/api/chapters → 侧栏；点章 → /api/examples?chapter=n → 卡片；
 * 点代码行 → 本地查 line_notes；运行 → POST /api/run → 渲染 stdout/stderr/hint 或接口结果。
 * 「已学」标记只存 localStorage，不发后端。
 */
"use strict";

const KEY_DONE = "plw:done:v1";

const state = {
  chapters: [],        // ChapterOut[]
  current: null,       // 当前章节 index
  examples: [],        // 当前章节的 ExampleOut[]
  all: [],             // 全量例子缓存（搜索用）
  done: new Set(JSON.parse(localStorage.getItem(KEY_DONE) || "[]")),
  meta: null,
  search: "",
  onlyRunnable: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
/* uid 形如 ch07ex02，它所属的解释文件是 data/notes/ch07.json */
const notesFile = (uid) => (uid.match(/^ch\d{2}/) || ["ch00"])[0];
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const api = async (path, init) => {
  const resp = await fetch(path, init);
  const data = await resp.json().catch(() => ({ detail: "响应不是 JSON" }));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
  return data;
};

/* ============================ Python 高亮 ============================ */
/* 手写的轻量分词：够覆盖教程里的例子，不追求 CPython 语法完备。
   跨行的三引号字符串用一个状态位带过去，避免把 docstring 染成彩色乱码。 */

const KW = new Set(
  ("async await def class return if elif else for while try except finally with as import from " +
    "pass raise yield lambda global nonlocal assert del break continue match case in is not and or None True False self cls").split(" ")
);
const RE_TOKEN =
  /(f?"[^"\n]*"?|f?'[^'\n]*'?)|(#.*$)|(@\w[\w.]*)|(\b\d[\d_.]*\b)|(\b[A-Za-z_一-龥][\w]*)|(\s+)|(.)/g;

function highlightLine(raw, state3) {
  // state3: 当前是否在多行字符串里，值是定界符（'"""' / "'''"）或 null
  const out = [];
  if (state3.delim) {
    const idx = raw.indexOf(state3.delim);
    if (idx === -1) {
      out.push(span("t-str", raw));
      return { html: out.join(""), delim: state3.delim };
    }
    out.push(span("t-str", raw.slice(0, idx + 3)));
    state3.delim = null;
    raw = raw.slice(idx + 3);
  }
  let rest = raw;
  RE_TOKEN.lastIndex = 0;
  let m;
  while ((m = RE_TOKEN.exec(rest)) !== null) {
    const [tok, str, com, dec, num, word, ws] = m;
    if (str) {
      if (/("""|''')$/.test(str) && !/^("""|''').*\1$/.test(str)) {
        state3.delim = str.slice(-3);
      }
      out.push(span("t-str", tok));
    } else if (com) out.push(span("t-com", tok));
    else if (dec) out.push(span("t-dec", tok));
    else if (num) out.push(span("t-num", tok));
    else if (word) {
      if (KW.has(word)) out.push(span("t-kw", tok));
      else if (word === "self" || word === "cls") out.push(span("t-self", tok));
      else if (rest[RE_TOKEN.lastIndex] === "(") out.push(span("t-fn", tok));
      else if (/^[A-Z]/.test(word)) out.push(span("t-cls", tok));
      else out.push(esc(word));
    } else out.push(esc(tok));
  }
  return { html: out.join(""), delim: state3.delim };
}

const span = (cls, text) => `<span class="${cls}">${esc(text)}</span>`;
const esc = (text) =>
  String(text).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

/* ============================ 侧栏 / 章节 ============================ */

async function boot() {
  try {
    [state.meta, state.chapters] = await Promise.all([api("/api/meta"), api("/api/chapters")]);
    renderMeta();
    renderChapters();
    const start = Number(new URLSearchParams(location.search).get("ch"));
    await selectChapter(Number.isInteger(start) && state.chapters[start] ? start : 0);
  } catch (err) {
    $("#content").textContent = "加载失败：" + err.message;
    $("#meta").textContent = "后端没起来？看 uvicorn 日志";
  }
}

function renderMeta() {
  const m = state.meta;
  $("#meta").innerHTML =
    `<b>${m.chapters}</b> 章 · <b>${m.examples}</b> 例（能跑 <b>${m.runnable}</b>）· ` +
    `逐行解释 <b>${m.line_notes}</b> 条 · API 卡片 <b>${m.api_cards}</b> 张 · ` +
    `超时 <b>${m.exec_timeout_ms}ms</b> / 内存 <b>${m.max_mem_mb}MB</b>`;
}

function renderChapters() {
  const nav = $("#chapters");
  nav.textContent = "";
  for (const ch of state.chapters) {
    const btn = el("button");
    btn.dataset.index = ch.index;
    const done = ch.examples.filter((e) => state.done.has(e.uid)).length;
    btn.innerHTML =
      `<span class="num">${ch.index}</span>` +
      `<span class="name">${esc(ch.title.replace(/^\d+\s*/, ""))}</span>` +
      `<span class="cnt">${done}/${ch.examples.length}</span>`;
    btn.onclick = () => selectChapter(ch.index);
    nav.append(btn);
  }
  markActiveChapter();
}

function markActiveChapter() {
  for (const btn of $("#chapters").children) {
    btn.classList.toggle("on", Number(btn.dataset.index) === state.current);
  }
}

async function selectChapter(index) {
  state.current = index;
  markActiveChapter();
  $("#search").value = "";
  state.search = "";
  const ch = state.chapters[index];
  state.examples = await api(`/api/examples?chapter=${index}&limit=200`);
  renderChapterHead(ch);
  renderExamples(state.examples);
}

function renderChapterHead(ch) {
  const head = el("div", "chapter-head");
  const done = state.examples.filter((e) => state.done.has(e.uid)).length;
  const pct = state.examples.length ? Math.round((done / state.examples.length) * 100) : 0;
  head.innerHTML =
    `<h1>${esc(ch.title)}</h1><div class="goal">${esc(ch.goal || "")}</div>` +
    `<div class="bar"><i style="width:${pct}%"></i></div>`;
  const old = $("#content .chapter-head, #content .results-head");
  if (old) old.replaceWith(head);
  else $("#content").prepend(head);
}

/* ============================ 例子卡片 ============================ */

function renderExamples(list, headerText) {
  const content = $("#content");
  $("#loading")?.remove();  // 首屏占位，渲染过例子就没必要留着
  content.querySelectorAll(".ex").forEach((n) => n.remove());
  if (headerText) {
    const h = el("div", "chapter-head results-head");
    h.innerHTML = `<h1>${esc(headerText)}</h1>`;
    content.querySelectorAll(".chapter-head")[0]?.after(h);
  }
  const shown = list.filter((ex) => !state.onlyRunnable || ex.runnable);
  if (!shown.length) content.append(el("p", "loading", "这一章没有符合条件的例子。"));
  shown.forEach((ex) => content.append(renderExample(ex)));
}

function renderExample(ex) {
  const card = el("article", "ex");
  card.dataset.uid = ex.uid;
  const notesByLine = indexNotes(ex.line_notes);

  card.append(renderExHead(ex));
  const cols = el("div", "cols");
  const codeCol = renderCodeCol(ex, notesByLine);
  mountExtras(codeCol);
  cols.append(codeCol, renderNoteCol(ex, notesByLine));
  card.append(cols);
  const out = el("div", "out hidden");
  card.append(out);
  return card;
}

function renderExHead(ex) {
  const head = el("header");
  head.append(el("span", "uid", ex.uid));
  head.append(el("h3", null, ex.caption || ex.heading || `例子 ${ex.order}`));
  head.append(el("span", "spacer"));
  head.append(pill(ex.lang, "lang"));
  if (ex.probe_status) head.append(pillProbe(ex));
  head.append(pill(`${ex.line_count} 行`, ""));
  const done = el("button", "pill" + (state.done.has(ex.uid) ? " ok" : ""), state.done.has(ex.uid) ? "✓ 已学" : "标记已学");
  done.onclick = () => {
    if (state.done.has(ex.uid)) state.done.delete(ex.uid);
    else state.done.add(ex.uid);
    localStorage.setItem(KEY_DONE, JSON.stringify([...state.done]));
    card_done_refresh(ex.uid);
  };
  head.append(done);
  return head;
}

const pill = (text, cls) => el("span", `pill ${cls}`, text);

function pillProbe(ex) {
  const s = ex.probe_status || "";
  if (s === "ok" || s === "asgi_ok") return pill("实测跑通", "ok");
  if (s === "silent") return pill("跑通但无输出", "warn");
  if (s === "asgi_partial") return pill("接口有 4xx", "warn");
  if (s === "skipped") return pill("非 Python", "");
  return pill("实测跑不通", "bad");
}

function card_done_refresh(uid) {
  const ex = state.examples.find((e) => e.uid === uid) || state.all.find((e) => e.uid === uid);
  const card = $(`.ex[data-uid="${uid}"]`);
  if (ex && card) {
    card.querySelector("header").replaceWith(renderExHead(ex));
  }
  const ch = state.chapters[state.current];
  if (ch) {
    const done = ch.examples.filter((e) => state.done.has(e.uid)).length;
    const btn = [...$("#chapters").children].find((b) => Number(b.dataset.index) === ch.index);
    if (btn) btn.querySelector(".cnt").textContent = `${done}/${ch.examples.length}`;
    renderChapterHead(ch);
  }
}

/* ---- 代码列 ---- */

function renderCodeCol(ex, notesByLine) {
  const col = el("div", "codecol");
  const wrap = el("div", "code");
  wrap.setAttribute("role", "list");
  const st3 = { delim: null };
  ex.code.split("\n").forEach((raw, i) => {
    const n = i + 1;
    const line = el("div", "ln");
    line.dataset.line = n;
    const note = notesByLine.get(n);
    const { html } = highlightLine(raw, st3);
    line.innerHTML =
      `<span class="no">${note ? '<i class="mk"></i>' : ""}${n}</span>` +
      `<span class="txt">${html || "&nbsp;"}</span>`;
    if (note) line.classList.add(note.kind);
    line.onclick = () => selectLine(line.closest(".ex"), n);
    wrap.append(line);
  });
  col.append(wrap);
  col.append(renderToolbar(ex));
  return col;
}

function renderToolbar(ex) {
  const bar = el("div", "toolbar");
  const editor = el("textarea", "editor hidden");
  editor.value = ex.code;
  editor.spellcheck = false;

  const btnRun = el("button", "primary", "▶ 运行");
  const btnAsgi = el("button", null, "⇄ 当服务调用");
  const btnEdit = el("button", null, "✎ 改代码");
  const btnRevert = el("button", null, "还原");
  btnRevert.classList.add("hidden");
  const hint = el("span", "note", ex.runnable ? "跑的是这段代码本身" : "非 Python 块：改代码后可当 Python 跑");

  const reqBox = el("div", "requests hidden");
  const reqArea = el("textarea");
  reqArea.value = JSON.stringify(ex.calls || [], null, 1);
  reqBox.append(el("div", "hint", "请求清单（改这里就能试别的入参；POST/PUT/PATCH 用 body 字段当请求体）"), reqArea);

  const currentCode = () => (editor.classList.contains("hidden") ? ex.code : editor.value);

  const run = async (mode) => {
    const out = bar.closest(".ex").querySelector(".out");
    out.classList.remove("hidden");
    out.textContent = "执行中…";
    [btnRun, btnAsgi].forEach((b) => (b.disabled = true));
    let requests = [];
    if (mode === "asgi") {
      try {
        requests = JSON.parse(reqArea.value || "[]");
      } catch (err) {
        out.innerHTML = `<div class="hint">请求清单不是合法 JSON：${esc(err.message)}</div>`;
        [btnRun, btnAsgi].forEach((b) => (b.disabled = false));
        return;
      }
    }
    try {
      const r = await api("/api/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          code: currentCode(),
          mode,
          requests,
          example_uid: ex.uid,
        }),
      });
      renderOutput(out, r);
    } catch (err) {
      out.innerHTML = `<div class="hint">请求失败：${esc(err.message)}</div>`;
    } finally {
      [btnRun, btnAsgi].forEach((b) => (b.disabled = false));
    }
  };

  btnRun.onclick = () => run("plain");
  btnAsgi.onclick = () => {
    reqBox.classList.toggle("hidden");
    if (!reqBox.classList.contains("hidden")) run("asgi");
  };
  btnEdit.onclick = () => {
    const hidden = editor.classList.toggle("hidden");
    btnRevert.classList.toggle("hidden", hidden);
    hint.textContent = hidden ? "跑的是这段代码本身" : "编辑中，跑的是你改后的版本";
    if (!hidden) editor.focus();
  };
  btnRevert.onclick = () => {
    editor.value = ex.code;
    editor.classList.add("hidden");
    btnRevert.classList.add("hidden");
    hint.textContent = "跑的是这段代码本身";
  };

  bar.append(btnRun, btnAsgi, btnEdit, btnRevert, hint);
  return Object.assign(bar, { _editor: editor, _reqBox: reqBox });
}

/* toolbar 生成的 editor / requests 得挂到 codecol 上（放在 toolbar 里会被 flex 挤坏） */
function mountExtras(codecol) {
  const bar = codecol.querySelector(".toolbar");
  if (!bar?._editor || bar.dataset.mounted) return;
  bar.dataset.mounted = "1";
  codecol.append(bar._editor, bar._reqBox);
}

function selectLine(card, n) {
  card.querySelectorAll(".code .ln").forEach((l) => l.classList.toggle("sel", Number(l.dataset.line) === n));
  const view = card.querySelector(".lineview");
  const ex = state.examples.find((e) => e.uid === card.dataset.uid) ||
    state.all.find((e) => e.uid === card.dataset.uid);
  if (!ex || !view) return;
  const code = (ex.code.split("\n")[n - 1] || "").trim();
  view.textContent = "";
  view.append(el("div", "src", `第 ${n} 行　${code || "(空行)"}`));
  const notes = ex.line_notes.filter((it) => n >= it.line_no && (it.to_line || it.line_no) >= n);
  if (!notes.length) {
    const empty = el("p", "note-empty");
    empty.innerHTML =
      `这一行还没有写解释。补一句：在 <code>data/notes/${notesFile(ex.uid)}.json</code> 里给 ` +
      `<code>${ex.uid}</code> 的 <code>line_notes</code> 加 <code>{"line": ${n}, "text": "..."}</code>，` +
      `然后重跑 <code>uv run python -m app.seed</code>。`;
    view.append(empty);
    return;
  }
  notes.forEach((it) => view.append(renderNote(it)));
}

function renderNote(it) {
  const box = el("div", `note-item ${it.kind}`);
  const range = it.to_line && it.to_line > it.line_no ? `${it.line_no}–${it.to_line} 行` : `${it.line_no} 行`;
  box.innerHTML =
    `<span class="tag">${{ key: "关键", warn: "易错", note: "解释" }[it.kind] || "解释"}</span>` +
    `<span class="range">${range}</span>`;
  box.append(el("p", null, it.text));
  return box;
}

function indexNotes(notes) {
  const map = new Map();
  for (const it of [...notes].sort((a, b) => b.line_no - a.line_no)) {
    const to = it.to_line && it.to_line > it.line_no ? it.to_line : it.line_no;
    for (let n = it.line_no; n <= to; n += 1) {
      const prev = map.get(n);
      if (!prev || rank(it.kind) > rank(prev.kind)) map.set(n, it);
    }
  }
  return map;
}
const rank = (kind) => ({ warn: 3, key: 2, note: 1 }[kind] || 0);

/* ---- 解释列 ---- */

function renderNoteCol(ex, notesByLine) {
  const col = el("div", "notecol");
  const view = el("div", "lineview");
  const first = [...notesByLine.keys()].sort((a, b) => a - b)[0];
  if (first) {
    view.append(el("div", "prompt", `已写 ${ex.line_notes.length} 条解释，覆盖 ${notesByLine.size} 行。点左侧任意一行看对应说明。`));
  } else {
    const p = el("div", "prompt");
    p.innerHTML =
      `这个例子还没写逐行解释。想补：编辑 <code>data/notes/${notesFile(ex.uid)}.json</code> → ` +
      `重跑 <code>uv run python -m app.seed</code> → 刷新本页。`;
    view.append(p);
  }
  col.append(view);

  const cards = el("div", "cards");
  cards.append(el("h4", null, "这段代码用到的 API / 概念"));
  if (!ex.api_cards.length) {
    cards.append(el("div", "todo", "（空）待补 API 卡片 —— 目前先看代码块右侧的原文说明"));
  }
  ex.api_cards.forEach((c) => cards.append(renderCard(c)));
  col.append(cards);
  return col;
}

function renderCard(c) {
  const box = el("div", "card");
  box.innerHTML =
    `<span class="kind">${esc(c.kind)}</span>` +
    `<div class="sig">${esc(c.signature || c.name)}</div>` +
    `<div class="sum">${esc(c.summary)}</div>`;
  if (c.params?.length) {
    const ul = el("ul");
    for (const p of c.params) {
      const li = el("li");
      li.innerHTML = `<b>${esc(p.name)}</b>　${esc(p.type || "")}${p.note ? " — " + esc(p.note) : ""}`;
      ul.append(li);
    }
    box.append(ul);
  }
  if (c.returns) {
    const ret = el("div", "ret");
    ret.innerHTML = `<b>返回</b> ${esc(c.returns)}`;
    box.append(ret);
  }
  if (c.gotcha) box.append(el("div", "gotcha", "⚠ " + c.gotcha));
  return box;
}

/* ---- 输出渲染 ---- */

function renderOutput(node, r) {
  node.textContent = "";
  const head = el("div", "headline");
  const cls = r.ok ? "ok" : "bad";
  head.innerHTML =
    `<span class="${cls}">${r.ok ? "✓ 正常退出" : "✗ 没跑成"}</span>　` +
    `mode=${esc(r.mode)}　exit=${r.exit_code ?? "-"}　${r.duration_ms}ms　` +
    `峰值内存 ${r.peak_rss_mb}MB　工作目录 ${esc(r.workdir || "-")}`;
  node.append(head);
  if (r.hint) node.append(el("div", "hint", r.hint));

  for (const item of r.results || []) {
    const row = el("div", "reqrow");
    const code = item.status ?? null;
    const bucket = code ? "s" + String(code)[0] : "err";
    row.append(el("div", "verb", item.method));
    const right = el("div");
    right.innerHTML =
      `<span class="path">${esc(item.path)}</span>` +
      `<span class="badge-status ${bucket}">${code ?? "error"}</span>`;
    const payload = item.error ? { error: item.error } : item.body;
    const pre = el("pre");
    pre.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 1);
    right.append(pre);
    row.append(right);
    node.append(row);
  }

  if (r.stdout) {
    const pre = el("pre");
    pre.textContent = r.stdout;
    node.append(pre);
  }
  if (r.stderr) {
    const pre = el("pre", "err");
    pre.textContent = r.stderr;
    node.append(pre);
  }
  if (!r.stdout && !r.stderr && !(r.results || []).length) {
    node.append(el("div", "hint", "没有任何输出。Python 例子常常是这样：代码只是定义，没 print。"));
  }
}

/* ============================ 搜索 / 快捷键 ============================ */

async function ensureAll() {
  if (!state.all.length) state.all = await api("/api/examples?limit=200");
  return state.all;
}

async function runSearch(term) {
  state.search = term.trim().toLowerCase();
  if (!state.search) {
    await selectChapter(state.current);
    return;
  }
  const all = await ensureAll();
  const hits = all.filter(
    (ex) =>
      ex.code.toLowerCase().includes(state.search) ||
      (ex.caption || "").toLowerCase().includes(state.search) ||
      ex.line_notes.some((it) => it.text.toLowerCase().includes(state.search)) ||
      ex.api_cards.some((c) => (c.name + c.summary).toLowerCase().includes(state.search))
  );
  const content = $("#content");
  content.querySelectorAll(".ex").forEach((n) => n.remove());
  const head = el("div", "chapter-head results-head");
  head.innerHTML = `<h1>搜索「${esc(term)}」</h1><div class="goal">命中 ${hits.length} 个例子（全 12 章）</div>`;
  content.querySelector(".chapter-head")?.replaceWith(head);
  hits.forEach((ex) => content.append(renderExample(ex)));
  if (!hits.length) content.append(el("p", "loading", "没找到。试试 Depends、lifespan、response_model、alembic、JWT。"));
}

function bindGlobal() {
  let timer = null;
  $("#search").addEventListener("input", (ev) => {
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(ev.target.value), 220);
  });
  $("#onlyRunnable").addEventListener("change", (ev) => {
    state.onlyRunnable = ev.target.checked;
    renderExamples(state.examples);
  });
  $("#btnClearProgress").onclick = () => {
    localStorage.removeItem(KEY_DONE);
    state.done = new Set();
    renderChapters();
    renderExamples(state.examples);
  };
  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea")) return;
    const n = Number(ev.key);
    if (/^\d$/.test(ev.key) && state.chapters[n]) {
      selectChapter(n);
    } else if (ev.key === "[") {
      selectChapter(Math.max(0, state.current - 1));
    } else if (ev.key === "]") {
      selectChapter(Math.min(state.chapters.length - 1, state.current + 1));
    }
  });
}

/* ============================ 启动 ============================ */

document.addEventListener("DOMContentLoaded", () => {
  bindGlobal();
  boot();
});
