import { NextRequest, NextResponse } from "next/server";
import {
  OPERATOR_COOKIE_NAME,
  OPERATOR_SESSION_TTL_SECONDS,
  createOperatorSessionToken,
  getConfiguredOperatorToken,
  validateOperatorCsrf,
  verifyOperatorSecret,
  verifyOperatorSessionToken,
} from "../../_lib/operatorAuth";

const NO_STORE_HEADERS = {
  "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
  pragma: "no-cache",
};

export async function GET(request: NextRequest) {
  const configuredToken = getConfiguredOperatorToken();
  if (!configuredToken) {
    return NextResponse.json(
      { configured: false, authenticated: false },
      { status: 200, headers: NO_STORE_HEADERS },
    );
  }
  const sessionCookie = request.cookies.get(OPERATOR_COOKIE_NAME)?.value;
  const authenticated = verifyOperatorSessionToken(sessionCookie, configuredToken);
  return NextResponse.json(
    { configured: true, authenticated },
    { status: 200, headers: NO_STORE_HEADERS },
  );
}

export async function POST(request: NextRequest) {
  const configuredToken = getConfiguredOperatorToken();
  if (!configuredToken) {
    return NextResponse.json(
      { detail: "Operator authentication is not configured" },
      { status: 503, headers: NO_STORE_HEADERS },
    );
  }
  if (!validateOperatorCsrf(request)) {
    return NextResponse.json(
      { detail: "CSRF validation failed" },
      { status: 403, headers: NO_STORE_HEADERS },
    );
  }

  let suppliedToken = "";
  try {
    const payload = (await request.json()) as { token?: unknown };
    suppliedToken = typeof payload?.token === "string" ? payload.token.trim() : "";
  } catch {
    return NextResponse.json(
      { detail: "Invalid JSON payload" },
      { status: 400, headers: NO_STORE_HEADERS },
    );
  }

  if (!verifyOperatorSecret(suppliedToken, configuredToken)) {
    return NextResponse.json(
      { detail: "Invalid operator token" },
      { status: 401, headers: NO_STORE_HEADERS },
    );
  }

  const sessionToken = createOperatorSessionToken(configuredToken);
  const response = NextResponse.json(
    { authenticated: true, ttl_seconds: OPERATOR_SESSION_TTL_SECONDS },
    { status: 200, headers: NO_STORE_HEADERS },
  );
  response.cookies.set(OPERATOR_COOKIE_NAME, sessionToken, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: OPERATOR_SESSION_TTL_SECONDS,
  });
  return response;
}

export async function DELETE(request: NextRequest) {
  if (!validateOperatorCsrf(request)) {
    return NextResponse.json(
      { detail: "CSRF validation failed" },
      { status: 403, headers: NO_STORE_HEADERS },
    );
  }
  const response = NextResponse.json(
    { authenticated: false },
    { status: 200, headers: NO_STORE_HEADERS },
  );
  response.cookies.set(OPERATOR_COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return response;
}

