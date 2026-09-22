/* 学习站前端：无框架、无构建、无 CDN。
 *
 * 分工（按使用者的要求定的）：
 *   代码区  = 代码 + 逐行解释（内联在对应行下方，像注释）
 *   右栏    = 系统性内容：这块代码的模块说明、API/概念卡片、实测状态
 *   章头部  = 本章要先搞懂的基础概念（可折叠）
 * 两栏宽度、代码区高度都可拖拽，尺寸记在 localStorage。
 */
"use strict";

const KEY_DONE = "plw:done:v1";
const KEY_LAYOUT = "plw:layout:v1";

const DEFAULTS = { colw: 620, codeh: 460, inline: true };

const state = {
  chapters: [],
  current: null,
  examples: [],
  all: [],
  done: new Set(JSON.parse(localStorage.getItem(KEY_DONE) || "[]")),
  meta: null,
  onlyRunnable: false,
  layout: { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY_LAYOUT) || "{}") },
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const notesFile = (uid) => (uid.match(/^ch\d{2}/) || ["ch00"])[0];
/* 渲染规则见 prose.js：esc 转义、prose 排版整段、proseInline 处理单行 */
const { esc, prose, proseInline } = Prose;

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

const KIND_LABEL = { base: "基础概念", arch: "模块与结构", flow: "执行流程", compare: "对比" };

/* ============================ Python 高亮 ============================ */
/* 手写分词，够覆盖教程例子。跨行三引号用一个状态位带过去，避免把 docstring 染色染乱。 */

const KW = new Set(
  ("async await def class return if elif else for while try except finally with as import from " +
    "pass raise yield lambda global nonlocal assert del break continue match case in is not and or None True False self cls").split(" ")
);
const RE_TOKEN =
  /(f?"[^"\n]*"?|f?'[^'\n]*'?)|(#.*$)|(@\w[\w.]*)|(\b\d[\d_.]*\b)|(\b[A-Za-z_]\w*)|(\s+)|(.)/g;

const span = (cls, text) => `<span class="${cls}">${esc(text)}</span>`;

function highlightLine(raw, st3) {
  const out = [];
  if (st3.delim) {
    const idx = raw.indexOf(st3.delim);
    if (idx === -1) return { html: span("t-str", raw), delim: st3.delim };
    out.push(span("t-str", raw.slice(0, idx + 3)));
    st3.delim = null;
    raw = raw.slice(idx + 3);
  }
  RE_TOKEN.lastIndex = 0;
  let m;
  while ((m = RE_TOKEN.exec(raw)) !== null) {
    const [, str, com, dec, num, word] = m;
    if (str) {
      if (/("""|''')$/.test(str) && !/^("""|''').*\1$/.test(str)) st3.delim = str.slice(-3);
      out.push(span("t-str", str));
    } else if (com) out.push(span("t-com", com));
    else if (dec) out.push(span("t-dec", dec));
    else if (num) out.push(span("t-num", num));
    else if (word) {
      if (KW.has(word)) out.push(span("t-kw", word));
      else if (word === "self" || word === "cls") out.push(span("t-self", word));
      else if (raw[RE_TOKEN.lastIndex] === "(") out.push(span("t-fn", word));
      else if (/^[A-Z]/.test(word)) out.push(span("t-cls", word));
      else out.push(esc(word));
    } else out.push(esc(m[0]));
  }
  return { html: out.join(""), delim: st3.delim };
}

/* ============================ 小段文字排版 ============================ */
/* 规则在 prose.js（**粗体** / `代码` / 列表分组 / 缩进代码 / 全量转义），
   单独成文件是为了能被 node --test 直接加载测到。 */

/* ============================ 启动 ============================ */

async function boot() {
  applyLayout();
  try {
    [state.meta, state.chapters] = await Promise.all([api("/api/meta"), api("/api/chapters")]);
    renderMeta();
    renderChapters();
    bindSizers();
    const fromUrl = Number(new URLSearchParams(location.search).get("ch"));
    const start = Number.isInteger(fromUrl) && state.chapters[fromUrl] ? fromUrl : 0;
    await selectChapter(start);
  } catch (err) {
    $("#content").textContent = "加载失败：" + err.message + "（后端没起来？看 uvicorn 日志）";
  }
}

function renderMeta() {
  const m = state.meta;
  $("#meta").innerHTML =
    `<b>${m.chapters}</b> 章 · <b>${m.examples}</b> 例（能跑 <b>${m.runnable}</b>）· ` +
    `解释 <b>${m.line_notes}</b> 条 · 概念 <b>${m.concepts}</b> 篇 · API 卡片 <b>${m.api_cards}</b> 张 · ` +
    `限额 <b>${m.exec_timeout_ms}ms/${m.max_mem_mb}MB</b>`;
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
      `<span class="name" title="${esc(ch.display_title)}">${esc(ch.short_title)}</span>` +
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
  state.examples = await api(`/api/examples?chapter=${index}&limit=200`);
  renderChapterHead(state.chapters[index]);
  renderExamples(state.examples);
  history.replaceState(null, "", `?ch=${index}`);
}

function renderChapterHead(ch) {
  const head = el("div", "chapter-head");
  const done = state.examples.filter((e) => state.done.has(e.uid)).length;
  const pct = state.examples.length ? Math.round((done / state.examples.length) * 100) : 0;
  head.innerHTML =
    `<h1>${esc(ch.display_title)}</h1>` +
    `<div class="goal">${esc(ch.goal || "")}</div>` +
    `<div class="bar"><i style="width:${pct}%"></i></div>`;

  if (ch.concepts?.length) {
    const wrap = el("div", "concepts");
    wrap.append(el("h2", "concepts-title", "本章要先搞懂的概念"));
    ch.concepts.forEach((c) => wrap.append(renderConcept(c, false)));
    head.append(wrap);
  }
  const old = $("#content .chapter-head");
  if (old) old.replaceWith(head);
  else $("#content").prepend(head);
}

function renderConcept(c, open) {
  const box = el("details", "concept " + (c.kind || "base"));
  box.open = open ?? (c.kind === "base" && false);
  box.innerHTML =
    `<summary><span class="ckind">${esc(KIND_LABEL[c.kind] || "概念")}</span>` +
    `<span class="ctitle">${esc(c.title)}</span></summary>` +
    `<div class="cbody">${prose(c.body)}</div>`;
  return box;
}

/* ============================ 例子卡片 ============================ */

function renderExamples(list) {
  const content = $("#content");
  $("#loading")?.remove();
  content.querySelectorAll(".ex").forEach((n) => n.remove());
  const shown = list.filter((ex) => !state.onlyRunnable || ex.runnable);
  if (!shown.length) {
    content.append(el("p", "loading", "没有符合条件的例子（取消「只看能跑的」试试）。"));
    return;
  }
  shown.forEach((ex) => content.append(renderExample(ex)));
}

function renderExample(ex) {
  const card = el("article", "ex");
  card.dataset.uid = ex.uid;
  const notes = notesByLine(ex.line_notes);

  card.append(renderExHead(ex));
  const cols = el("div", "cols");
  const codeCol = renderCodeCol(ex, notes);
  cols.append(codeCol, el("div", "chresizer", ""), renderNoteCol(ex));
  card.append(cols);
  card.append(el("div", "out hidden"));
  return card;
}

function renderExHead(ex) {
  const head = el("header");
  head.innerHTML =
    `<span class="no">${esc(ex.display_no)}</span>` +
    `<h3>${esc(ex.display_name)}</h3>` +
    (ex.title && ex.caption ? `<span class="file">${esc(ex.caption)}</span>` : "") +
    `<span class="spacer"></span>`;
  head.append(pill(ex.lang, "lang"), pillProbe(ex), pill(`${ex.line_count} 行`, ""));
  const done = el("button", "pill" + (state.done.has(ex.uid) ? " ok" : ""), state.done.has(ex.uid) ? "✓ 已学" : "标记已学");
  done.onclick = () => {
    if (state.done.has(ex.uid)) state.done.delete(ex.uid);
    else state.done.add(ex.uid);
    localStorage.setItem(KEY_DONE, JSON.stringify([...state.done]));
    refreshProgress(ex.uid);
  };
  head.append(done);
  return head;
}

const pill = (text, cls) => el("span", `pill ${cls}`, text);

/* 探测状态 → 徽章。教程是循序渐进的，很多块本来就「接着上文才能跑」，
   这类要标成中性的「片段」，别用红色的「跑不通」打击人。 */
const FRAGMENT = new Set([
  "error:NameError", "error:ImportError", "error:ModuleNotFoundError", "error:no_http_result",
]);
const EXCERPT = new Set(["error:SyntaxError", "error:IndentationError"]);

function pillProbe(ex) {
  const s = ex.probe_status || "";
  if (s === "ok" || s === "asgi_ok") return pill("实测跑通", "ok");
  if (s === "silent") return pill("跑通·无输出", "warn");
  if (s === "asgi_partial") return pill("接口有 4xx", "warn");
  if (s === "skipped" || !ex.runnable) return pill("非 Python", "");
  if (s === "timeout") return pill("跑超时被拦", "bad");
  if (s === "killed") return pill("被限额拦下", "bad");
  if (FRAGMENT.has(s)) return pill("片段：依赖上文", "warn");
  if (EXCERPT.has(s)) return pill("节选：非完整文件", "warn");
  if (s.startsWith("error:")) return pill(`跑到 ${s.slice(6)} 停`, "warn");
  return pill("未探测", "");
}

function refreshProgress(uid) {
  const ex = state.examples.find((e) => e.uid === uid);
  const card = $(`.ex[data-uid="${uid}"]`);
  if (ex && card) card.querySelector("header").replaceWith(renderExHead(ex));
  const ch = state.chapters[state.current];
  if (!ch) return;
  const done = ch.examples.filter((e) => state.done.has(e.uid)).length;
  const btn = [...$("#chapters").children].find((b) => Number(b.dataset.index) === ch.index);
  if (btn) btn.querySelector(".cnt").textContent = `${done}/${ch.examples.length}`;
  renderChapterHead(ch);
}

/* ---- 代码列：解释内联在行下方 ---- */

function notesByLine(notes) {
  /* 返回 {起始行: note}，以及「该行下方该显示哪些注释块」的映射。
     区间注释挂在区间最后一行下方，读者顺着读更自然。 */
  const byStart = new Map();
  const below = new Map();
  for (const it of [...notes].sort((a, b) => a.line_no - b.line_no)) {
    byStart.set(it.line_no, it);
    const anchor = it.to_line > it.line_no ? it.to_line : it.line_no;
    if (!below.has(anchor)) below.set(anchor, []);
    below.get(anchor).push(it);
  }
  return { byStart, below };
}

function renderCodeCol(ex, notes) {
  const col = el("div", "codecol");
  const wrap = el("div", "code");
  const st3 = { delim: null };
  ex.code.split("\n").forEach((raw, i) => {
    const n = i + 1;
    const line = el("div", "ln");
    line.dataset.line = n;
    const start = notes.byStart.get(n);
    if (start) line.classList.add(start.kind);
    const { html } = highlightLine(raw, st3);
    line.innerHTML =
      `<span class="no">${start ? '<i class="mk"></i>' : ""}${n}</span>` +
      `<span class="txt">${html || "&nbsp;"}</span>`;
    line.onclick = () => focusLine(line, n);
    wrap.append(line);
    (notes.below.get(n) || []).forEach((it) => wrap.append(renderInlineNote(it)));
  });
  col.append(wrap, el("div", "vresizer", ""));

  const { bar, editor, reqBox } = renderToolbar(ex, wrap);
  col.append(bar, editor, reqBox);
  return col;
}

function renderInlineNote(it) {
  const box = el("div", `inline-note ${it.kind}`);
  const range = it.to_line > it.line_no ? `${it.line_no}–${it.to_line}` : `${it.line_no}`;
  box.innerHTML =
    `<span class="rng">${range} 行</span>` +
    `<span class="txt">${proseInline(it.text)}</span>`;
  return box;
}

function focusLine(lineEl, n) {
  const card = lineEl.closest(".ex");
  $$(".ln", card).forEach((l) => l.classList.toggle("sel", l === lineEl));
  const note = lineEl.nextElementSibling;
  if (note?.classList.contains("inline-note")) {
    note.classList.remove("flash");
    void note.offsetWidth;  // 重启动画
    note.classList.add("flash");
    note.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function renderToolbar(ex, codeBox) {
  const bar = el("div", "toolbar");
  const editor = el("textarea", "editor hidden");
  editor.value = ex.code;
  editor.spellcheck = false;

  const btnRun = el("button", "primary", "▶ 运行");
  const btnAsgi = el("button", null, "⇄ 当服务调用");
  const btnEdit = el("button", null, "✎ 改代码");
  const btnRevert = el("button", null, "还原");
  const btnInline = el("button", null, state.layout.inline ? "≡ 行内解释 开" : "≡ 行内解释 关");
  btnRevert.classList.add("hidden");
  const hint = el("span", "note", ex.runnable ? "plain 模式：把这段当脚本跑" : "非 Python 块：改完可当 Python 跑");

  const reqBox = el("div", "requests hidden");
  const reqArea = el("textarea");
  reqArea.value = JSON.stringify(ex.calls || [], null, 1);
  reqBox.append(
    el("div", "hint", "请求清单：改这里就能试别的入参；POST/PUT/PATCH 用 body 字段当请求体"),
    reqArea
  );

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
        body: JSON.stringify({ code: currentCode(), mode, requests, example_uid: ex.uid }),
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
    hint.textContent = hidden ? "plain 模式：把这段当脚本跑" : "编辑中，跑的是你改后的版本";
    if (!hidden) {
      editor.style.height = Math.max(160, codeBox.offsetHeight) + "px";
      editor.focus();
    }
  };
  btnRevert.onclick = () => {
    editor.value = ex.code;
    editor.classList.add("hidden");
    btnRevert.classList.add("hidden");
    hint.textContent = "plain 模式：把这段当脚本跑";
  };
  btnInline.onclick = () => {
    state.layout.inline = !state.layout.inline;
    document.body.classList.toggle("no-inline", !state.layout.inline);
    btnInline.textContent = state.layout.inline ? "≡ 行内解释 开" : "≡ 行内解释 关";
    saveLayout();
  };

  bar.append(btnRun, btnAsgi, btnEdit, btnRevert, btnInline, hint);
  return { bar, editor, reqBox };
}

/* ---- 右栏：只放系统性内容 ---- */

function renderNoteCol(ex) {
  const col = el("div", "notecol");

  const concepts = el("div", "block");
  concepts.append(el("h4", null, "这块代码在讲什么 · 模块功能"));
  if (ex.concepts?.length) ex.concepts.forEach((c) => concepts.append(renderConcept(c, true)));
  else concepts.append(el("p", "empty", "（待补）这里放模块级说明：这个文件在工程里管什么、和谁配合。逐行的解释已经内联到左边代码里了。"));
  col.append(concepts);

  const cards = el("div", "block");
  cards.append(el("h4", null, "用到的 API / 概念"));
  if (ex.api_cards?.length) ex.api_cards.forEach((c) => cards.append(renderCard(c)));
  else cards.append(el("p", "empty", "（待补）API 卡片：签名、参数、返回、易错点。"));
  col.append(cards);

  const probe = el("div", "block");
  probe.innerHTML =
    `<h4>实测记录（seed --probe 跑出来的）</h4>` +
    `<div class="probe"><b>${esc(ex.probe_status || "未探测")}</b>　模式 ${esc(ex.probe_mode || "-")}　` +
    `<span>跑不通多半是片段代码依赖上文定义的名字，不是教程写错了</span>` +
    `<pre class="mini">${esc(ex.probe_output || "（无输出）")}</pre></div>`;
  col.append(probe);
  return col;
}

function renderCard(c) {
  const box = el("div", "card");
  box.innerHTML =
    `<span class="kind">${esc(c.kind)}</span>` +
    `<div class="sig">${esc(c.signature || c.name)}</div>` +
    `<div class="sum">${proseInline(c.summary || "")}</div>`;
  if (c.params?.length) {
    const ul = el("ul");
    for (const p of c.params) {
      const li = el("li");
      li.innerHTML = `<b>${esc(p.name)}</b>　${esc(p.type || "")}${p.note ? " — " + proseInline(p.note) : ""}`;
      ul.append(li);
    }
    box.append(ul);
  }
  if (c.returns) {
    const ret = el("div", "ret");
    ret.innerHTML = `<b>返回</b> ${proseInline(c.returns)}`;
    box.append(ret);
  }
  if (c.gotcha) {
    const g = el("div", "gotcha");
    g.innerHTML = `⚠ ${proseInline(c.gotcha)}`;
    box.append(g);
  }
  return box;
}

/* ---- 输出渲染 ---- */

function renderOutput(node, r) {
  node.textContent = "";
  const head = el("div", "headline");
  head.innerHTML =
    `<span class="${r.ok ? "ok" : "bad"}">${r.ok ? "✓ 正常退出" : "✗ 没跑成"}</span>　` +
    `mode=${esc(r.mode)}　exit=${r.exit_code ?? "-"}　${r.duration_ms}ms　` +
    `峰值内存 ${r.peak_rss_mb}MB　工作目录 ${esc(r.workdir || "-")}`;
  node.append(head);
  if (r.hint) node.append(el("div", "hint", r.hint));

  for (const item of r.results || []) {
    const row = el("div", "reqrow");
    const codeV = item.status ?? null;
    const bucket = codeV ? "s" + String(codeV)[0] : "err";
    row.append(el("div", "verb", item.method));
    const right = el("div");
    right.innerHTML =
      `<span class="path">${esc(item.path)}</span>` +
      `<span class="badge-status ${bucket}">${codeV ?? "error"}</span>`;
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
    node.append(el("div", "hint", "没有任何输出。这段代码只是定义，没 print——用「⇄ 当服务调用」看效果。"));
  }
}

/* ============================ 可拖拽布局 ============================ */

function applyLayout() {
  const root = document.documentElement.style;
  root.setProperty("--colw", state.layout.colw + "px");
  root.setProperty("--codeh", state.layout.codeh + "px");
  document.body.classList.toggle("no-inline", !state.layout.inline);
}

function saveLayout() {
  localStorage.setItem(KEY_LAYOUT, JSON.stringify(state.layout));
}

function bindSizers() {
  /* 事件代理：卡片是动态生成的，所以把手挂在 document 上。 */
  let drag = null;
  document.addEventListener("pointerdown", (ev) => {
    const h = ev.target;
    if (h.classList.contains("vresizer")) {
      const box = h.previousElementSibling;  // .code
      drag = { kind: "v", start: ev.clientY, base: box.offsetHeight, box };
      h.setPointerCapture(ev.pointerId);
      document.body.classList.add("resizing");
      ev.preventDefault();
    } else if (h.classList.contains("chresizer")) {
      drag = { kind: "h", start: ev.clientX, base: state.layout.colw };
      h.setPointerCapture(ev.pointerId);
      document.body.classList.add("resizing");
      ev.preventDefault();
    }
  });
  document.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    if (drag.kind === "v") {
      const px = clamp(drag.base + (ev.clientY - drag.start), 140, 2200);
      drag.box.style.height = px + "px";
      drag.live = px;
    } else {
      const px = clamp(drag.base + (ev.clientX - drag.start), 320, 1400);
      document.documentElement.style.setProperty("--colw", px + "px");
      drag.live = px;
    }
  });
  document.addEventListener("pointerup", () => {
    if (!drag) return;
    if (drag.kind === "v") {
      state.layout.codeh = drag.live ?? state.layout.codeh;
      /* 高度改成按 CSS 变量走：写回 root，让之后新建的卡片同高 */
      document.documentElement.style.setProperty("--codeh", state.layout.codeh + "px");
      $$(".code").forEach((b) => (b.style.height = ""));
    } else {
      state.layout.colw = drag.live ?? state.layout.colw;
    }
    saveLayout();
    document.body.classList.remove("resizing");
    drag = null;
  });
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, Math.round(v)));
}

/* ============================ 搜索 / 快捷键 ============================ */

async function ensureAll() {
  if (!state.all.length) state.all = await api("/api/examples?limit=200");
  return state.all;
}

async function runSearch(term) {
  const content = $("#content");
  if (!term.trim()) {
    await selectChapter(state.current);
    return;
  }
  const t = term.trim().toLowerCase();
  const all = await ensureAll();
  const hits = all.filter(
    (ex) =>
      ex.code.toLowerCase().includes(t) ||
      (ex.caption || "").toLowerCase().includes(t) ||
      (ex.title || "").toLowerCase().includes(t) ||
      ex.line_notes.some((it) => it.text.toLowerCase().includes(t)) ||
      ex.api_cards.some((c) => (c.name + c.summary).toLowerCase().includes(t)) ||
      (chOf(ex).short_title || "").toLowerCase().includes(t)
  );
  content.querySelectorAll(".ex").forEach((n) => n.remove());
  const head = el("div", "chapter-head results-head");
  head.innerHTML = `<h1>搜索「${esc(term)}」</h1><div class="goal">全 12 章命中 ${hits.length} 个例子</div>`;
  content.querySelector(".chapter-head")?.replaceWith(head);
  hits.forEach((ex) => content.append(renderExample(ex)));
  if (!hits.length) content.append(el("p", "loading", "没找到。试试 Depends、lifespan、response_model、alembic、JWT。"));
}

const chapterIndexOf = (uid) => Number(uid.slice(2, 4));  // ch07ex02 -> 7
const chOf = (ex) => state.chapters[chapterIndexOf(ex.uid)] || {};

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
  $("#btnResetLayout").onclick = () => {
    state.layout = { ...DEFAULTS };
    saveLayout();
    applyLayout();
  };
  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea")) return;
    if (/^\d$/.test(ev.key) && state.chapters[Number(ev.key)]) selectChapter(Number(ev.key));
    else if (ev.key === "[") selectChapter(Math.max(0, state.current - 1));
    else if (ev.key === "]") selectChapter(Math.min(state.chapters.length - 1, state.current + 1));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindGlobal();
  boot();
});
