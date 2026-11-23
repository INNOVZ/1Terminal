import { NextRequest } from "next/server";
import { proxyToBackend } from "../../_lib/backendProxy";
import { isTruthyQueryFlag } from "../../_lib/operatorAuth";

export async function GET(request: NextRequest) {
  const wantsRefresh = isTruthyQueryFlag(request.nextUrl.searchParams.get("refresh"));
  return proxyToBackend(request, "/api/watchlist/auto", {
    attachOperatorAuth: wantsRefresh,
  });
}

