import test from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import { createAppServer } from "../src/server.js";

async function withServer(run) {
  const server = await createAppServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const address = server.address();
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    server.close();
    await once(server, "close");
  }
}

test("health endpoint reports service readiness", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "ok", service: "ryderesolve" });
  });
});

test("route deviation endpoint validates malformed input", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/disputes/route-deviation`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ case_id: "bad" })
    });
    assert.equal(response.status, 400);
    const body = await response.json();
    assert.equal(body.error, "Invalid route deviation dispute");
    assert.ok(body.details.length > 0);
  });
});
