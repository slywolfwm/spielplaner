import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";


test("proxy forwards paths and rewrites the public origin", async () => {
  const originalFetch = globalThis.fetch;
  const forwardedRequests = [];
  globalThis.fetch = async (request) => {
    forwardedRequests.push(request);
    if (new URL(request.url).pathname === "/redirect") {
      return new Response(null, {
        status: 302,
        headers: {
          Location:
            "https://spielplaner-handamball.streamlit.app/~/+/spieldauern?__cf_access_jwt=should-not-leak",
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
      new Request(
        "https://spielplaner.handamball.de/mannschaftspaare?team=1&__cf_access_jwt=forged",
        {
          headers: {
            Origin: "https://spielplaner.handamball.de",
            "Cf-Access-Jwt-Assertion": "signed-access-token",
          },
        },
      ),
      env,
    );

    assert.equal(await response.text(), "ok");
    assert.equal(
      forwardedRequests[0].url,
      "https://spielplaner-handamball.streamlit.app/mannschaftspaare?team=1",
    );
    assert.equal(
      forwardedRequests[0].headers.get("Origin"),
      "https://spielplaner-handamball.streamlit.app",
    );
    assert.equal(
      forwardedRequests[0].headers.get("X-Forwarded-Host"),
      "spielplaner-handamball.streamlit.app",
    );
    assert.equal(
      forwardedRequests[0].headers.get("Cf-Access-Jwt-Assertion"),
      "signed-access-token",
    );

    await worker.fetch(
      new Request("https://spielplaner.handamball.de/_stcore/stream", {
        headers: {
          Cookie: "Spielplaner_Access_Proof=encrypted-proof",
        },
      }),
      env,
    );
    assert.equal(
      forwardedRequests[1].headers.get("X-Spielplaner-Access-Proof"),
      "encrypted-proof",
    );
    assert.equal(forwardedRequests[1].headers.get("Referer"), null);

    const redirect = await worker.fetch(
      new Request("https://spielplaner.handamball.de/redirect"),
      env,
    );
    assert.equal(
      redirect.headers.get("Location"),
      "https://spielplaner.handamball.de/spieldauern",
    );

    const authenticatedRedirect = await worker.fetch(
      new Request("https://spielplaner.handamball.de/redirect", {
        headers: {
          Cookie: "Spielplaner_Access_Proof=encrypted-proof",
        },
      }),
      env,
    );
    assert.equal(
      new URL(authenticatedRedirect.headers.get("Location")).searchParams.get(
        "__access_proof",
      ),
      "encrypted-proof",
    );

    globalThis.fetch = async (request) => {
      forwardedRequests.push(request);
      return new Response(null, {
        status: 303,
        headers: [
          [
            "Location",
            "https://spielplaner-handamball.streamlit.app/-/login?code=test",
          ],
          [
            "Set-Cookie",
            "streamlit_session=test; Domain=.streamlit.io; Path=/; Secure; HttpOnly",
          ],
        ],
      });
    };
    const authRedirect = await worker.fetch(
      new Request(
        "https://spielplaner.handamball.de/-/auth/app?__access_proof=encrypted-proof",
        {
          headers: {
            Cookie: "Spielplaner_Access_Proof=encrypted-proof",
          },
        },
      ),
      env,
    );
    assert.equal(
      forwardedRequests.at(-1).url,
      "https://share.streamlit.io/-/auth/app?__access_proof=encrypted-proof",
    );
    assert.equal(
      new URL(authRedirect.headers.get("Location")).origin,
      "https://spielplaner.handamball.de",
    );
    assert.equal(
      authRedirect.headers.get("Set-Cookie").includes("Domain="),
      false,
    );
    assert.equal(
      authRedirect.headers.get("Set-Cookie").startsWith(
        "__sp_auth_streamlit_session=",
      ),
      true,
    );

    globalThis.fetch = async (request) => {
      forwardedRequests.push(request);
      return new Response(
        'import "./subdomain.js"; const host = window.location.hostname;',
        {
          headers: {
            "Content-Type": "application/javascript",
          },
        },
      );
    };
    const routerScript = await worker.fetch(
      new Request(
        "https://spielplaner.handamball.de/-/build/assets/router.js",
      ),
      env,
    );
    assert.equal(
      await routerScript.text(),
      'import "./subdomain.js?__sp_router=3"; const host = "spielplaner-handamball.streamlit.app";',
    );

    globalThis.fetch = async (request) => {
      forwardedRequests.push(request);
      return new Response(
        '<script type="module" src="/-/build/assets/index.js"></script>',
        {
          headers: {
            "Content-Type": "text/html; charset=utf-8",
          },
        },
      );
    };
    const shell = await worker.fetch(
      new Request("https://spielplaner.handamball.de/"),
      env,
    );
    assert.equal(
      await shell.text(),
      '<script type="module" src="/-/build/assets/index.js?__sp_router=3"></script>',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
