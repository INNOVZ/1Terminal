import { NextRequest } from "next/server";
import { proxyToBackend } from "../../_lib/backendProxy";
import { isTruthyQueryFlag } from "../../_lib/operatorAuth";

export async function GET(request: NextRequest) {
  const isRefresh = isTruthyQueryFlag(request.nextUrl.searchParams.get("refresh"));
  return proxyToBackend(request, "/api/screener/live", {
    attachOperatorAuth: isRefresh,
  });
}
