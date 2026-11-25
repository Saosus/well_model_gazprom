const ns = "http://www.w3.org/2000/svg";

function element(name, attributes, label) {
  const node = document.createElementNS(ns, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (label !== undefined) node.textContent = label;
  return node;
}

function axis(maximum, count) {
  const raw = maximum / count;
  const base = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find(n => n * base >= raw) ?? 10) * base;
  const intervals = Math.ceil(maximum / step);
  const ticks = Array.from({length: intervals + 1}, (_, i) => i * step);
  return {ticks, end: ticks.at(-1)};
}

const tickLabel = new Intl.NumberFormat("ru-RU", {maximumSignificantDigits: 4});
function tick(value) {
  return value && (value < 0.001 || value >= 1e6) ? value.toExponential(0) : tickLabel.format(value);
}

export function drawChart(svg, result) {
  const {width, height} = svg.parentElement.getBoundingClientRect();
  if (width < 1 || height < 1) return;
  const left = 60, top = 24, right = width - 25, bottom = height - 56;
  const {ipr, vlp, operating_point: point} = result;
  const q = axis(Math.max(...ipr.q_liq, ...vlp.q_liq, point.q_liq), width < 430 ? 4 : 6);
  const p = axis(Math.max(...ipr.p_wf, ...vlp.p_wf, point.p_wf), 5);
  const x = value => left + value / q.end * (right - left);
  const y = value => bottom - value / p.end * (bottom - top);
  const frame = document.createDocumentFragment();

  function line(x1, y1, x2, y2) {
    frame.append(element("line", {x1, y1, x2, y2, stroke: "#d9e2dc"}));
  }
  function label(x, y, text, anchor = "middle", extra = {}) {
    frame.append(element("text", {x, y, fill: "#435b53", "font-size": 12, "text-anchor": anchor, ...extra}, text));
  }
  for (const v of q.ticks) {
    line(x(v), top, x(v), bottom);
    label(x(v), bottom + 18, tick(v));
  }
  for (const v of p.ticks) {
    line(left, y(v), right, y(v));
    label(left - 8, y(v) + 4, tick(v), "end");
  }
  label((left + right) / 2, height - 12, "Дебит q_liq, м³/сут");
  const mid = (top + bottom) / 2;
  label(16, mid, "Давление p_wf, атм", "middle", {transform: `rotate(-90 16 ${mid})`});

  for (const [name, curve, style] of [
    ["IPR", ipr, {stroke: "var(--ipr)"}],
    ["VLP", vlp, {stroke: "var(--vlp)", "stroke-dasharray": "8 5"}],
  ]) {
    const points = curve.q_liq.map((flow, i) => [x(flow), y(curve.p_wf[i])].join(",")).join(" ");
    frame.append(element("polyline", {"aria-label": name, points, fill: "none", "stroke-width": 3, "stroke-linejoin": "round", ...style}));
  }
  const marker = element("circle", {cx: x(point.q_liq), cy: y(point.p_wf), r: 6.5, fill: "var(--point)", stroke: "white", "stroke-width": 2});
  marker.append(element("title", {}, `Рабочая точка · ${point.q_liq} м³/сут · ${point.p_wf} атм`));
  frame.append(marker);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.replaceChildren(frame);
}
