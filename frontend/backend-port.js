// В Docker/Nginx frontend и API находятся на одном origin.
// Локальный scripts/run_local.py подменяет этот файл и включает directBackend.
export const backendPort = 8080;
export const directBackend = false;

export function backendUrl(path) {
  const url = new URL(path, window.location.origin);
  if (directBackend) url.port = String(backendPort);
  return url;
}
