const origin = new URL(window.QING_B2B_CONFIG?.apiOrigin || location.origin, location.origin).origin;
const api = new URL("/api/v1/b2b/", origin);

export const absolute = (path) => new URL(path, origin).href;
export const sameOrigin = origin === location.origin;

export function embeddedSettings() {
  if (!sameOrigin) return null;
  const content = document.querySelector('meta[name="qing-b2b-settings"]')?.content;
  if (!content) return null;
  try {
    return JSON.parse(content);
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(body, response) {
    super(body?.message || response.statusText);
    this.code = body?.code;
    this.details = body?.details || [];
    this.status = response.status;
  }
}

export async function request(path, { data, token, method, signal, binary = false } = {}) {
  const headers = { "X-B2B-Language": document.documentElement.lang || "en" };
  if (token) headers.Authorization = "Bearer " + token;
  if (data !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(new URL(path, api), {
    method: method || (data === undefined ? "GET" : "POST"),
    headers,
    body: data === undefined ? undefined : JSON.stringify(data),
    credentials: "omit",
    signal,
  });
  if (binary && response.ok) return response.blob();
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.error, response);
  return body;
}

export async function nativeDevice(remember) {
  const session = await fetch(absolute("/b2b/native/session"), { credentials: "same-origin" });
  if (!session.ok || session.redirected) return null;
  const { csrf_token } = await session.json();
  const result = await fetch(absolute("/b2b/native/device"), {
    credentials: "same-origin",
    method: "POST",
    body: new URLSearchParams({ csrf_token, remember }),
  });
  if (!result.ok) throw new ApiError(null, result);
  return result.json();
}
