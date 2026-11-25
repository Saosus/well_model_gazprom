import {backendUrl} from "./backend-port.js";
import {drawChart} from "./chart.js";

const form = document.querySelector("#parameters");
const chart = document.querySelector("#chart");
const empty = document.querySelector("#empty");
const status = document.querySelector("#status");
const button = document.querySelector("#calculate");
const flow = document.querySelector("#flow");
const pressure = document.querySelector("#pressure");
const endpoint = backendUrl("/api/calculate");
const format = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 5});
let current = null;
let displayed = null;

function message(value, tone = "") {
  status.textContent = value;
  status.dataset.tone = tone;
}

function clearOutput(placeholder) {
  displayed = null;
  chart.setAttribute("hidden", "");
  chart.replaceChildren();
  empty.hidden = false;
  empty.textContent = placeholder;
  flow.textContent = pressure.textContent = "—";
}

function invalidate(note) {
  current?.abort();
  current = null;
  button.disabled = false;
  clearOutput("Для этих параметров график ещё не построен.");
  message(note);
  for (const input of form.querySelectorAll('[aria-invalid]')) {
    input.removeAttribute("aria-invalid");
    input.setCustomValidity("");
  }
}

form.addEventListener("input", () => invalidate("Параметры изменены. Нажмите «Рассчитать»."));
form.addEventListener("reset", () => invalidate("Восстановлены параметры примера. Нажмите «Рассчитать»."));

function rejectField(input, text) {
  input.closest("details").open = true;
  input.setAttribute("aria-invalid", "true");
  input.setCustomValidity(text);
  input.reportValidity();
  throw new Error(text);
}

function requestBody() {
  const data = {};
  for (const input of form.querySelectorAll("input[name]")) {
    const path = input.name.split(".");
    let target = data;
    for (const key of path.slice(0, -1)) target = target[key] ??= {};
    const label = input.labels[0].firstChild.textContent;
    let value;
    if (input.type === "number") {
      value = input.valueAsNumber;
      if (!Number.isFinite(value)) rejectField(input, `Введите число в поле «${label}».`);
      if (input.name === "pvt.wct" && (value < 0 || value > 100))
        rejectField(input, "Обводнённость должна быть от 0 до 100 %.");
      if (input.name.endsWith("_points") && (!Number.isInteger(value) || value < 2 || value > 201))
        rejectField(input, "Число точек должно быть целым от 2 до 201.");
    } else {
      const items = input.value.trim().split(/\s+/);
      if (!items.every(item => /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(item) && Number.isFinite(Number(item))))
        rejectField(input, `«${label}» — введите числа через пробел.`);
      value = items.map(Number);
    }
    target[path.at(-1)] = value;
  }
  return data;
}

async function calculate() {
  invalidate("");
  let payload;
  try {
    payload = requestBody();
  } catch (error) {
    message(error.message, "error");
    return;
  }
  const pending = new AbortController();
  current = pending;
  button.disabled = true;
  clearOutput("Строим кривые IPR и VLP…");
  message("Идёт расчёт рабочей точки.");
  try {
    const response = await fetch(endpoint, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload), signal: pending.signal,
    });
    const result = await response.json();
    if (current !== pending) return;
    if (!response.ok) throw new Error(result.detail || `Ошибка backend (HTTP ${response.status}).`);
    const point = result.operating_point;
    empty.hidden = true;
    chart.removeAttribute("hidden");
    drawChart(chart, result);
    displayed = result;
    flow.textContent = format.format(point.q_liq);
    pressure.textContent = format.format(point.p_wf);
    message(`Результат для текущих параметров · устьевое давление ${payload.well.p_wh} атм · обводнённость ${payload.pvt.wct} %.`);
  } catch (error) {
    if (current !== pending) return;
    clearOutput("Рабочая точка не получена.");
    message(error instanceof TypeError ? "Не удалось получить расчёт. Проверьте связь на странице «Сервисы»." : error.message, "error");
  } finally {
    if (current === pending) {
      button.disabled = false;
      current = null;
    }
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  calculate();
});
new ResizeObserver(() => { if (displayed) drawChart(chart, displayed); }).observe(chart.parentElement);
calculate();
