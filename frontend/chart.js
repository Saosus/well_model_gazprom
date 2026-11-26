import {formatOperatingPoint} from "./number-format.js";

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


function badge(parent, width) {
  const group = element("g", {});
  const label = element("text", {
    "text-anchor": "middle", y: 4, fill: "white", "font-size": 12,
    style: "font-family: sans-serif; font-variant-numeric: tabular-nums",
  });
  group.append(element("rect", {
    x: -width / 2, y: -10, width, height: 20, rx: 4, fill: "#36534b",
  }), label);
  parent.append(group);
  return (x, y, value) => {
    group.setAttribute("transform", `translate(${x} ${y})`);
    label.textContent = formatOperatingPoint(value);
  };
}

function addHover(frame, svg, bounds, curves, point, q, p, pressureWidth) {
  const {left, right, top, bottom, width} = bounds;
  const area = element("rect", {x: left, y: top, width: right-left, height: bottom-top,
    fill: "transparent", cursor: "none"});
  const overlay = element("g", {visibility: "hidden", "pointer-events": "none", "aria-hidden": "true"});
  const guide = {stroke: "#516e66", "stroke-opacity": 0.62, "stroke-dasharray": "3 4"};
  const horizontal = element("line", {...guide, x1: left, x2: right});
  const vertical = element("line", {...guide, y1: top, y2: bottom});
  const dot = element("circle", {r: 3.5, stroke: "white", "stroke-width": 1.5});
  overlay.append(horizontal, vertical, dot);
  const flowWidth = formatOperatingPoint(q.end).length * 8 + 10;
  const showPressure = badge(overlay, pressureWidth);
  const showFlow = badge(overlay, flowWidth);
  frame.append(area, overlay);
  let activeCurve = null;

  function move(event) {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const mouse = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    let {x, y} = mouse;
    let color = "#516e66";
    let flow, pressure;
    if (Math.hypot(x-point.x, y-point.y) <= 8) {
      ({x, y, flow, pressure} = point);
      color = "var(--point)";
    } else {
      let best = Infinity;
      let selected = null;
      for (const curve of curves) {
        for (let i = 1; i < curve.points.length; i++) {
          const a = curve.points[i-1], b = curve.points[i];
          const dx = b.x-a.x, dy = b.y-a.y;
          const length2 = dx*dx + dy*dy;
          const t = length2 ? Math.max(0, Math.min(1,
            ((mouse.x-a.x)*dx + (mouse.y-a.y)*dy) / length2)) : 0;
          const px = a.x+t*dx, py = a.y+t*dy;
          const distance = Math.hypot(mouse.x-px, mouse.y-py);
          const retained = activeCurve === curve;
          const score = distance - (retained ? 1.5 : 0);
          if (distance <= (retained ? 10 : 7) && score < best) {
            best = score;
            selected = curve;
            x = px;
            y = py;
          }
        }
      }
      activeCurve = selected;
      if (selected) color = selected.color;
    }
    horizontal.setAttribute("y1", y);
    horizontal.setAttribute("y2", y);
    vertical.setAttribute("x1", x);
    vertical.setAttribute("x2", x);
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("fill", color);
    showPressure(left - pressureWidth/2 - 3, y, pressure ?? (bottom-y)/(bottom-top)*p.end);
    showFlow(Math.max(flowWidth/2, Math.min(width-flowWidth/2, x)), bottom+15,
      flow ?? (x-left)/(right-left)*q.end);
    overlay.setAttribute("visibility", "visible");
  }
  function hide() {
    activeCurve = null;
    overlay.setAttribute("visibility", "hidden");
  }
  area.addEventListener("pointermove", move);
  area.addEventListener("pointerleave", hide);
  area.addEventListener("pointercancel", hide);
}

export function drawChart(svg, result, reference = null) {
  const {width, height} = svg.parentElement.getBoundingClientRect();
  if (width < 1 || height < 1) return;
  const top = 24, right = width - 25, bottom = height - 56;
  const {ipr, vlp, operating_point: point} = result;
  const q = axis(Math.max(...ipr.q_liq, ...vlp.q_liq, point.q_liq, ...(reference ? [...reference.ipr.q_liq, ...reference.vlp.q_liq, reference.operating_point.q_liq] : [])), width < 430 ? 4 : 6);
  const p = axis(Math.max(...ipr.p_wf, ...vlp.p_wf, point.p_wf, ...(reference ? [...reference.ipr.p_wf, ...reference.vlp.p_wf, reference.operating_point.p_wf] : [])), 5);
  const pressureWidth = valueLabel.format(p.end).length * 8 + 10;
  const left = Math.max(60, pressureWidth + 26);
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

  if (reference) {
    for (const [name, curve, color] of [["IPR", reference.ipr, "var(--ipr)"], ["VLP", reference.vlp, "var(--vlp)"]]) {
      frame.append(element("polyline", {"aria-label": "Базовая " + name, points:curve.q_liq.map((v,i)=>`${x(v)},${y(curve.p_wf[i])}`).join(" "), fill:"none", stroke:color, "stroke-width":2, "stroke-dasharray":"3 5", opacity:.45}));
    }
    const bp=reference.operating_point;
    const baseMarker=element("circle",{cx:x(bp.q_liq),cy:y(bp.p_wf),r:6,fill:"white",stroke:"var(--point)","stroke-width":2});
    baseMarker.append(element("title",{},`Базовая точка · ${formatOperatingPoint(bp.q_liq)} м³/сут · ${formatOperatingPoint(bp.p_wf)} атм`)); frame.append(baseMarker);
  }
  const curves = [];
  for (const [name, curve, style] of [
    ["IPR", ipr, {stroke: "var(--ipr)"}],
    ["VLP", vlp, {stroke: "var(--vlp)", "stroke-dasharray": "8 5"}],
  ]) {
    const vertices = curve.q_liq.map((flow, i) => ({x: x(flow), y: y(curve.p_wf[i])}));
    curves.push({points: vertices, color: style.stroke});
    const points = vertices.map(v => `${v.x},${v.y}`).join(" ");
    frame.append(element("polyline", {"aria-label": name, points, fill: "none", "stroke-width": 3, "stroke-linejoin": "round", ...style}));
  }
  const marker = element("circle", {cx: x(point.q_liq), cy: y(point.p_wf), r: 6.5, fill: "var(--point)", stroke: "white", "stroke-width": 2});
  marker.append(element("title", {}, `Рабочая точка · ${formatOperatingPoint(point.q_liq)} м³/сут · ${formatOperatingPoint(point.p_wf)} атм`));
  frame.append(marker);
  addHover(frame, svg, {left, right, top, bottom, width}, curves, {
    x: x(point.q_liq), y: y(point.p_wf), flow: point.q_liq, pressure: point.p_wf,
  }, q, p, pressureWidth);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.replaceChildren(frame);
}
