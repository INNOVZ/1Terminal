import { NextRequest } from "next/server";
import { proxyToBackend } from "../../_lib/backendProxy";

// Explicit static route avoids the DELETE-only /watchlist/[symbol] route shadowing this read.
export async function GET(request: NextRequest) {
  return proxyToBackend(request, "/api/watchlist/audit");
}
