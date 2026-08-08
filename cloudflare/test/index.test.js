import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";


test("proxy forwards paths and preserves the public origin", async () => {
  const originalFetch = globalThis.fetch;
  const forwardedRequests = [];
  globalThis.fetch = async (request) => {
    forwardedRequests.push(request);
    if (new URL(request.url).pathname === "/~/+/redirect") {
      return new Response(null, {
        status: 302,
        headers: {
          Location: "https://spielplaner-handamball.streamlit.app/~/+/spieldauern",
        },
      });
    }
    return new Response("ok");
  };

  try {
    const env = {
      UPSTREAM_ORIGIN: "https://spielplaner-handamball.streamlit.app",
    };
    const response = await worker.fetch(
      new Request("https://spielplaner.handamball.de/mannschaftspaare?team=1", {
        headers: { Origin: "https://spielplaner.handamball.de" },
      }),
      env,
    );

    assert.equal(await response.text(), "ok");
    assert.equal(
      forwardedRequests[0].url,
      "https://spielplaner-handamball.streamlit.app/~/+/mannschaftspaare?team=1",
    );
    assert.equal(
      forwardedRequests[0].headers.get("Origin"),
      "https://spielplaner.handamball.de",
    );
    assert.equal(
      forwardedRequests[0].headers.get("X-Forwarded-Host"),
      "spielplaner.handamball.de",
    );

    const redirect = await worker.fetch(
      new Request("https://spielplaner.handamball.de/redirect"),
      env,
    );
    assert.equal(
      redirect.headers.get("Location"),
      "https://spielplaner.handamball.de/spieldauern",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
