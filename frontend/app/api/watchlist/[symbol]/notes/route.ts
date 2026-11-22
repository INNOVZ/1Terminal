import { NextRequest } from "next/server";
import { proxyToBackend } from "../../../_lib/backendProxy";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await context.params;
  return proxyToBackend(
    request,
    `/api/watchlist/${encodeURIComponent(symbol)}/notes`,
    { attachOperatorAuth: true },
  );
}

