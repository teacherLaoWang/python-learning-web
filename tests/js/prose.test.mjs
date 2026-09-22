/* 渲染器的回归测试：node --test tests/js/

   这些用例的来源都是真实踩过的点：正文里 90 处 **粗体** 原样显示、
   `__init__` 差点被当成 markdown 粗体吃掉、"2.0 的统一入口" 差点被当成有序列表。
*/
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { prose, proseInline } = require("../../app/static/prose.js");

test("粗体与行内代码都被渲染，不留原始标记", () => {
  const html = proseInline("解释器不看注解，**工具看**：存在 `__annotations__` 里");
  assert.match(html, /<strong>工具看<\/strong>/);
  assert.match(html, /<code>__annotations__<\/code>/);
  assert.doesNotMatch(html, /\*\*|`/);
});

test("裸双下划线名自动加代码样式", () => {
  assert.match(proseInline("被装饰函数的 __name__ 会变成 wrapper"), /<code>__name__<\/code>/);
});

test("已经在反引号里的标识符不会被二次包裹", () => {
  const html = proseInline("`__init__.py` 是包标记");
  assert.equal((html.match(/<code>/g) || []).length, 1, html);
  assert.doesNotMatch(html, /<code><code>/);
});

test("引号里的 * 号不会被当成斜体", () => {
  const html = proseInline('`allow_methods=["*"]` 生产建议列举');
  assert.doesNotMatch(html, /<em>|<i>|<strong>/);
  assert.match(html, /<code>allow_methods=\[&quot;\*&quot;\]<\/code>/);
});

test("HTML 一律转义，笔记内容不能变成标签", () => {
  const html = proseInline("<script>alert(1)</script> 与 a<b 的比较");
  assert.doesNotMatch(html, /<script/);
  assert.match(html, /&lt;script&gt;/);
});

test("段落 + 列表混排会分组", () => {
  const html = prose("三条硬规则：\n\n1. **services 不 import fastapi**\n2. **repositories 只认识 ORM**");
  assert.match(html, /^<p>三条硬规则：<\/p>/);
  assert.match(html, /<ol class="mini"><li><strong>services 不 import fastapi<\/strong><\/li>/);
  assert.equal((html.match(/<li>/g) || []).length, 2);
});

test("项目符号列表", () => {
  const html = prose("- 甲\n- 乙");
  assert.match(html, /^<ul class="mini"><li>甲<\/li><li>乙<\/li><\/ul>$/);
});

test("版本号开头不是有序列表", () => {
  const html = proseInline("2.0 的统一入口，传语句对象");
  assert.doesNotMatch(html, /<ol|<li/);
});

test("四空格缩进行变成代码块", () => {
  const html = prose("包一层才能跑：\n\n    async def main():\n        return await fetch()");
  assert.match(html, /^<p>包一层才能跑：<\/p><pre class="mini">/);
  assert.match(html, /async def main\(\):\n {4}return await fetch\(\)/);
});

test("代码块里的尖括号仍然转义", () => {
  const html = prose("    if a < b:\n        pass");
  assert.match(html, /if a &lt; b:/);
});

test("代码块保留内部缩进（Python 的缩进是语义）", () => {
  const html = prose("例子：\n\n    async def main():\n        return await fetch()");
  assert.match(html, /<pre class="mini">async def main\(\):\n {4}return await fetch\(\)<\/pre>/);
});

test("proseInline 不产生块级标签", () => {
  assert.doesNotMatch(proseInline("一行说明，带 **强调**"), /<p>|<ul>|<pre>/);
});

test("代码片段里的 ** 不会被当成粗体", () => {
  const html = proseInline("调用写法 `bg.add_task(fn, *args, **kwargs)` 不是结果");
  assert.doesNotMatch(html, /<strong>/);
  assert.match(html, /<code>bg\.add_task\(fn, \*args, \*\*kwargs\)<\/code>/);
});

test("未闭合的 *args/**kwargs 保持原样，不误生成标签", () => {
  const html = proseInline("用 `*args` / `**kwargs` 转发");
  assert.doesNotMatch(html, /<strong>/);
  assert.equal((html.match(/<code>/g) || []).length, 2);
});

test("空值安全", () => {
  assert.equal(prose(""), "");
  assert.equal(prose(undefined), "");
  assert.equal(proseInline(null), "");
});
