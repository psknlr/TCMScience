import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseHTML } from "linkedom";
import { renderMarkdown, safeHref } from "../js/core/markdown.js";

const { document } = parseHTML("<!doctype html><html><body></body></html>");

function render(md, opts = {}) {
  const div = document.createElement("div");
  div.appendChild(renderMarkdown(md, { document, ...opts }));
  return div;
}

/** No element or attribute a page could be attacked with, anywhere in the tree. */
function assertInert(root) {
  for (const el of root.querySelectorAll("*")) {
    const tag = el.tagName.toLowerCase();
    assert.ok(!["script", "iframe", "object", "embed", "img", "svg", "style", "form", "input", "link", "meta", "base"].includes(tag), `unexpected <${tag}>`);
    for (const attr of el.attributes) {
      assert.ok(!/^on/i.test(attr.name), `event handler attribute ${attr.name}`);
      if (attr.name === "href") assert.match(attr.value, /^(https?:\/\/|mailto:)/i, `href ${attr.value}`);
      assert.ok(!/javascript:/i.test(attr.value) || attr.name === "data-tex", `javascript: in ${attr.name}`);
    }
  }
}

const XSS = [
  "[click](javascript:alert(1))",
  "[click](JaVaScRiPt:alert(1))",
  "[click]( javascript:alert(1))",
  "[click](java\nscript:alert(1))",
  "[click](jav&#x61;script:alert(1))",
  "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "[click](vbscript:msgbox(1))",
  "[click](//evil.example/x)",
  "[click](/relative/path)",
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "<a href=\"javascript:alert(1)\">x</a>",
  "<iframe src=\"https://evil.example\"></iframe>",
  "![x](javascript:alert(1))",
  "![x](https://evil.example/pixel.png)",
  "<javascript:alert(1)>",
  "[x](https://ok.example/\" onmouseover=\"alert(1))",
  "[x](https://ok.example/ \"title\" onclick=alert(1))",
  "**<b onclick=alert(1)>bold</b>**",
  "| a | b |\n|---|---|\n| <script>x</script> | [y](javascript:alert(1)) |",
  "```html\n<script>alert(1)</script>\n```",
  "`<img src=x onerror=alert(1)>`",
  "[E1](javascript:alert(1))",
  "mailto: [m](mailto:a@b.co?subject=<script>)",
  "<svg onload=alert(1)>",
  "\\<script>alert(1)\\</script>",
];

test("XSS vectors render as inert text: no elements, handlers or script URLs", () => {
  for (const md of XSS) {
    const root = render(md);
    assertInert(root);
  }
});

test("raw HTML stays visible as text", () => {
  const root = render("<script>alert(1)</script> and <b>x</b>");
  assert.equal(root.querySelector("script"), null);
  assert.equal(root.querySelector("b"), null);
  assert.match(root.textContent, /<script>alert\(1\)<\/script> and <b>x<\/b>/);
});

test("allowed links: http(s) and mailto, opened safely; others are plain text", () => {
  const root = render("[Europe PMC](https://europepmc.org/search?query=%E8%91%9B%E6%A0%B9) · [mail](mailto:team@impf.ai) · [bad](javascript:x)");
  const links = [...root.querySelectorAll("a")];
  assert.deepEqual(links.map((a) => a.getAttribute("href")), ["https://europepmc.org/search?query=%E8%91%9B%E6%A0%B9", "mailto:team@impf.ai"]);
  assert.equal(links[0].getAttribute("rel"), "noopener noreferrer nofollow");
  assert.equal(links[0].getAttribute("target"), "_blank");
  assert.match(root.textContent, /bad/);
  assert.equal(safeHref("https://a.example/x y"), null);
  assert.equal(safeHref("HTTPS://A.example"), "https://a.example/");
});

test("bare URLs are linked without trailing punctuation, also next to Chinese text", () => {
  const root = render("见 https://pubmed.ncbi.nlm.nih.gov/12345678/。另见 (https://en.wikipedia.org/wiki/Pueraria_(plant)).");
  const hrefs = [...root.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  assert.deepEqual(hrefs, ["https://pubmed.ncbi.nlm.nih.gov/12345678/", "https://en.wikipedia.org/wiki/Pueraria_(plant)"]);
});

test("images are links, never loaded", () => {
  const root = render("![结构图](https://example.org/a.png)");
  assert.equal(root.querySelector("img"), null);
  assert.equal(root.querySelector("a.md-image-link").textContent, "结构图");
});

test("citation chips: [E1], [E1, E2], 【E3】 through the callback; a default chip without one", () => {
  const seen = [];
  const root = render("甘草与甘遂记载为十八反 [E1]，另见 [E2, E3] 与【E4】。不是引用：[E1](https://x.example)", {
    onCitation: (id) => { seen.push(id); const b = document.createElement("button"); b.setAttribute("data-cite", id); b.textContent = id; return b; },
  });
  assert.deepEqual(seen, ["E1", "E2", "E3", "E4"]);
  assert.equal(root.querySelectorAll("button[data-cite]").length, 4);
  assert.equal(root.querySelectorAll("a").length, 1, "[E1](url) is a link, not a chip");
  const plain = render("见 [E7]");
  assert.equal(plain.querySelector("span.md-cite").getAttribute("data-cite"), "E7");
});

test("headings, lists (nested, ordered, tasks), quotes, rules, paragraphs with line breaks", () => {
  const root = render("# 标题\n\n第一行\n第二行\n\n- 甲\n  - 甲一\n- 乙\n\n3. 三\n4. 四\n\n- [x] 完成\n- [ ] 待办\n\n> 引用 **粗**\n\n---");
  assert.equal(root.querySelector("h2").textContent, "标题");
  assert.equal(root.querySelector("p").querySelectorAll("br").length, 1);
  const ul = root.querySelector("ul");
  assert.equal(ul.children.length, 2);
  assert.equal(ul.querySelector("li ul li").textContent, "甲一");
  assert.equal(root.querySelector("ol").getAttribute("start"), "3");
  assert.equal(root.querySelectorAll("li.md-task").length, 2);
  assert.equal(root.querySelector("li.md-task").getAttribute("data-checked"), "true");
  assert.equal(root.querySelector("blockquote strong").textContent, "粗");
  assert.ok(root.querySelector("hr"));
  assert.equal(render("1、甲\n2、乙").querySelectorAll("ol li").length, 2, "Chinese list numbering");
});

test("tables with alignment, escaped pipes and code spans with pipes", () => {
  const root = render("| 药名 | 剂量 | 说明 |\n|:--|--:|:-:|\n| 甘草 | 6 g | `a|b` 与 \\| 号 |\n| 葛根 | 15 g |");
  const rows = root.querySelectorAll("tbody tr");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].children[2].querySelector("code").textContent, "a|b");
  assert.match(rows[0].children[2].textContent, /\| 号/);
  assert.equal(rows[1].children.length, 3, "short rows are padded");
  assert.equal(root.querySelector("th").getAttribute("style"), "text-align:left");
  assert.equal(root.querySelectorAll("th")[1].getAttribute("style"), "text-align:right");
  assert.ok(root.querySelector("div.md-table[tabindex='0']"));
});

test("code blocks keep their text exactly, with a language label and a copy button", () => {
  const root = render("```python\nprint('<b>')\n  x = 1\n```\n\n~~~\nplain\n~~~");
  const code = root.querySelector("pre code");
  assert.equal(code.textContent, "print('<b>')\n  x = 1");
  assert.equal(code.getAttribute("data-lang"), "python");
  assert.ok(root.querySelector("button[data-copy-code]"));
  assert.equal(root.querySelectorAll("pre").length, 2);
  assert.equal(render("```\nunclosed").querySelector("pre code").textContent, "unclosed", "an unclosed fence (streaming) is still code");
});

test("emphasis is CJK-friendly; snake_case and arithmetic are left alone", () => {
  const root = render("**甘草**的用量 *宜* 小，~~删除~~；变量 tcm_herb_name 与 2 * 3 * 4。");
  assert.equal(root.querySelector("strong").textContent, "甘草");
  assert.equal(root.querySelector("em").textContent, "宜");
  assert.equal(root.querySelector("del").textContent, "删除");
  assert.match(root.textContent, /tcm_herb_name/);
  assert.match(root.textContent, /2 \* 3 \* 4/);
  assert.equal(root.querySelectorAll("em").length, 1);
});

test("math is kept as TeX for the UI to typeset; prices are not math", () => {
  const root = render("效应量 $d = 0.5$，费用 $5 与 $10。\n\n$$\n\\frac{a}{b}\n$$");
  const inline = root.querySelector("span.md-math");
  assert.equal(inline.getAttribute("data-tex"), "d = 0.5");
  assert.equal(root.querySelector("div.md-math-block").getAttribute("data-tex"), "\\frac{a}{b}");
  assert.match(root.textContent, /\$5 与 \$10/);
});

test("<br> inside a table cell becomes a line break", () => {
  const root = render("| a |\n|---|\n| 一<br>二 |");
  assert.equal(root.querySelectorAll("td br").length, 1);
});

test("streaming: a caret follows the last words, also inside a list item", () => {
  assert.ok(render("正在", { streaming: true }).querySelector("p .md-caret"));
  assert.ok(render("- 一\n- 二", { streaming: true }).querySelector("li:last-child .md-caret"));
  assert.ok(render("```\ncode", { streaming: true }).querySelector(":scope > p > .md-caret"), "not inside code");
});

test("empty and odd input does not throw", () => {
  for (const md of ["", null, undefined, "\n\n\n", "|", "| a |\n|", "[", "](", "**", "*", "$", "`", "<", "【E", "- ", "> "]) {
    assertInert(render(md));
  }
});

test("pathological input renders in linear time", () => {
  const inputs = ["*a ".repeat(20000), "**a ".repeat(15000), "`".repeat(30000), "[".repeat(30000), "[a](".repeat(10000), "_x ".repeat(20000), "~~a ".repeat(15000), "$a ".repeat(20000),
    ">".repeat(20000) + " deep", Array.from({ length: 300 }, (_, k) => `${" ".repeat(k * 2)}- level ${k}`).join("\n"), Array.from({ length: 300 }, (_, k) => `${">".repeat(k)} q`).join("\n")];
  for (const md of inputs) {
    const t0 = performance.now();
    render(md);
    const ms = performance.now() - t0;
    assert.ok(ms < 1500, `${JSON.stringify(md.slice(0, 8))}… took ${ms.toFixed(0)} ms`);
  }
});
