import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const req = Module.createRequire(path.join(frontendRoot, "package.json"));
const ts = req("typescript");

// Compile TypeScript Next.js route modules in-memory without network or running server
const compileTs = (moduleObj, filename) => {
  const source = fs.readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  });
  moduleObj._compile(outputText, filename);
};
req.extensions[".ts"] = compileTs;

const { NextRequest } = req("next/server");
const operatorAuth = req(path.join(frontendRoot, "app/api/_lib/operatorAuth.ts"));
const sessionRoute = req(path.join(frontendRoot, "app/api/operator/session/route.ts"));
const liveRoute = req(path.join(frontendRoot, "app/api/screener/live/route.ts"));
const rescanRoute = req(path.join(frontendRoot, "app/api/screener/rescan/route.ts"));
const publicRoute = req(path.join(frontendRoot, "app/api/[...path]/route.ts"));
const shortWatchTickRoute = req(
  path.join(frontendRoot, "app/api/screener/short-watch/tick/route.ts"),
);
const watchlistRoute = req(path.join(frontendRoot, "app/api/watchlist/route.ts"));
const watchlistSymbolRoute = req(
  path.join(frontendRoot, "app/api/watchlist/[symbol]/route.ts"),
);
const watchlistNotesRoute = req(
  path.join(frontendRoot, "app/api/watchlist/[symbol]/notes/route.ts"),
);

const TEST_TOKEN = "test-operator-secret-token-for-unit-tests-only-123456";
const APP_ORIGIN = "https://atlas-test.invalid";

function setupFakeUpstream() {
  const calls = [];
  const origFetch = global.fetch;
  process.env.ATLAS_OPERATOR_TOKEN = TEST_TOKEN;
  process.env.API_BACKEND_URL = "https://upstream-test.invalid";
  global.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      method: init?.method || "GET",
      authorization: new Headers(init?.headers).get("authorization"),
      redirect: init?.redirect,
    });
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  return {
    calls,
    restore() {
      global.fetch = origFetch;
    },
  };
}

test("R1: anonymous requests to all privileged proxy routes fail 401 with ZERO upstream calls", async () => {
  const upstream = setupFakeUpstream();
  try {
    const responses = [
      await watchlistSymbolRoute.DELETE(
        new NextRequest(`${APP_ORIGIN}/api/watchlist/TESTUSDT`, { method: "DELETE" }),
        { params: Promise.resolve({ symbol: "TESTUSDT" }) },
      ),
      await watchlistRoute.POST(
        new NextRequest(`${APP_ORIGIN}/api/watchlist`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ symbol: "TESTUSDT" }),
        }),
      ),
      await watchlistNotesRoute.PATCH(
        new NextRequest(`${APP_ORIGIN}/api/watchlist/TESTUSDT/notes`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ notes: "anon" }),
        }),
        { params: Promise.resolve({ symbol: "TESTUSDT" }) },
      ),
      await rescanRoute.POST(
        new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, { method: "POST" }),
      ),
      await shortWatchTickRoute.POST(
        new NextRequest(`${APP_ORIGIN}/api/screener/short-watch/tick`, { method: "POST" }),
      ),
      await liveRoute.GET(
        new NextRequest(`${APP_ORIGIN}/api/screener/live?refresh=true`, { method: "GET" }),
      ),
      await liveRoute.GET(
        new NextRequest(`${APP_ORIGIN}/api/screener/live?refresh=1`, { method: "GET" }),
      ),
    ];

    for (const res of responses) {
      assert.equal(res.status, 401);
    }
    assert.equal(upstream.calls.length, 0);
  } finally {
    upstream.restore();
  }
});

test("production credential transport rejects missing/insecure upstream and prevents redirects", async () => {
  const upstream = setupFakeUpstream();
  const previousMode = process.env.NODE_ENV;
  const previousPublic = process.env.NEXT_PUBLIC_API_URL;
  const previousRequireHttps = process.env.ATLAS_REQUIRE_HTTPS_BACKEND;
  process.env.NODE_ENV = "production";
  process.env.ATLAS_REQUIRE_HTTPS_BACKEND = "true";
  delete process.env.NEXT_PUBLIC_API_URL;
  const request = () => new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, {
    method: "POST", headers: { authorization: `Bearer ${TEST_TOKEN}` },
  });
  try {
    for (const url of [undefined, "http://34.51.164.30:8000", "ftp://upstream.invalid", "https://user:password@upstream.invalid"]) {
      if (url) process.env.API_BACKEND_URL = url;
      else delete process.env.API_BACKEND_URL;
      assert.equal((await rescanRoute.POST(request())).status, 503);
    }
    // Forwarded client Bearers on otherwise public reads need the same transport protection.
    process.env.API_BACKEND_URL = "http://upstream.invalid";
    assert.equal((await liveRoute.GET(new NextRequest(`${APP_ORIGIN}/api/screener/live`, {
      headers: { authorization: `Bearer ${TEST_TOKEN}` },
    }))).status, 503);
    assert.equal(upstream.calls.length, 0);
    process.env.API_BACKEND_URL = "https://upstream-test.invalid";
    assert.equal((await rescanRoute.POST(request())).status, 200);
    assert.equal(upstream.calls.length, 1);
    assert.equal(upstream.calls[0].authorization, `Bearer ${TEST_TOKEN}`);
    assert.equal(upstream.calls[0].redirect, "error");
    // Actual fetch rejects redirects with this mode; sanitize that transport failure.
    global.fetch = async () => { throw new TypeError("fetch redirect refused"); };
    const response = await rescanRoute.POST(request());
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes(TEST_TOKEN));
  } finally {
    if (previousMode === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousMode;
    if (previousPublic === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previousPublic;
    if (previousRequireHttps === undefined) delete process.env.ATLAS_REQUIRE_HTTPS_BACKEND;
    else process.env.ATLAS_REQUIRE_HTTPS_BACKEND = previousRequireHttps;
    process.env.API_BACKEND_URL = "https://upstream-test.invalid";
    upstream.restore();
  }
});

test("public chart/macro routes use the shared transport boundary without a rewrite bypass", async () => {
  const upstream = setupFakeUpstream();
  const previousMode = process.env.NODE_ENV;
  const previousRequireHttps = process.env.ATLAS_REQUIRE_HTTPS_BACKEND;
  process.env.NODE_ENV = "production";
  process.env.ATLAS_REQUIRE_HTTPS_BACKEND = "true";
  try {
    for (const routePath of [["chart", "TESTUSDT"], ["chart", "币安人生USDT"], ["macro"], ["screener", "cron", "status"]]) {
      const response = await publicRoute.GET(new NextRequest(`${APP_ORIGIN}/api/${routePath.join("/")}`), {
        params: Promise.resolve({ path: routePath }),
      });
      assert.equal(response.status, 200);
    }
    const auditRoute = req(path.join(frontendRoot, "app/api/watchlist/audit/route.ts"));
    assert.equal((await auditRoute.GET(new NextRequest(`${APP_ORIGIN}/api/watchlist/audit`))).status, 200);
    assert.ok(upstream.calls.every(call => call.authorization === null && call.redirect === "error"));
    upstream.calls.length = 0;
    process.env.API_BACKEND_URL = "http://upstream.invalid";
    assert.equal((await publicRoute.GET(new NextRequest(`${APP_ORIGIN}/api/chart/TESTUSDT`), {
      params: Promise.resolve({ path: ["chart", "TESTUSDT"] }),
    })).status, 503);
    const response = await publicRoute.GET(new NextRequest(`${APP_ORIGIN}/api/chart/TESTUSDT`, {
      headers: { authorization: `Bearer ${TEST_TOKEN}` },
    }), { params: Promise.resolve({ path: ["chart", "TESTUSDT"] }) });
    assert.equal(response.status, 503);
    const unknown = await publicRoute.GET(new NextRequest(`${APP_ORIGIN}/api/admin`), {
      params: Promise.resolve({ path: ["admin"] }),
    });
    assert.equal(unknown.status, 404);
    assert.equal(upstream.calls.length, 0);
    const nextConfig = req(path.join(frontendRoot, "next.config.ts")).default;
    assert.ok(!(await nextConfig.rewrites()).some(rule => rule.source === "/api/:path*"));
  } finally {
    if (previousMode === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousMode;
    if (previousRequireHttps === undefined) delete process.env.ATLAS_REQUIRE_HTTPS_BACKEND;
    else process.env.ATLAS_REQUIRE_HTTPS_BACKEND = previousRequireHttps;
    process.env.API_BACKEND_URL = "https://upstream-test.invalid";
    upstream.restore();
  }
});

test("R1: unconfigured ATLAS_OPERATOR_TOKEN fails closed with 503 and ZERO upstream calls", async () => {
  const upstream = setupFakeUpstream();
  delete process.env.ATLAS_OPERATOR_TOKEN;
  try {
    const res = await rescanRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, { method: "POST" }),
    );
    assert.equal(res.status, 503);
    assert.equal(upstream.calls.length, 0);
  } finally {
    upstream.restore();
  }
});

test("R1: invalid Bearer, tampered cookie, and expired cookie are all rejected 401 with ZERO upstream calls", async () => {
  const upstream = setupFakeUpstream();
  try {
    const badBearer = await rescanRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, {
        method: "POST",
        headers: { authorization: "Bearer wrong-secret" },
      }),
    );
    assert.equal(badBearer.status, 401);

    const validCookie = operatorAuth.createOperatorSessionToken(TEST_TOKEN);
    const tamperedCookie = `${validCookie.slice(0, -1)}${validCookie.endsWith("a") ? "b" : "a"}`;
    const tamperedRes = await rescanRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, {
        method: "POST",
        headers: {
          cookie: `${operatorAuth.OPERATOR_COOKIE_NAME}=${tamperedCookie}`,
          "x-atlas-operator-request": "1",
        },
      }),
    );
    assert.equal(tamperedRes.status, 401);

    const expiredCookie = operatorAuth.createOperatorSessionToken(
      TEST_TOKEN,
      Math.floor(Date.now() / 1000) - operatorAuth.OPERATOR_SESSION_TTL_SECONDS - 60,
    );
    const expiredRes = await rescanRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, {
        method: "POST",
        headers: {
          cookie: `${operatorAuth.OPERATOR_COOKIE_NAME}=${expiredCookie}`,
          "x-atlas-operator-request": "1",
        },
      }),
    );
    assert.equal(expiredRes.status, 401);
    assert.equal(upstream.calls.length, 0);
  } finally {
    upstream.restore();
  }
});

test("R1: valid session cookie requires CSRF header and matching Origin (403 on CSRF failure)", async () => {
  const upstream = setupFakeUpstream();
  try {
    const validCookie = operatorAuth.createOperatorSessionToken(TEST_TOKEN);

    // Missing CSRF custom header -> 403
    const missingCsrf = await watchlistSymbolRoute.DELETE(
      new NextRequest(`${APP_ORIGIN}/api/watchlist/TESTUSDT`, {
        method: "DELETE",
        headers: {
          cookie: `${operatorAuth.OPERATOR_COOKIE_NAME}=${validCookie}`,
        },
      }),
      { params: Promise.resolve({ symbol: "TESTUSDT" }) },
    );
    assert.equal(missingCsrf.status, 403);

    // Cross-origin Origin header -> 403
    const crossOrigin = await watchlistSymbolRoute.DELETE(
      new NextRequest(`${APP_ORIGIN}/api/watchlist/TESTUSDT`, {
        method: "DELETE",
        headers: {
          cookie: `${operatorAuth.OPERATOR_COOKIE_NAME}=${validCookie}`,
          "x-atlas-operator-request": "1",
          origin: "https://evil.example",
        },
      }),
      { params: Promise.resolve({ symbol: "TESTUSDT" }) },
    );
    assert.equal(crossOrigin.status, 403);
    assert.equal(upstream.calls.length, 0);
  } finally {
    upstream.restore();
  }
});

test("R1: operator session login -> HttpOnly SameSite=Strict cookie -> authorized mutation -> logout", async () => {
  const upstream = setupFakeUpstream();
  try {
    // 1. Login with wrong token -> 401
    const badLogin = await sessionRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/operator/session`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-atlas-operator-request": "1",
        },
        body: JSON.stringify({ token: "wrong" }),
      }),
    );
    assert.equal(badLogin.status, 401);

    // 2. Login with valid token -> 200 + signed HttpOnly SameSite=Strict cookie
    const goodLogin = await sessionRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/operator/session`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-atlas-operator-request": "1",
          origin: APP_ORIGIN,
        },
        body: JSON.stringify({ token: TEST_TOKEN }),
      }),
    );
    assert.equal(goodLogin.status, 200);
    const setCookie = goodLogin.headers.get("set-cookie") || "";
    assert.match(setCookie, /atlas_operator_session=/);
    assert.match(setCookie, /HttpOnly/i);
    assert.match(setCookie, /SameSite=strict/i);
    const cookiePair = setCookie.split(";")[0];

    // 3. Session status GET confirms authenticated
    const statusRes = await sessionRoute.GET(
      new NextRequest(`${APP_ORIGIN}/api/operator/session`, {
        method: "GET",
        headers: { cookie: cookiePair },
      }),
    );
    assert.deepEqual(await statusRes.json(), { configured: true, authenticated: true });

    // 4. Privileged mutation with valid cookie + CSRF succeeds and attaches upstream Bearer
    const delRes = await watchlistSymbolRoute.DELETE(
      new NextRequest(`${APP_ORIGIN}/api/watchlist/TESTUSDT`, {
        method: "DELETE",
        headers: {
          cookie: cookiePair,
          "x-atlas-operator-request": "1",
          origin: APP_ORIGIN,
        },
      }),
      { params: Promise.resolve({ symbol: "TESTUSDT" }) },
    );
    assert.equal(delRes.status, 200);
    assert.equal(upstream.calls.length, 1);
    assert.equal(upstream.calls[0].authorization, `Bearer ${TEST_TOKEN}`);

    // 5. Explicit valid Bearer token also succeeds without cookie
    const bearerRes = await rescanRoute.POST(
      new NextRequest(`${APP_ORIGIN}/api/screener/rescan`, {
        method: "POST",
        headers: { authorization: `Bearer ${TEST_TOKEN}` },
      }),
    );
    assert.equal(bearerRes.status, 200);
    assert.equal(upstream.calls.length, 2);

    // 6. Logout clears session cookie
    const logoutRes = await sessionRoute.DELETE(
      new NextRequest(`${APP_ORIGIN}/api/operator/session`, {
        method: "DELETE",
        headers: {
          cookie: cookiePair,
          "x-atlas-operator-request": "1",
          origin: APP_ORIGIN,
        },
      }),
    );
    assert.equal(logoutRes.status, 200);
    assert.match(logoutRes.headers.get("set-cookie") || "", /Max-Age=0/i);
  } finally {
    upstream.restore();
  }
});

test("R1: public GET routes remain open and NEVER attach ATLAS_OPERATOR_TOKEN", async () => {
  const upstream = setupFakeUpstream();
  try {
    const liveRes = await liveRoute.GET(
      new NextRequest(`${APP_ORIGIN}/api/screener/live`, { method: "GET" }),
    );
    const wlRes = await watchlistRoute.GET(
      new NextRequest(`${APP_ORIGIN}/api/watchlist`, { method: "GET" }),
    );
    assert.equal(liveRes.status, 200);
    assert.equal(wlRes.status, 200);
    assert.equal(upstream.calls.length, 2);
    assert.equal(upstream.calls[0].authorization, null);
    assert.equal(upstream.calls[1].authorization, null);
  } finally {
    upstream.restore();
  }
});

