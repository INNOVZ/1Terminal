import { NextRequest, NextResponse } from "next/server";
import { proxyToBackend } from "../_lib/backendProxy";

// Public reads also pass the shared transport boundary; never use a blanket external rewrite.
const PUBLIC_READ_PATHS = new Set([
  "macro", "macro/overextension", "watchlist/audit",
  "screener/short-watch", "screener/cron/status",
]);

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const joined = path.join("/");
  const isChart = path.length === 2 && path[0] === "chart" && /^[\p{L}\p{N}_]+$/u.test(path[1]);
  if (!isChart && !PUBLIC_READ_PATHS.has(joined)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  return proxyToBackend(request, `/api/${path.map(encodeURIComponent).join("/")}`);
}
