import { NextRequest, NextResponse } from "next/server";
import { authorizeOperatorProxyRequest } from "./operatorAuth";

const NO_STORE_HEADERS = {
  "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
  pragma: "no-cache",
};

function getBackendUrl(): string {
  return (
    process.env.API_BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === "production"
      ? "http://34.51.164.30:8000"
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

  const targetUrl = new URL(backendPath, getBackendUrl());
  targetUrl.search = request.nextUrl.search;

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
  }

  const upstream = await fetch(targetUrl.toString(), init);
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json",
      ...NO_STORE_HEADERS,
    },
  });
}
