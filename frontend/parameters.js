const form = document.querySelector("#parameters");
const stations = document.querySelector("#stations");
const summary = document.querySelector("#validation-summary");
const surveyPrefix = "well.inclinometry.";
const defaultSurvey = [[0, 0], [1000, 1000], [1500, 1100]];

function appendStation(values = ["", ""]) {
  const row = stations.insertRow();
  row.insertCell();
  for (const [i, col] of ["MD", "TVD"].entries()) {
    const label = document.createElement("label");
    const wrap = document.createElement("span");
    wrap.className = "field";
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.required = true;
    input.dataset.column = col;
    input.defaultValue = values[i];
    wrap.append(input);
    label.append(wrap);
    row.insertCell().append(label);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary remove";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    row.remove();
    indexStations();
    form.dispatchEvent(new Event("input"));
  });
  row.insertCell().append(remove);
}

function indexStations() {
  Array.from(stations.rows).forEach((row, i) => {
    row.cells[0].textContent = i + 1;
    for (const input of row.querySelectorAll("input")) {
      input.name = surveyPrefix + input.dataset.column + "." + i;
      input.setAttribute("aria-label", "Строка " + (i+1) + ", " + input.dataset.column + ", м");
    }
    row.querySelector("button").disabled = stations.rows.length === 1;
    row.querySelector("button").setAttribute("aria-label", "Удалить замер " + (i+1));
  });
}

export function resetSurvey() {
  stations.replaceChildren();
  defaultSurvey.forEach(appendStation);
  indexStations();
}
resetSurvey();
document.querySelector("#add-station").addEventListener("click", () => {
  appendStation();
  indexStations();
  stations.lastElementChild.querySelector("input").focus();
  form.dispatchEvent(new Event("input"));
});

export function applyParameters(parameters) {
  const survey = parameters.well?.inclinometry || {};
  const count = Math.max(1, survey.MD?.length || 0, survey.TVD?.length || 0);
  stations.replaceChildren();
  for (let i = 0; i < count; i++) appendStation();
  indexStations();
  for (const input of form.querySelectorAll("input[name]")) {
    const value = input.name.split(".").reduce((node, key) => node?.[key], parameters);
    input.value = Number.isFinite(value) ? value : "";
  }
}

export function requestBody(forAssistant = false) {
  const data = {well: {inclinometry: {MD: [], TVD: []}}};
  for (const input of form.querySelectorAll("input[name]")) {
    if (forAssistant && !input.value && !input.validity.badInput && !input.dataset.column) continue;
    const path = input.name.split(".");
    let target = data;
    for (const key of path.slice(0, -1)) target = target[key] ??= {};
    const value = input.valueAsNumber;
    target[path.at(-1)] = Number.isFinite(value) ? value : null;
  }
  if (forAssistant) {
    const survey = data.well.inclinometry;
    for (const col of ["MD", "TVD"]) {
      if (survey[col].every(value => value === null)) delete survey[col];
      else if (survey[col].includes(null)) throw Error("Заполните пустые ячейки " + col + " или удалите лишний замер.");
    }
    if (!Object.keys(survey).length) delete data.well.inclinometry;
    if (!Object.keys(data.well).length) delete data.well;
    if (!Object.keys(data).length) throw Error("Сначала заполните хотя бы один параметр.");
  }
  return data;
}

export function renderIssues(issues) {
  const groups = new Map();
  for (const issue of issues) {
    for (const path of issue.paths) {
      if (!groups.has(path)) groups.set(path, []);
      groups.get(path).push(issue);
    }
  }
  for (const input of form.querySelectorAll("input[name]")) {
    const label = input.closest("label");
    label.querySelector(".feedback")?.remove();
    const hint = label.querySelector(".hint");
    if (hint) hint.id = "hint-" + input.name;
    const descriptions = [hint?.id || "survey-help"];
    const messages = groups.get(input.name) || [];
    const blocking = messages.some(item => item.level === "error");
    input.toggleAttribute("data-warning", !!messages.length && !blocking);
    input.setAttribute("aria-invalid", String(blocking));
    if (messages.length) {
      input.closest("details").open = true;
      const feedback = document.createElement("span");
      feedback.className = "feedback";
      feedback.id = "feedback-" + input.name;
      descriptions.push(feedback.id);
      for (const item of messages) {
        const line = document.createElement("span");
        line.className = item.level;
        const row = input.dataset.column ? input.getAttribute("aria-label").replace(", м", "") + ". " : "";
        line.textContent = row + item.message;
        feedback.append(line);
      }
      label.append(feedback);
    }
    input.setAttribute("aria-describedby", descriptions.join(" "));
  }
  const errors = issues.filter(x => x.level === "error").length;
  const warnings = issues.length - errors;
  summary.hidden = !issues.length;
  summary.className = errors ? "error" : "warning";
  summary.textContent = [errors && "Ошибок: " + errors, warnings && "Предупреждений: " + warnings,
    errors ? "Исправьте отмеченные поля." : "Расчёт доступен."].filter(Boolean).join(" · ");
  return errors;
}
