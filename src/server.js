import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { RouteDeviationDisputePipeline } from "./pipeline.js";
import { ValidationError } from "./contracts.js";

const here = dirname(fileURLToPath(import.meta.url));

export async function loadPolicy() {
  const raw = await readFile(join(here, "..", "policies", "route-deviation.v1.json"), "utf8");
  return JSON.parse(raw);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1_000_000) throw new ValidationError("Request body exceeds 1 MB");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new ValidationError("Request body must be valid JSON");
  }
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

export async function createAppServer() {
  const policy = await loadPolicy();
  const pipeline = new RouteDeviationDisputePipeline({ policy });

  return createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/health") {
        sendJson(response, 200, { status: "ok", service: "ryderesolve" });
        return;
      }
      if (request.method === "POST" && request.url === "/api/disputes/route-deviation") {
        const input = await readJsonBody(request);
        const resolution = await pipeline.resolve(input);
        sendJson(response, 200, resolution);
        return;
      }
      sendJson(response, 404, { error: "NOT_FOUND" });
    } catch (error) {
      if (error instanceof ValidationError) {
        sendJson(response, 400, { error: error.message, details: error.details });
        return;
      }
      console.error(error);
      sendJson(response, 500, { error: "INTERNAL_SERVER_ERROR" });
    }
  });
}

const invokedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
  const server = await createAppServer();
  const port = Number(process.env.PORT ?? 3000);
  server.listen(port, "127.0.0.1", () => {
    console.log(`RydeResolve listening on http://127.0.0.1:${port}`);
  });
}
