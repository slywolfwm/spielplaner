export default {
  async fetch(request, env) {
    const publicUrl = new URL(request.url);
    const upstreamOrigin = new URL(env.UPSTREAM_ORIGIN);
    const upstreamPath = `/~/+${publicUrl.pathname}`;
    const upstreamUrl = new URL(upstreamPath + publicUrl.search, upstreamOrigin);
    const upstreamRequest = new Request(upstreamUrl, request);

    if (upstreamRequest.headers.get("Origin") === publicUrl.origin) {
      upstreamRequest.headers.set("Origin", upstreamOrigin.origin);
    }
    upstreamRequest.headers.set("X-Forwarded-Host", publicUrl.host);
    upstreamRequest.headers.set(
      "X-Forwarded-Proto",
      publicUrl.protocol.replace(":", ""),
    );
    const accessJwt = request.headers.get("Cf-Access-Jwt-Assertion");
    if (accessJwt) {
      upstreamRequest.headers.set("Cf-Access-Jwt-Assertion", accessJwt);
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
    return response;
  },
};
