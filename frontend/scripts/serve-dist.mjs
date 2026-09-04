import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const port = Number.parseInt(process.env.PORT || "8080", 10);
const apiBaseUrl = process.env.FRONTEND_API_BASE_URL?.trim();
if (!apiBaseUrl) throw new Error("FRONTEND_API_BASE_URL_REQUIRED");

const apiUrl = new URL(apiBaseUrl);
if (apiUrl.protocol !== "https:" && !["127.0.0.1", "localhost", "::1"].includes(apiUrl.hostname)) {
  throw new Error("FRONTEND_API_BASE_URL_HTTPS_REQUIRED");
}

const oidcEnabled = process.env.FRONTEND_OIDC_ENABLED === "true";
const publicOrigin = process.env.FRONTEND_PUBLIC_ORIGIN?.trim() || "";
const oidc = {
  enabled: oidcEnabled,
  authority: process.env.FRONTEND_OIDC_AUTHORITY?.trim() || "",
  clientId: process.env.FRONTEND_OIDC_CLIENT_ID?.trim() || "",
  redirectUri:
    process.env.FRONTEND_OIDC_REDIRECT_URI?.trim() ||
    (publicOrigin ? `${publicOrigin.replace(/\/$/, "")}/auth/callback` : ""),
  postLogoutRedirectUri:
    process.env.FRONTEND_OIDC_POST_LOGOUT_REDIRECT_URI?.trim() || publicOrigin,
  scope: process.env.FRONTEND_OIDC_SCOPE?.trim() || "openid profile email",
};
if (oidcEnabled && (!oidc.authority || !oidc.clientId || !oidc.redirectUri)) {
  throw new Error("FRONTEND_OIDC_CONFIGURATION_INCOMPLETE");
}
if (oidcEnabled) {
  const authority = new URL(oidc.authority);
  const redirect = new URL(oidc.redirectUri);
  const allowedLocalHosts = ["127.0.0.1", "localhost", "::1"];
  if (authority.protocol !== "https:" && !allowedLocalHosts.includes(authority.hostname)) {
    throw new Error("FRONTEND_OIDC_AUTHORITY_HTTPS_REQUIRED");
  }
  if (publicOrigin && redirect.origin !== new URL(publicOrigin).origin) {
    throw new Error("FRONTEND_OIDC_REDIRECT_ORIGIN_MISMATCH");
  }
}

const runtimeConfig = JSON.stringify({ apiBaseUrl, oidc })
  .replaceAll("<", "\\u003c")
  .replaceAll("\u2028", "\\u2028")
  .replaceAll("\u2029", "\\u2029");
const mimeTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".woff2", "font/woff2"],
]);

function sendFile(response, target) {
  response.writeHead(200, {
    "Content-Type": mimeTypes.get(path.extname(target)) || "application/octet-stream",
    "Cache-Control": target.endsWith("index.html") ? "no-store" : "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  createReadStream(target).pipe(response);
}

createServer((request, response) => {
  if (!["GET", "HEAD"].includes(request.method || "GET")) {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end();
    return;
  }
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(request.url || "/", "http://localhost").pathname);
  } catch {
    response.writeHead(400);
    response.end();
    return;
  }
  if (pathname === "/health") {
    response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    response.end('{"status":"ok"}');
    return;
  }
  if (pathname === "/runtime-config.js") {
    response.writeHead(200, {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(`globalThis.__RAGKB_CONFIG__=${runtimeConfig};`);
    return;
  }
  const candidate = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  const insideRoot = candidate === root || candidate.startsWith(`${root}${path.sep}`);
  try {
    if (insideRoot && statSync(candidate).isFile()) {
      sendFile(response, candidate);
      return;
    }
  } catch {
    // Unknown browser routes fall through to the SPA entry point.
  }
  sendFile(response, path.join(root, "index.html"));
}).listen(port, "0.0.0.0", () => {
  console.log(`frontend_static_server_ready port=${port}`);
});
