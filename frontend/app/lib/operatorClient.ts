export const OPERATOR_CSRF_HEADER_NAME = "X-Atlas-Operator-Request";

export function operatorRequestHeaders(
  extra: Record<string, string> = {},
): Record<string, string> {
  return {
    [OPERATOR_CSRF_HEADER_NAME]: "1",
    ...extra,
  };
}

export async function establishOperatorSession(
  token: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch("/api/operator/session", {
    method: "POST",
    credentials: "same-origin",
    headers: operatorRequestHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ token: token.trim() }),
  });
  if (res.ok) {
    return { ok: true };
  }
  try {
    const body = (await res.json()) as { detail?: string };
    return { ok: false, error: body.detail || `Authentication failed (HTTP ${res.status})` };
  } catch {
    return { ok: false, error: `Authentication failed (HTTP ${res.status})` };
  }
}

export async function clearOperatorSession(): Promise<void> {
  await fetch("/api/operator/session", {
    method: "DELETE",
    credentials: "same-origin",
    headers: operatorRequestHeaders(),
  }).catch(() => {});
}

