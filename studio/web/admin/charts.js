// The dashboard's charts, drawn by hand as SVG: the page runs its own scripts only (no chart library). Colour comes from
// the stylesheet's roles — categorical slots series-1…3 in a fixed order, and the status scale — validated as a set for
// both modes. A chart with two series or more has a legend, and direct labels at the line ends where there is room;
// every chart has a tooltip that follows the pointer or the arrow keys, and a table view. Values go in as text, never
// as markup, and nothing sets an inline style attribute (the policy forbids it; CSSOM properties are used instead).

import { t } from "./i18n.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** An HTML element. props: {class, text, on<event>: handler, …attributes}; children: nodes or text. */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false || k === "style") continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = String(v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function svg(tag, attrs = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  if (text !== null) node.textContent = String(text);
  return node;
}

// ------------------------------------------------------------------------------------------------- redrawing on resize

let observer = null;
const draws = new WeakMap();

function watch(node, draw) {
  draws.set(node, draw);
  if (typeof ResizeObserver !== "function") {
    requestAnimationFrame(() => draw(node.clientWidth || 640));
    return;
  }
  observer ||= new ResizeObserver((entries) => {
    for (const e of entries) {
      const fn = draws.get(e.target);
      const w = Math.round(e.contentRect.width);
      if (!fn || !w || w === fn.width) continue;
      fn.width = w;
      fn(w);
    }
  });
  observer.observe(node);
}

/** Stop redrawing the charts of a view that has been replaced. */
export function resetCharts() {
  observer?.disconnect();
}

// ------------------------------------------------------------------------------------------------- scales

function niceStep(x) {
  const exp = Math.floor(Math.log10(x));
  const f = x / 10 ** exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * 10 ** exp;
}

/** Axis ticks from zero to a round number at or above max, about `count` steps; whole numbers for counts. */
export function ticks(max, count = 4, whole = true) {
  if (!(max > 0)) return [0, 1];
  let step = niceStep(max / count);
  if (whole) step = Math.max(1, Math.ceil(step));
  const top = Math.ceil(max / step) * step;
  const out = [];
  for (let v = 0; v <= top + step / 2; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

/** About how wide a label is, in px: CJK characters a full em, others a little over half. */
function textWidth(s, size = 12) {
  let w = 0;
  for (const ch of String(s)) w += ch.codePointAt(0) > 0x2e80 ? size : size * 0.6;
  return w;
}

/** Which day indices get a label: the last always, then back at an even step, at most `room` of them. */
function labelled(n, room) {
  const step = Math.max(1, Math.ceil(n / Math.max(1, room)));
  const out = new Set();
  for (let i = n - 1; i >= 0; i -= step) out.add(i);
  return out;
}

const shortDay = (d) => String(d).slice(5);

// ------------------------------------------------------------------------------------------------- shared frame

/** The figure every chart sits in: legend, plot, tooltip, table view and its switch. */
function frame({ legend, table, label }) {
  const fig = el("figure", { class: "chart" });
  const plot = el("div", { class: "chart__plot" });
  const tip = el("div", { class: "tip", hidden: true, "aria-hidden": "true" });
  const tableView = el("div", { class: "chart__table", hidden: true }, table);
  const toggle = el("button", { class: "btn btn--small chart__toggle", type: "button", "aria-pressed": "false", text: t("table") });
  toggle.addEventListener("click", () => {
    const showTable = tableView.hidden;
    tableView.hidden = !showTable;
    plot.hidden = showTable;
    tip.hidden = true;
    toggle.setAttribute("aria-pressed", String(showTable));
    toggle.textContent = showTable ? t("chart") : t("table");
  });
  if (label) fig.setAttribute("aria-label", label);
  fig.append(...[legend, plot, tip, tableView, toggle].filter(Boolean));
  return { fig, plot, tip };
}

function legendOf(items) {
  return el("ul", { class: "chart__legend" }, items.map((it) => el("li", {}, el("span", { class: `key ${it.box ? "key--box " : ""}${it.cls}`, "aria-hidden": "true" }), it.name)));
}

/** Place the tooltip beside x (plot coordinates), inside the figure. */
function placeTip(tip, plot, x, y) {
  tip.hidden = false;
  const fig = plot.parentElement;
  const w = tip.offsetWidth;
  let left = plot.offsetLeft + x + 14;
  if (left + w > fig.clientWidth) left = Math.max(0, plot.offsetLeft + x - 14 - w);
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(plot.offsetTop + y)}px`;
}

function tipRows(tip, title, rows) {
  tip.replaceChildren(
    el("div", { class: "tip__title", text: title }),
    ...rows.map((r) => el("div", { class: "tip__row" },
      r.cls ? el("span", { class: `key ${r.box ? "key--box " : ""}${r.cls}`, "aria-hidden": "true" }) : null,
      el("span", { class: "tip__value", text: r.value }), el("span", { class: "tip__name", text: r.name }))),
  );
}

/** Pointer and keyboard drive one cursor over the days: pointer by nearest day, arrows step, Home/End jump. */
function cursor(svgEl, { n, xAt, W, show }) {
  let at = null;
  const set = (i) => {
    at = i === null ? null : Math.max(0, Math.min(n - 1, i));
    show(at);
  };
  const nearest = (px) => {
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(xAt(i) - px) < Math.abs(xAt(best) - px)) best = i;
    return best;
  };
  svgEl.addEventListener("pointermove", (e) => {
    const r = svgEl.getBoundingClientRect();
    if (!r.width) return;
    set(nearest(((e.clientX - r.left) * W) / r.width));
  });
  svgEl.addEventListener("pointerleave", () => set(null));
  svgEl.addEventListener("focus", () => set(at ?? n - 1));
  svgEl.addEventListener("blur", () => set(null));
  svgEl.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1 }[e.key];
    if (step) set((at ?? n - 1) + step);
    else if (e.key === "Home") set(0);
    else if (e.key === "End") set(n - 1);
    else if (e.key === "Escape") set(null);
    else return;
    e.preventDefault();
  });
}

function axes(g, { yTicks, y, l, r, W, H, b, format, days, xAt, labelRoom, centred = false }) {
  for (const v of yTicks) {
    g.append(svg("line", { class: v === 0 ? "base-line" : "grid-line", x1: l, x2: W - r, y1: y(v), y2: y(v) }));
    g.append(svg("text", { class: "tick", x: l - 6, y: y(v), "text-anchor": "end", "dominant-baseline": "middle" }, format(v)));
  }
  const show = labelled(days.length, labelRoom);
  for (const i of show) {
    const x = xAt(i);
    // a line's first and last points sit on the plot's edges, so their labels align inwards; a column's sits mid-step
    const anchor = centred || days.length === 1 ? "middle" : i === 0 ? "start" : i === days.length - 1 ? "end" : "middle";
    g.append(svg("text", { class: "tick", x, y: H - b + 16, "text-anchor": anchor }, shortDay(days[i])));
  }
}

// ------------------------------------------------------------------------------------------------- line chart

/**
 * Lines over days on one axis from zero: series [{name, values, slot: 1–3}] (a fourth would fold into a table, never a
 * new hue). opts: {format (numbers on the axis and in the tooltip), label (a summary for assistive technology), height}.
 */
export function lineChart({ days, series, format = String, label = "", height = 220 }) {
  const table = dataTable(
    [{ key: "day", label: t("col_day") }, ...series.map((sr, k) => ({ key: `s${k}`, label: sr.name, num: true, format }))],
    days.map((d, i) => Object.fromEntries([["day", d], ...series.map((sr, k) => [`s${k}`, sr.values[i] ?? 0])])),
  );
  const legend = series.length > 1 ? legendOf(series.map((sr) => ({ name: sr.name, cls: `series-${sr.slot}` }))) : null;
  const { fig, plot, tip } = frame({ legend, table, label });

  const draw = (width) => {
    const W = Math.max(260, width);
    const narrow = W < 480;
    const H = narrow ? Math.min(height, 190) : height;
    const max = Math.max(0, ...series.flatMap((sr) => sr.values.map((v) => v || 0)));
    const yTicks = ticks(max);
    const top = yTicks[yTicks.length - 1] || 1;
    const endLabels = series.length > 0 && !narrow;
    const r = endLabels ? Math.min(W * 0.3, 16 + Math.max(...series.map((sr) => textWidth(sr.name)))) : 12;
    const l = 10 + Math.max(...yTicks.map((v) => textWidth(format(v), 11)));
    const tpad = 10;
    const b = 24;
    const plotW = W - l - r;
    const plotH = H - tpad - b;
    const n = days.length;
    const xAt = (i) => (n === 1 ? l + plotW / 2 : l + (i * plotW) / (n - 1));
    const y = (v) => tpad + plotH - ((v || 0) / top) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", tabindex: 0, "aria-label": label || series.map((sr) => sr.name).join(", ") });
    const g = svg("g");
    axes(g, { yTicks, y, l, r, W, H, b, format, days, xAt, labelRoom: Math.floor(plotW / 56) });
    for (const sr of series) {
      if (n > 1) g.append(svg("path", { class: `line series-${sr.slot}`, d: sr.values.map((v, i) => `${i ? "L" : "M"}${xAt(i).toFixed(1)},${y(v).toFixed(1)}`).join("") }));
      g.append(svg("circle", { class: `dot series-${sr.slot}`, cx: xAt(n - 1), cy: y(sr.values[n - 1]), r: 4 }));
    }
    if (endLabels) {
      // end labels in the text colour, nudged apart so they never overlap
      const placed = series.map((sr) => ({ sr, y: y(sr.values[n - 1]) })).sort((a, z) => a.y - z.y);
      for (let k = 1; k < placed.length; k++) placed[k].y = Math.max(placed[k].y, placed[k - 1].y + 14);
      const overflow = placed.length ? placed[placed.length - 1].y - (tpad + plotH) : 0;
      if (overflow > 0) for (const p of placed) p.y -= overflow;
      for (const p of placed) g.append(svg("text", { class: "end-label", x: W - r + 10, y: p.y, "dominant-baseline": "middle" }, p.sr.name));
    }
    const cross = svg("line", { class: "crosshair", y1: tpad, y2: tpad + plotH, visibility: "hidden" });
    const marks = series.map((sr) => svg("circle", { class: `dot series-${sr.slot}`, r: 4, visibility: "hidden" }));
    g.append(cross, ...marks, svg("rect", { class: "hit", x: l, y: tpad, width: Math.max(1, plotW), height: plotH }));
    root.append(g);
    plot.replaceChildren(root);
    cursor(root, {
      n, xAt, W,
      show: (i) => {
        if (i === null) {
          cross.setAttribute("visibility", "hidden");
          for (const m of marks) m.setAttribute("visibility", "hidden");
          tip.hidden = true;
          return;
        }
        const x = xAt(i);
        cross.setAttribute("x1", x);
        cross.setAttribute("x2", x);
        cross.setAttribute("visibility", "visible");
        series.forEach((sr, k) => {
          marks[k].setAttribute("cx", x);
          marks[k].setAttribute("cy", y(sr.values[i]));
          marks[k].setAttribute("visibility", "visible");
        });
        tipRows(tip, days[i], series.map((sr) => ({ cls: `series-${sr.slot}`, value: format(sr.values[i] ?? 0), name: sr.name })));
        placeTip(tip, plot, x, tpad);
      },
    });
  };
  watch(plot, draw);
  return fig;
}

// ------------------------------------------------------------------------------------------------- column chart

function barPath(x, y, w, h, radius) {
  if (!(h > 0) || !(w > 0)) return "";
  const r = Math.min(radius, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * Columns over days, one series: {days, values, name, format, breakdown(i) → [{name, value, cls}] for the tooltip and the
 * table (cls: a status or series class)}. The rounded end is the data end; 2px of surface between columns.
 */
export function columnChart({ days, values, name, format = String, label = "", height = 200, breakdown = null }) {
  const parts = breakdown ? days.map((_, i) => breakdown(i)) : null;
  const partNames = parts?.[0]?.map((p) => p.name) || [];
  const table = dataTable(
    [{ key: "day", label: t("col_day") }, { key: "v", label: name, num: true, format }, ...partNames.map((pn, k) => ({ key: `p${k}`, label: pn, num: true, format }))],
    days.map((d, i) => Object.fromEntries([["day", d], ["v", values[i] ?? 0], ...partNames.map((_, k) => [`p${k}`, parts[i][k]?.value ?? 0])])),
  );
  const { fig, plot, tip } = frame({ legend: null, table, label });

  const draw = (width) => {
    const W = Math.max(260, width);
    const H = W < 480 ? Math.min(height, 180) : height;
    const yTicks = ticks(Math.max(0, ...values.map((v) => v || 0)));
    const top = yTicks[yTicks.length - 1] || 1;
    const l = 10 + Math.max(...yTicks.map((v) => textWidth(format(v), 11)));
    const r = 8;
    const tpad = 10;
    const b = 24;
    const plotW = W - l - r;
    const plotH = H - tpad - b;
    const n = days.length;
    const step = plotW / n;
    const gap = step >= 4 ? 2 : 0;
    const bw = Math.max(1, step - gap);
    const xAt = (i) => l + i * step + step / 2;
    const y = (v) => tpad + plotH - ((v || 0) / top) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", tabindex: 0, "aria-label": label || name });
    const g = svg("g");
    axes(g, { yTicks, y, l, r, W, H, b, format, days, xAt, labelRoom: Math.floor(plotW / 56), centred: true });
    const bars = values.map((v, i) => {
      const yv = y(v);
      const path = svg("path", { class: "bar series-1", d: barPath(l + i * step + gap / 2, yv, bw, tpad + plotH - yv, 4) });
      g.append(path);
      return path;
    });
    g.append(svg("rect", { class: "hit", x: l, y: tpad, width: Math.max(1, plotW), height: plotH }));
    root.append(g);
    plot.replaceChildren(root);
    let hot = null;
    cursor(root, {
      n, xAt, W,
      show: (i) => {
        hot?.classList.remove("is-hot");
        hot = null;
        if (i === null) {
          tip.hidden = true;
          return;
        }
        hot = bars[i];
        hot.classList.add("is-hot");
        const rows = [{ cls: "series-1", box: true, value: format(values[i] ?? 0), name }];
        for (const p of parts?.[i] || []) if (p.value) rows.push({ cls: p.cls, box: true, value: format(p.value), name: p.name });
        tipRows(tip, days[i], rows);
        placeTip(tip, plot, xAt(i), tpad);
      },
    });
  };
  watch(plot, draw);
  return fig;
}

// ------------------------------------------------------------------------------------------------- small forms

/** A sparkline for a stat tile: the shape of the period, no axis, no hover (the tile's number is the reading). */
export function sparkline(values, { width = 120, height = 28 } = {}) {
  const root = svg("svg", { class: "tile__spark", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true", focusable: "false" });
  if (!values || values.length < 2) return root;
  const max = Math.max(...values, 0) || 1;
  const x = (i) => 3 + (i * (width - 8)) / (values.length - 1);
  const y = (v) => height - 4 - ((v || 0) / max) * (height - 8);
  root.append(svg("path", { class: "spark", d: values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("") }));
  root.append(svg("circle", { class: "spark-end", cx: x(values.length - 1), cy: y(values[values.length - 1]), r: 2.5 }));
  return root;
}

/**
 * Part of a whole as one bar: parts [{name, value, cls: good|serious|critical|other, state: good|warning|serious|critical|none}].
 * Each part's colour comes with its icon and its name in the legend, with the count and the share.
 */
export function stackBar(parts, { format = String, pct = (x) => `${Math.round(x * 100)}%` } = {}) {
  const sum = parts.reduce((a, p) => a + (p.value || 0), 0);
  const bar = el("div", { class: "stack", role: "img", "aria-label": parts.map((p) => `${p.name} ${format(p.value || 0)}`).join(", ") });
  for (const p of parts) {
    if (!p.value) continue;
    const seg = el("span", { class: p.cls, title: `${p.name}: ${format(p.value)} (${pct(p.value / sum)})` });
    seg.style.flexGrow = String(p.value);
    seg.style.flexBasis = "0";
    bar.append(seg);
  }
  const legend = el("ul", { class: "chart__legend" }, parts.map((p) => el("li", {},
    el("span", { class: `state state--${p.state}`, text: p.name }),
    el("span", { class: "tip__value", text: format(p.value || 0) }),
    el("span", { class: "tip__name", text: sum ? pct((p.value || 0) / sum) : "—" }))));
  return el("div", { class: "stack-wrap" }, bar, legend);
}

/** Horizontal bars, longest first: rows [{label, value}]; the value is printed at the end of each. */
export function hbars(rows, { format = String, label = "" } = {}) {
  const max = Math.max(1, ...rows.map((r) => r.value || 0));
  return el("ul", { class: "hbars", "aria-label": label || null }, rows.map((r) => {
    const bar = el("span", { class: "hbar", title: `${r.label}: ${format(r.value)}` });
    bar.style.width = `${Math.max(0.5, ((r.value || 0) / max) * 100)}%`;
    return el("li", {}, el("span", { class: "hbar-label", text: r.label }), el("span", { class: "hbar-track" }, bar), el("span", { class: "val", text: format(r.value) }));
  }));
}

/** A table wider than its card scrolls sideways; then (only then) it takes the focus, as a named region, so the keyboard
 * can scroll it too. */
function scrollable(wrap, label) {
  watch(wrap, () => {
    if (wrap.scrollWidth > wrap.clientWidth + 1) {
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", label);
    } else {
      wrap.removeAttribute("tabindex");
      wrap.removeAttribute("role");
      wrap.removeAttribute("aria-label");
    }
  });
}

/**
 * A table: columns [{key, label, num, mono, format(value, row) → text or a node}], rows of objects. Empty cells show "—".
 */
export function dataTable(columns, rows, { caption = "" } = {}) {
  const table = el("table", {},
    caption ? el("caption", { class: "sr-only", text: caption }) : null,
    el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : null, scope: "col", text: c.label })))),
    el("tbody", {}, rows.map((row) => el("tr", {}, columns.map((c) => {
      const v = c.format ? c.format(row[c.key], row) : row[c.key];
      const cell = el("td", { class: [c.num ? "num" : "", c.mono ? "mono" : ""].join(" ").trim() || null });
      if (v instanceof Node) cell.append(v);
      else cell.textContent = v === null || v === undefined || v === "" ? "—" : String(v);
      return cell;
    })))));
  const wrap = el("div", { class: "table-wrap" }, table);
  scrollable(wrap, caption || columns.map((c) => c.label).join(" · "));
  return wrap;
}
