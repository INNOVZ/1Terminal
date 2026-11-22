import crypto from "crypto";
import type { NextRequest } from "next/server";

export const OPERATOR_COOKIE_NAME = "atlas_operator_session";
export const OPERATOR_CSRF_HEADER = "x-atlas-operator-request";
export const OPERATOR_SESSION_TTL_SECONDS = 12 * 60 * 60; // 12 hours

const SESSION_DOMAIN_PREFIX = "atlas-operator-session-v1";
const MAX_CLOCK_SKEW_SECONDS = 30;
const TRUTHY_FLAGS = new Set(["1", "true", "t", "yes", "y", "on"]);

export type OperatorAuthResult =
  | { ok: true; method: "bearer" | "session_cookie"; upstreamAuthorization: string }
  | { ok: false; status: 401 | 403 | 503; error: string };

export function isTruthyQueryFlag(raw: string | null): boolean {
  if (!raw) return false;
  return TRUTHY_FLAGS.has(raw.trim().toLowerCase());
}

export function getConfiguredOperatorToken(): string | null {
  const token = process.env.ATLAS_OPERATOR_TOKEN?.trim();
  return token && token.length > 0 ? token : null;
}

export function constantTimeEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  if (bufA.length !== bufB.length) {
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

export function verifyOperatorSecret(candidate: string, configuredToken: string): boolean {
  if (!candidate || !configuredToken) return false;
  return constantTimeEqual(candidate, configuredToken);
}

export function verifyBearerHeader(
  headerValue: string | null,
  configuredToken: string,
): boolean {
  if (!headerValue || !configuredToken) return false;
  return constantTimeEqual(headerValue, `Bearer ${configuredToken}`);
}

function signSessionPayload(payload: string, secret: string): string {
  return crypto
    .createHmac("sha256", secret)
    .update(`${SESSION_DOMAIN_PREFIX}:${payload}`)
    .digest("hex");
}

export function createOperatorSessionToken(
  configuredToken: string,
  nowSec: number = Math.floor(Date.now() / 1000),
): string {
  const issued = Math.floor(nowSec);
  const expires = issued + OPERATOR_SESSION_TTL_SECONDS;
  const nonce = crypto.randomBytes(16).toString("hex");
  const payload = `${issued}.${expires}.${nonce}`;
  const sig = signSessionPayload(payload, configuredToken);
  return `${payload}.${sig}`;
}

export function verifyOperatorSessionToken(
  cookieValue: string | null | undefined,
  configuredToken: string,
  nowSec: number = Math.floor(Date.now() / 1000),
): boolean {
  if (!cookieValue || !configuredToken) return false;
  const parts = cookieValue.split(".");
  if (parts.length !== 4) return false;
  const [issuedRaw, expiresRaw, nonce, providedSig] = parts;
  if (!/^\d+$/.test(issuedRaw) || !/^\d+$/.test(expiresRaw)) return false;
  if (!/^[a-f0-9]{32}$/i.test(nonce) || !/^[a-f0-9]{64}$/i.test(providedSig)) {
    return false;
  }
  const issued = Number(issuedRaw);
  const expires = Number(expiresRaw);
  if (
    !Number.isSafeInteger(issued) ||
    !Number.isSafeInteger(expires) ||
    expires <= issued ||
    expires - issued > OPERATOR_SESSION_TTL_SECONDS
  ) {
    return false;
  }
  if (issued > nowSec + MAX_CLOCK_SKEW_SECONDS || nowSec >= expires) {
    return false;
  }
  const payload = `${issuedRaw}.${expiresRaw}.${nonce}`;
  const expectedSig = signSessionPayload(payload, configuredToken);
  return constantTimeEqual(providedSig.toLowerCase(), expectedSig.toLowerCase());
}

export function validateOperatorCsrf(request: NextRequest): boolean {
  if (request.headers.get(OPERATOR_CSRF_HEADER) !== "1") {
    return false;
  }
  const expectedOrigin = request.nextUrl?.origin;
  const origin = request.headers.get("origin");
  if (origin && expectedOrigin && origin !== expectedOrigin) {
    return false;
  }
  const referer = request.headers.get("referer");
  if (!origin && referer && expectedOrigin) {
    try {
      if (new URL(referer).origin !== expectedOrigin) {
        return false;
      }
    } catch {
      return false;
    }
  }
  return true;
}

export function authorizeOperatorProxyRequest(request: NextRequest): OperatorAuthResult {
  const configuredToken = getConfiguredOperatorToken();
  if (!configuredToken) {
    return {
      ok: false,
      status: 503,
      error: "Operator authentication is not configured",
    };
  }

  const incomingAuth = request.headers.get("authorization");
  if (incomingAuth !== null) {
    if (verifyBearerHeader(incomingAuth, configuredToken)) {
      return {
        ok: true,
        method: "bearer",
        upstreamAuthorization: `Bearer ${configuredToken}`,
      };
    }
    return {
      ok: false,
      status: 401,
      error: "Invalid operator credentials",
    };
  }

  const sessionCookie = request.cookies?.get(OPERATOR_COOKIE_NAME)?.value;
  if (sessionCookie && verifyOperatorSessionToken(sessionCookie, configuredToken)) {
    if (!validateOperatorCsrf(request)) {
      return {
        ok: false,
        status: 403,
        error: "CSRF validation failed for operator session",
      };
    }
    return {
      ok: true,
      method: "session_cookie",
      upstreamAuthorization: `Bearer ${configuredToken}`,
    };
  }

  return {
    ok: false,
    status: 401,
    error: "Operator authentication required",
  };
}

