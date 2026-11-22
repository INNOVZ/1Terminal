import { NextRequest } from "next/server";
import { proxyToBackend } from "../_lib/backendProxy";

export async function GET(request: NextRequest) {
  return proxyToBackend(request, "/api/watchlist", { attachOperatorAuth: false });
}

export async function POST(request: NextRequest) {
  return proxyToBackend(request, "/api/watchlist", { attachOperatorAuth: true });
}

