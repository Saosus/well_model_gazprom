import {backendUrl, directBackend} from './backend-port.js';

const localFrontend = directBackend;
const components = [
  {
    id: "ipr", name: "IPR", purpose: "Приток из пласта", route: "/ipr/*", port: 8003,
    local: {health: "/ipr/health", docs: "/docs"},
    proxy: {health: "/ipr/health", docs: "/ipr/docs"},
  },
  {
    id: "vlp", name: "VLP", purpose: "Подъём по стволу и PVT", route: "/vlp/*", port: 8002,
    local: {health: "/vlp/health", docs: "/docs"},
    proxy: {health: "/vlp/health", docs: "/vlp/docs"},
  },
  {
    id: "nodal", name: "Nodal", purpose: "Пересечение кривых", route: "/nodal/*", port: 8004,
    local: {health: "/nodal/health", docs: "/docs"},
    proxy: {health: "/nodal/health", docs: "/nodal/docs"},
  },
  {
    id: "backend", name: "Backend", purpose: "API дашборда", route: "/api/*", port: 8080,
    local: {health: "/health", docs: "/docs"},
    proxy: {health: "/backend/health", docs: "/backend/docs"},
  },
  {id: "frontend", name: "Frontend", purpose: "Веб-интерфейс", route: "/", port: 3000},
];

const rows = document.querySelector("#services");
const refresh = document.querySelector("#refresh");
const checked = document.querySelector("#checked");
const indicators = new Map();

function serviceUrl(component, key) {
  const path = (localFrontend ? component.local : component.proxy)[key];
  if (component.id === "backend" && localFrontend) return backendUrl(path).href;
  const url = new URL(path, window.location.origin);
  if (localFrontend) url.port = String(component.port);
  return url.href;
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
  route.textContent = component.route;

  const state = document.createElement("span");
  state.className = "state";
  row.insertCell().append(state);
  indicators.set(component.id, state);

  const links = document.createElement("div");
  links.className = "links";
  if (component.local) {
    for (const [label, key] of [["Health", "health"], ["Swagger", "docs"]]) {
      const anchor = document.createElement("a");
      anchor.textContent = label;
      anchor.setAttribute("aria-label", component.name + " — " + label);
      anchor.href = serviceUrl(component, key);
      links.append(anchor);
    }
    setState(component.id, "unknown", "Ожидание");
  } else {
    links.textContent = "Эта страница";
    setState(component.id, "ok", "Открыт");
  }
  row.insertCell().append(links);
}

const schema = document.querySelector("#schema");
schema.href = localFrontend ? backendUrl("/openapi.json").href : "/backend/openapi.json";

async function checkServices() {
  refresh.disabled = true;
  checked.textContent = "Проверяем связь с компонентами…";
  for (const item of components.filter(item => item.local)) {
    setState(item.id, "unknown", "Проверка…");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6000);
  try {
    const statusUrl = localFrontend
      ? backendUrl("/api/services/health").href
      : "/api/services/health";
    const response = await fetch(statusUrl, {
      cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new Error("health");
    const statuses = await response.json();
    setState("backend", "ok", "На связи");
    for (const id of ["ipr", "vlp", "nodal"]) {
      const ok = statuses[id] === "ok";
      setState(id, ok ? "ok" : "unavailable", ok ? "На связи" : "Недоступен");
    }
    checked.textContent = "Последняя проверка в " + new Date().toLocaleTimeString("ru-RU");
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
