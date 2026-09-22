/* 极简 markdown 渲染（笔记正文用）。浏览器和 Node 都能加载，Node 侧给 tests/js 用。

   只支持这些，而且是刻意收窄的：
     - **粗体**
     - `代码`
     - 行首 `- ` / `1. ` 列表（可与段落混排，会分组）
     - 四个空格缩进的行 → 代码块
     - 裸的 __init__ / __name__ 这类双下划线名字自动加代码样式
   不支持 __粗体__ 和 *斜体*：内容里大量出现的是 `__init__`、`allow_methods=["*"]`，
   支持它们会把代码标识符吃掉（见 tests/js/prose.test.mjs 里那两条回归用例）。
   其余一律转义成纯文本，所以笔记里写 <script> 也不会变成 HTML。
*/
(function (root, factory) {
  const api = factory();
  root.Prose = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

  function esc(text) {
    return String(text ?? "").replace(/[&<>"]/g, (c) => ESCAPES[c]);
  }

  function inline(text) {
    let out = esc(text);
    // 先把 `代码` 挖出来占位：否则 "**kwargs" 这类内容会被粗体规则啃掉，
    // 代码片段里的 ** 也会莫名其妙变成 <strong>。
    const spans = [];
    out = out.replace(/`([^`\n]+)`/g, (_m, codeText) => {
      spans.push(codeText);
      return `\u0001${spans.length - 1}\u0001`;
    });
    out = out.replace(/\*\*([^*\n]+)\*\*/g, (_m, bold) => `<strong>${bold}</strong>`);
    // 裸双下划线名（add.__annotations__、__init__）：没写反引号时补上代码样式。
    out = out.replace(
      /(^|[^-\w`>&\u0001])(__-[a-z]+__|__[a-z][\w]*__)(?![\w`<])/gi,
      (_m, before, name) => `${before}<code>${name}</code>`
    );
    out = out.replace(/\u0001(\d+)\u0001/g, (_m, i) => `<code>${spans[Number(i)]}</code>`);
    return out;
  }

  const RE_CODE_LINE = /^\s{4,}\S/;
  const RE_BULLET = /^\s*[-•]\s+(.*)$/;
  const RE_NUM = /^\s*(\d+)\.\s+(.*)$/;

  function blockToHtml(block) {
    const lines = block.split("\n");
    const out = [];
    let para = [];
    const flush = () => {
      if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    };

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];

      if (RE_CODE_LINE.test(line)) {
        flush();
        const buf = [];
        while (i < lines.length && RE_CODE_LINE.test(lines[i])) {
          buf.push(lines[i]);
          i += 1;
        }
        i -= 1;
        // 按最小公共缩进 dedent：Python 代码块的缩进本身是语义，不能逐行 trim
        const min = Math.min(...buf.map((l) => l.match(/^\s*/)[0].length));
        const body = buf.map((l) => l.slice(min)).join("\n");
        out.push(`<pre class="mini">${esc(body)}</pre>`);
        continue;
      }

      const num = line.match(RE_NUM);
      const bullet = line.match(RE_BULLET);
      // "2.0 的统一入口" 不算列表：RE_NUM 要求点号后面有空白
      if (num || bullet) {
        flush();
        const tag = num ? "ol" : "ul";
        const items = [];
        while (i < lines.length) {
          const m = num ? lines[i].match(RE_NUM) : lines[i].match(RE_BULLET);
          if (!m) break;
          items.push(`<li>${inline(num ? m[2] : m[1])}</li>`);
          i += 1;
        }
        i -= 1;
        out.push(`<${tag} class="mini">${items.join("")}</${tag}>`);
        continue;
      }

      if (!line.trim()) continue;
      para.push(line.trim());
    }
    flush();
    return out.join("");
  }

  /** 概念正文：按空行分段，段内再识别列表与缩进代码。 */
  function prose(text) {
    return String(text || "")
      .replace(/\r/g, "")
      .split(/\n{2,}/)
      .map((b) => b.replace(/\s+$/, ""))
      .filter(Boolean)
      .map(blockToHtml)
      .join("");
  }

  /** 单行场景（逐行注释、API 卡片的 summary/returns/gotcha）：只走行内规则。 */
  function proseInline(text) {
    return inline(text);
  }

  return { esc, inline, prose, proseInline };
});
