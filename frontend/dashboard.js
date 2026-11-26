import {initAssistant, defaultPaths} from "./assistant.js";
import { serviceUrl } from "./service-url.js";
import {applyParameters, requestBody, renderIssues, resetSurvey} from "./parameters.js";
import {drawChart} from "./chart.js";
import {initAnalysis, exportReport} from "./analysis.js";
import {initScenarios} from "./scenarios.js";
import {formatOperatingPoint} from "./number-format.js";

const form = document.querySelector("#parameters");
const chart = document.querySelector("#chart");
const empty = document.querySelector("#empty");
const status = document.querySelector("#status");
const button = document.querySelector("#calculate");
const flow = document.querySelector("#flow");
const pressure = document.querySelector("#pressure");
const endpoint = serviceUrl("backend", "/api/calculate");
let current = null;
let displayed = null;
let reportSnapshot = null;
const reportButtons=[document.getElementById("report-html"),document.getElementById("report-md")];
function setReport(snapshot){reportSnapshot=snapshot;reportButtons.forEach(b=>b.disabled=!snapshot);}
reportButtons.forEach((b,i)=>b.addEventListener("click",()=>{if(reportSnapshot)exportReport([reportSnapshot],i?"md":"html");}));

function message(value, tone = "") {
  status.textContent = value;
  status.dataset.tone = tone;
}

function clearOutput(placeholder) {
  displayed = null;
  setReport(null);
  chart.setAttribute("hidden", "");
  chart.replaceChildren();
  empty.hidden = false;
  empty.textContent = placeholder;
  flow.textContent = pressure.textContent = "—";
}

let checkedIssues = [];
let validation = null;

async function validate(run = false) {
  validation?.abort();
  current?.abort();
  current = null;
  const checking = new AbortController();
  validation = checking;
  button.disabled = true;
  clearOutput("Для этих параметров график ещё не построен.");
  const payload = requestBody();
  try {
    const response = await fetch(serviceUrl("backend", "/api/validate"), {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload), signal: checking.signal,
    });
    if (!response.ok) throw new Error();
    const result = await response.json();
    if (validation !== checking) return;
    checkedIssues = result.issues;
    const errors = renderIssues(checkedIssues);
    button.disabled = !!errors;
    message(errors ? "Исправьте отмеченные поля для запуска расчёта." : "Параметры проверены. Можно запускать расчёт.", errors ? "error" : "");
    if (run && !errors) await calculate(payload);
  } catch {
    if (validation !== checking) return;
    button.disabled = false;
    message("Проверка недоступна. Проверьте связь на странице «Сервисы» и нажмите «Рассчитать», чтобы повторить.", "error");
  }
}

form.addEventListener("input", () => validate());
form.addEventListener("reset", () => setTimeout(() => { resetSurvey(); validate(); }, 0));

function showFailure(detail, statusCode) {
  if (detail.startsWith("NODAL") && /не пересекаются|нет пересечения/i.test(detail)) {
    clearOutput("В заданном диапазоне дебитов рабочая точка отсутствует.");
    message("Кривые IPR и VLP не пересеклись. Проверьте диапазон дебита VLP и исходные давления.");
    return;
  }
  if (detail.startsWith("NODAL") && /неоднозначна|нескольких/.test(detail)) {
    clearOutput("Единственную рабочую точку определить нельзя.");
    message(detail);
    return;
  }
  const issues = [];
  for (const part of detail.replace(/^(IPR|VLP) — /, "").split(" | ")) {
    const split = part.indexOf(" — ");
    if (split < 0) continue;
    let path = part.slice(0, split);
    if (!form.elements.namedItem(path)) {
      if (path === "points") path = detail.startsWith("IPR") ? "sampling.ipr_points" : "sampling.vlp_points";
      else path = ["well.", "reservoir.", "pvt.", "sampling."].map(p => p+path).find(p => form.elements.namedItem(p));
    }
    if (path && form.elements.namedItem(path))
      issues.push({paths: [path], message: part.slice(split+3), level: "error"});
  }
  if (issues.length) {
    renderIssues([...checkedIssues, ...issues]);
    button.disabled = true;
    message("Расчёт остановлен. Проверьте отмеченные параметры.", "error");
  } else {
    message(detail || ("Сервер не смог завершить расчёт (HTTP " + statusCode + "). Проверьте состояние сервисов."), "error");
  }
}

async function calculate(payload) {
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
    const result = await response.json().catch(() => ({}));
    if (current !== pending) return;
    button.disabled = false;
    if (!response.ok) {
      clearOutput("Расчёт не завершён.");
      showFailure(typeof result.detail === "string" ? result.detail : "", response.status);
      return;
    }
    renderResult(result,payload,checkedIssues);
    message(`Результат для текущих параметров · устьевое давление ${payload.well.p_wh} атм · обводнённость ${payload.pvt.wct} %.`);
  } catch (error) {
    if (current !== pending) return;
    button.disabled = false;
    clearOutput("Рабочая точка не получена.");
    message(error instanceof TypeError ? "Не удалось получить расчёт. Проверьте связь на странице «Сервисы»." : error.message, "error");
  } finally {
    if (current === pending) {
      current = null;
    }
  }
}

function renderResult(result,parameters,issues=[]) {
  setReport({name:"Текущая скважина",parameters:structuredClone(parameters),calculation:structuredClone(result),issues});
  const point = result.operating_point;
  empty.hidden = true;
  chart.removeAttribute("hidden");
  drawChart(chart, result);
  displayed = result;
  flow.textContent = formatOperatingPoint(point.q_liq);
  pressure.textContent = formatOperatingPoint(point.p_wf);
}

function assistantIssues(report) {
  const issues = [];
  function add(path, message, level, row) {
    let paths = [path];
    if (path === "well.inclinometry.MD" || path === "well.inclinometry.TVD") {
      paths = row ? [path + "." + (row - 1)] :
        Array.from(form.querySelectorAll('input[name^="' + path + '."]'), input => input.name);
    }
    issues.push({paths, message, level});
  }
  for (const e of report.errors) add(e.path, e.message, "error", e.row);
  for (const e of report.warnings) add(e.path, e.message, "warning", e.row);
  for (const p of report.missing) add(p, "Заполните обязательное поле.", "error");
  for (const p of defaultPaths(report)) add(p, "Введите значение или согласуйте предложенное в чате.", "error");
  return issues;
}

const analysis=initAnalysis({requestBody,applyParameters});
const scenarios=initScenarios({requestBody, applyParameters, validate});
const restored=initAssistant({
  scenario: scenarios.render,
  analysis: analysis.render,
  report: row=>exportReport([row],"html"),
  form, requestBody,
  begin() {
    // A chat message may be a question: do not discard an unrelated result.
    form.inert = true;
  },
  finish() { form.inert = false; },
  apply(response) {
    if (response.mode === "conversation") return;
    validation?.abort();
    validation = null;
    current?.abort();
    current = null;
    if (response.status === "invalid_input" || !Object.keys(response.parameters).length) {
      button.disabled = checkedIssues.some(item => item.level === "error");
      clearOutput("Новый расчёт из чата не выполнен. Данные формы сохранены.");
      message(response.answer, "error");
      return;
    }
    applyParameters(response.parameters);
    saveForm();
    checkedIssues = assistantIssues(response.validation);
    button.disabled = Boolean(renderIssues(checkedIssues));
    if (response.calculation && response.status === "calculated") {
      renderResult(response.calculation,response.parameters,checkedIssues);
      message("Рабочая точка рассчитана по параметрам из диалога.");
    } else {
      clearOutput("Рабочая точка в этом запросе не получена.");
      message(response.status === "needs_input" ? "Уточните отмеченные параметры в чате или в форме." : response.answer);
    }
  },
  fail() { /* No new server state received: leave form, validation and existing graph unchanged. */ },
});

form.addEventListener("submit", event => {
  event.preventDefault();
  validate(true);
});
new ResizeObserver(() => { if (displayed) drawChart(chart, displayed); }).observe(chart.parentElement);
if (!restored) {
  try { const saved=JSON.parse(localStorage.getItem('well-form-v7')||'null'); if(saved) applyParameters(saved); } catch {}
  validate(true);
}
function saveForm(){try {localStorage.setItem('well-form-v7',JSON.stringify(requestBody()));}catch{}}
form.addEventListener('input',saveForm);
form.addEventListener('reset',()=>setTimeout(saveForm,0));

