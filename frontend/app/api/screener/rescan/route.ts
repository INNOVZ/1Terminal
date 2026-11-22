import { NextRequest } from "next/server";
import { proxyToBackend } from "../../_lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyToBackend(request, "/api/screener/rescan", {
    attachOperatorAuth: true,
  });
}

