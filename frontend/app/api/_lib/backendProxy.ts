import { NextRequest, NextResponse } from "next/server";
import { authorizeOperatorProxyRequest } from "./operatorAuth";

const NO_STORE_HEADERS = {
  "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
  pragma: "no-cache",
};

function getBackendUrl(): string | undefined {
  const requireHttps = process.env.ATLAS_REQUIRE_HTTPS_BACKEND === "true";
  return (
    process.env.API_BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === "production"
      ? requireHttps
        ? undefined
        : "http://34.51.164.30:8000"
      : "http://127.0.0.1:8000")
  );
}

export async function proxyToBackend(
  request: NextRequest,
  backendPath: string,
  options: { attachOperatorAuth?: boolean } = {},
): Promise<NextResponse> {
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  if (options.attachOperatorAuth) {
    const auth = authorizeOperatorProxyRequest(request);
    if (!auth.ok) {
      return NextResponse.json(
        { detail: auth.error },
        { status: auth.status, headers: NO_STORE_HEADERS },
      );
    }
    headers.set("authorization", auth.upstreamAuthorization);
  } else {
    const incomingAuth = request.headers.get("authorization");
    if (incomingAuth) {
      headers.set("authorization", incomingAuth);
    }
  }

  let targetUrl: URL;
  try {
    const backendUrl = getBackendUrl();
    if (!backendUrl) throw new Error("Missing upstream");
    const base = new URL(backendUrl);
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) {
      throw new Error("Invalid upstream");
    }
    targetUrl = new URL(backendPath, base);
    if (targetUrl.origin !== base.origin) throw new Error("Invalid upstream path");
    if (
      process.env.NODE_ENV === "production" &&
      process.env.ATLAS_REQUIRE_HTTPS_BACKEND === "true" &&
      targetUrl.protocol !== "https:"
    ) {
      throw new Error("Insecure production transport");
    }
  } catch {
    return NextResponse.json(
      { detail: "Backend transport is not securely configured" },
      { status: 503, headers: NO_STORE_HEADERS },
    );
  }
  targetUrl.search = request.nextUrl.search;

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
    redirect: "error",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
  }

  try {
    const upstream = await fetch(targetUrl.toString(), init);
    const body = await upstream.text();
    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json",
        ...NO_STORE_HEADERS,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Backend request unavailable" },
      { status: 502, headers: NO_STORE_HEADERS },
    );
  }
}
