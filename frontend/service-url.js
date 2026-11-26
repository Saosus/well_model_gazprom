import { backendPort } from "./backend-port.js";

const localPorts = { backend: backendPort, ipr: 8003, vlp: 8002, nodal: 8004 };

export function serviceUrl(service, path) {
  const url = new URL(path, window.location.origin);
  if (backendPort !== null) {
    url.port = localPorts[service];
  } else if (service !== "backend" && path === "/docs") {
    url.pathname = `/${service}/docs`;
  }
  return url;
}
