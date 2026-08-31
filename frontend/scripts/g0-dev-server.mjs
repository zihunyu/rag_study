import { createReadStream } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const port = Number.parseInt(process.env.PORT ?? "5173", 10);

createServer((request, response) => {
  if (request.url !== "/" && request.url !== "/index.html") {
    response.writeHead(404).end("Not found");
    return;
  }
  response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  createReadStream(join(root, "index.html")).pipe(response);
}).listen(port, "127.0.0.1", () => {
  console.log(`G0 frontend stub listening on http://127.0.0.1:${port}`);
});
