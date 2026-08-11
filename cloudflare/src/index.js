import { createRemoteJWKSet, jwtVerify } from "jose";


const jwksByTeam = new Map();
const ACCESS_PROOF_COOKIE = "Spielplaner_Access_Proof";
const STREAMLIT_AUTH_ORIGIN = "https://share.streamlit.io";
const STREAMLIT_APP_COOKIE_PREFIX = "__sp_app_";
const STREAMLIT_AUTH_COOKIE_PREFIX = "__sp_auth_";


function getJwks(teamDomain) {
  if (!jwksByTeam.has(teamDomain)) {
    jwksByTeam.set(
      teamDomain,
      createRemoteJWKSet(new URL(`${teamDomain}/cdn-cgi/access/certs`)),
    );
  }
  return jwksByTeam.get(teamDomain);
}


function decodeBase64Url(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}


function encodeBase64Url(value) {
  return btoa(String.fromCharCode(...value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}


async function createAccessProof(payload, secret) {
  const keyBytes = decodeBase64Url(secret);
  if (keyBytes.length !== 32 || !payload.email) {
    throw new Error("Access proof configuration or identity is invalid");
  }

  const rolesValue = payload.custom?.roles;
  const roles = Array.isArray(rolesValue)
    ? rolesValue.map(String)
    : rolesValue
      ? [String(rolesValue)]
      : [];
  const now = Math.floor(Date.now() / 1000);
  const expiresAt = Math.min(Number(payload.exp) || now + 3600, now + 3600);
  const plaintext = new TextEncoder().encode(
    JSON.stringify({
      type: "app",
      email: String(payload.email),
      sub: String(payload.sub || ""),
      custom: { roles },
      exp: expiresAt,
    }),
  );
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    "AES-GCM",
    false,
    ["encrypt"],
  );
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, plaintext),
  );
  const encrypted = new Uint8Array(nonce.length + ciphertext.length);
  encrypted.set(nonce);
  encrypted.set(ciphertext, nonce.length);
  return encodeBase64Url(encrypted);
}


function cookieValue(request, name) {
  const cookieHeader = request.headers.get("Cookie") || "";
  for (const part of cookieHeader.split(";")) {
    const [cookieName, ...valueParts] = part.trim().split("=");
    if (cookieName === name) {
      return valueParts.join("=");
    }
  }
  return "";
}


function setCookieValues(headers) {
  if (typeof headers.getSetCookie === "function") {
    return headers.getSetCookie();
  }
  if (typeof headers.getAll === "function") {
    return headers.getAll("Set-Cookie");
  }
  const value = headers.get("Set-Cookie");
  return value ? [value] : [];
}


function upstreamCookieHeader(request, prefix) {
  const cookieHeader = request.headers.get("Cookie") || "";
  const cookies = [];
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    const separator = trimmed.indexOf("=");
    if (separator < 1) {
      continue;
    }
    const name = trimmed.slice(0, separator);
    if (name.startsWith(prefix)) {
      cookies.push(`${name.slice(prefix.length)}${trimmed.slice(separator)}`);
    }
  }
  return cookies.join("; ");
}


function rewriteSetCookies(response, cookies, prefix) {
  if (!cookies.length) {
    return;
  }
  response.headers.delete("Set-Cookie");
  for (const cookie of cookies) {
    const separator = cookie.indexOf("=");
    if (separator < 1) {
      continue;
    }
    response.headers.append(
      "Set-Cookie",
      `${prefix}${cookie.slice(0, separator)}${cookie.slice(separator)}`.replace(
        /;\s*Domain=[^;]+/gi,
        "",
      ),
    );
  }
}


async function rewriteStreamlitRouterScript(
  response,
  publicUrl,
  upstreamOrigin,
) {
  const contentType = response.headers.get("Content-Type") || "";
  if (
    !publicUrl.pathname.startsWith("/-/build/assets/") ||
    !contentType.includes("javascript")
  ) {
    return response;
  }

  const script = await response.text();
  const rewritten = script
    .replaceAll(
      "window.location.hostname",
      JSON.stringify(upstreamOrigin.hostname),
    )
    .replace(
      /(["'])(\.\/|assets\/)([^"'?]+\.js)\1/g,
      '$1$2$3?__sp_router=3$1',
    );
  const headers = new Headers(response.headers);
  headers.delete("Content-Encoding");
  headers.delete("Content-Length");
  headers.delete("ETag");
  headers.set("Cache-Control", "no-store");
  return new Response(rewritten, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}


async function rewriteStreamlitShell(response) {
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.includes("text/html")) {
    return response;
  }

  const html = await response.text();
  const rewritten = html.replace(
    /src=(["'])(\/-\/build\/assets\/[^"']+\.js)\1/g,
    'src=$1$2?__sp_router=3$1',
  );
  const headers = new Headers(response.headers);
  headers.delete("Content-Encoding");
  headers.delete("Content-Length");
  headers.delete("ETag");
  headers.set("Cache-Control", "no-store");
  return new Response(rewritten, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}


export default {
  async fetch(request, env) {
    const publicUrl = new URL(request.url);
    const upstreamOrigin = new URL(env.UPSTREAM_ORIGIN);
    const streamlitAuthOrigin = new URL(STREAMLIT_AUTH_ORIGIN);
    const accessJwt = request.headers.get("Cf-Access-Jwt-Assertion");
    let accessProof = cookieValue(request, ACCESS_PROOF_COOKIE);
    const isDocument =
      request.method === "GET" &&
      (request.headers.get("Sec-Fetch-Dest") === "document" ||
        request.headers.get("Accept")?.includes("text/html"));

    if (accessJwt && env.ACCESS_PROXY_SECRET) {
      try {
        const teamDomain = env.TEAM_DOMAIN.replace(/\/$/, "");
        const { payload } = await jwtVerify(accessJwt, getJwks(teamDomain), {
          issuer: teamDomain,
          audience: env.POLICY_AUD,
        });
        accessProof = await createAccessProof(payload, env.ACCESS_PROXY_SECRET);
      } catch {
        if (isDocument) {
          return new Response("Access authentication failed", { status: 403 });
        }
      }
    }

    if (isDocument && !publicUrl.searchParams.has("__access_proof")) {
      if (!accessJwt || !env.ACCESS_PROXY_SECRET) {
        return new Response("Access authentication is incomplete", { status: 403 });
      }
      if (accessProof) {
        const redirectUrl = new URL(publicUrl);
        redirectUrl.searchParams.delete("__cf_access_jwt");
        redirectUrl.searchParams.set("__access_proof", accessProof);
        return new Response(null, {
          status: 302,
          headers: {
            Location: redirectUrl.toString(),
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Set-Cookie": `${ACCESS_PROOF_COOKIE}=${accessProof}; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Lax`,
          },
        });
      }
      return new Response("Access authentication failed", { status: 403 });
    }

    const targetOrigin = publicUrl.pathname.startsWith("/-/auth/")
      ? streamlitAuthOrigin
      : upstreamOrigin;
    const cookiePrefix = targetOrigin.origin === streamlitAuthOrigin.origin
      ? STREAMLIT_AUTH_COOKIE_PREFIX
      : STREAMLIT_APP_COOKIE_PREFIX;
    const upstreamUrl = new URL(publicUrl.pathname, targetOrigin);
    for (const [name, value] of publicUrl.searchParams) {
      if (name !== "__cf_access_jwt") {
        upstreamUrl.searchParams.append(name, value);
      }
    }
    const upstreamRequest = new Request(upstreamUrl, request);
    const upstreamCookies = upstreamCookieHeader(request, cookiePrefix);
    if (upstreamCookies) {
      upstreamRequest.headers.set("Cookie", upstreamCookies);
    } else {
      upstreamRequest.headers.delete("Cookie");
    }

    if (upstreamRequest.headers.get("Origin") === publicUrl.origin) {
      upstreamRequest.headers.set("Origin", targetOrigin.origin);
    }
    upstreamRequest.headers.set("X-Forwarded-Host", targetOrigin.host);
    upstreamRequest.headers.set(
      "X-Forwarded-Proto",
      publicUrl.protocol.replace(":", ""),
    );
    if (accessJwt) {
      upstreamRequest.headers.set("Cf-Access-Jwt-Assertion", accessJwt);
    }
    if (accessProof) {
      upstreamRequest.headers.set("X-Spielplaner-Access-Proof", accessProof);
    }

    const upstreamResponse = await fetch(upstreamRequest, {
      redirect: "manual",
    });
    if (upstreamResponse.status === 101) {
      return upstreamResponse;
    }

    const responseCookies = setCookieValues(upstreamResponse.headers);
    const response = new Response(upstreamResponse.body, upstreamResponse);
    rewriteSetCookies(response, responseCookies, cookiePrefix);
    const location = response.headers.get("Location");
    if (location) {
      const locationUrl = new URL(location, targetOrigin);
      if (
        locationUrl.origin === upstreamOrigin.origin ||
        locationUrl.origin === streamlitAuthOrigin.origin
      ) {
        locationUrl.pathname =
          locationUrl.pathname.replace(/^\/~\/\+/, "") || "/";
        locationUrl.searchParams.delete("__cf_access_jwt");
        if (accessProof) {
          locationUrl.searchParams.set("__access_proof", accessProof);
        } else {
          locationUrl.searchParams.delete("__access_proof");
        }
        locationUrl.protocol = publicUrl.protocol;
        locationUrl.host = publicUrl.host;
        response.headers.set("Location", locationUrl.toString());
      }
    }

    if (
      response.headers.get("Access-Control-Allow-Origin") === targetOrigin.origin
    ) {
      response.headers.set("Access-Control-Allow-Origin", publicUrl.origin);
    }
    response.headers.set("Referrer-Policy", "no-referrer");
    const shellResponse = await rewriteStreamlitShell(response);
    return rewriteStreamlitRouterScript(
      shellResponse,
      publicUrl,
      upstreamOrigin,
    );
  },
};
