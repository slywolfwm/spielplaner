import { createRemoteJWKSet, jwtVerify } from "jose";


const jwksByTeam = new Map();
const ACCESS_PROOF_COOKIE = "Spielplaner_Access_Proof";


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


export default {
  async fetch(request, env) {
    const publicUrl = new URL(request.url);
    const upstreamOrigin = new URL(env.UPSTREAM_ORIGIN);
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

    const upstreamPath = `/~/+${publicUrl.pathname}`;
    const upstreamUrl = new URL(upstreamPath, upstreamOrigin);
    for (const [name, value] of publicUrl.searchParams) {
      if (name !== "__cf_access_jwt") {
        upstreamUrl.searchParams.append(name, value);
      }
    }
    const upstreamRequest = new Request(upstreamUrl, request);

    if (upstreamRequest.headers.get("Origin") === publicUrl.origin) {
      upstreamRequest.headers.set("Origin", upstreamOrigin.origin);
    }
    upstreamRequest.headers.set("X-Forwarded-Host", publicUrl.host);
    upstreamRequest.headers.set(
      "X-Forwarded-Proto",
      publicUrl.protocol.replace(":", ""),
    );
    if (accessJwt) {
      upstreamRequest.headers.set("Cf-Access-Jwt-Assertion", accessJwt);
    }
    if (accessProof) {
      upstreamRequest.headers.set("X-Spielplaner-Access-Proof", accessProof);
      const proofReferer = new URL(publicUrl.origin);
      proofReferer.searchParams.set("__access_proof", accessProof);
      upstreamRequest.headers.set("Referer", proofReferer.toString());
    }

    const upstreamResponse = await fetch(upstreamRequest);
    if (upstreamResponse.status === 101) {
      return upstreamResponse;
    }

    const response = new Response(upstreamResponse.body, upstreamResponse);
    const location = response.headers.get("Location");
    if (location) {
      const locationUrl = new URL(location, upstreamOrigin);
      if (locationUrl.origin === upstreamOrigin.origin) {
        locationUrl.pathname =
          locationUrl.pathname.replace(/^\/~\/\+/, "") || "/";
        locationUrl.searchParams.delete("__cf_access_jwt");
        locationUrl.searchParams.delete("__access_proof");
        locationUrl.protocol = publicUrl.protocol;
        locationUrl.host = publicUrl.host;
        response.headers.set("Location", locationUrl.toString());
      }
    }

    if (
      response.headers.get("Access-Control-Allow-Origin") === upstreamOrigin.origin
    ) {
      response.headers.set("Access-Control-Allow-Origin", publicUrl.origin);
    }
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  },
};
