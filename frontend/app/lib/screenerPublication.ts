/** Resolve publication authority before handling scan-progress UI. */
export interface PublicationEnvelope {
  is_scanning?: boolean;
  scan_source?: string;
  data_status?: string;
  stale?: boolean;
  macro_narrative?: string;
  last_scan_error?: string;
  opportunities?: readonly unknown[];
}

export function isWithheldStatus(status: string): boolean {
  return ["stale", "scan_failed", "degraded_zero_usable_inputs", "startup_revalidation_required"].includes(status);
}

export function screenerPublication(envelope: PublicationEnvelope) {
  const reportedStatus = envelope.data_status || "current";
  const status = envelope.stale && !isWithheldStatus(reportedStatus) ? "stale" : reportedStatus;
  if (envelope.stale || isWithheldStatus(status)) {
    return {
      action: "withhold" as const,
      status,
      detail: envelope.macro_narrative || envelope.last_scan_error ||
        "Qualifications withheld until a fresh live scan verifies current market structure.",
    };
  }
  if (envelope.is_scanning && envelope.scan_source !== "scheduled") {
    return { action: "progress" as const, status, detail: null };
  }
  return { action: "replace" as const, status, detail: null };
}
