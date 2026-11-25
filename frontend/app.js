import { serviceUrl } from "./service-url.js";

const components = [
  { id: "ipr", name: "IPR", purpose: "Приток из пласта", health: "/ipr/health" },
  { id: "vlp", name: "VLP", purpose: "Подъём по стволу и PVT", health: "/vlp/health" },
  { id: "nodal", name: "Nodal", purpose: "Пересечение кривых", health: "/nodal/health" },
  { id: "backend", name: "Backend", purpose: "API дашборда", health: "/health" },
  { id: "frontend", name: "Frontend", purpose: "Веб-интерфейс" },
];

const rows = document.querySelector("#services");
const refresh = document.querySelector("#refresh");
const checked = document.querySelector("#checked");
const indicators = new Map();

function link(label, component, path) {
  const anchor = document.createElement("a");
  anchor.textContent = label;
  anchor.setAttribute("aria-label", component.name + " — " + label);
  anchor.href = serviceUrl(component.id, path).href;
  return anchor;
}

function setState(id, state, label) {
  const indicator = indicators.get(id);
  indicator.dataset.state = state;
  indicator.textContent = label;
}

for (const component of components) {
  const row = rows.insertRow();
  const name = document.createElement("th");
  name.scope = "row";
  name.textContent = component.name;
  const purpose = document.createElement("span");
  purpose.className = "purpose";
  purpose.textContent = component.purpose;
  name.append(purpose);
  row.append(name);
  const route = row.insertCell();
  route.className = "route";
  route.textContent = component.health || "/";
  const state = document.createElement("span");
  state.className = "state";
  row.insertCell().append(state);
  indicators.set(component.id, state);
  const links = document.createElement("div");
  links.className = "links";
  if (component.health) {
    links.append(link("Health", component, component.health));
    links.append(link("Swagger", component, "/docs"));
    setState(component.id, "unknown", "Ожидание");
  } else {
    links.textContent = "Эта страница";
    setState(component.id, "ok", "Открыт");
  }
  row.insertCell().append(links);
}

document.querySelector("#schema").href = serviceUrl("backend", "/openapi.json");

async function checkServices() {
  refresh.disabled = true;
  checked.textContent = "Проверяем связь с компонентами…";
  for (const item of components.filter(item => item.health)) {
    setState(item.id, "unknown", "Проверка…");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6000);
  try {
    const response = await fetch(serviceUrl("backend", "/api/services/health"), {
      cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new Error("health");
    const statuses = await response.json();
    setState("backend", "ok", "На связи");
    for (const id of ["ipr", "vlp", "nodal"]) {
      const ok = statuses[id] === "ok";
      setState(id, ok ? "ok" : "unavailable", ok ? "На связи" : "Недоступен");
    }
    const time = new Date().toLocaleTimeString("ru-RU");
    checked.textContent = "Последняя проверка в " + time;
  } catch {
    setState("backend", "unavailable", "Нет ответа");
    for (const id of ["ipr", "vlp", "nodal"]) setState(id, "unknown", "Не проверен");
    checked.textContent = "Проверка не удалась. Откройте Health у backend и повторите попытку.";
  } finally {
    clearTimeout(timer);
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", checkServices);
checkServices();
