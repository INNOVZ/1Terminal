import assert from "node:assert/strict";
import test from "node:test";
import { screenerPublication } from "../app/lib/screenerPublication.ts";

test("scheduled snapshots replace the previous cohort including an authoritative empty result", () => {
  const previous = [{ symbol: "OLDUSDT" }];
  for (const opportunities of [[], [{ symbol: "NEWUSDT" }]]) {
    const response = { is_scanning: true, scan_source: "scheduled", data_status: "current", opportunities };
    assert.equal(screenerPublication(response).action, "replace");
    assert.notDeepEqual(response.opportunities, previous);
  }
});

test("withheld state always clears publication even when a scan is active", () => {
  for (const data_status of ["stale", "scan_failed", "degraded_zero_usable_inputs", "startup_revalidation_required"]) {
    for (const scan_source of ["scheduled", "manual_or_init"]) {
      const result = screenerPublication({ is_scanning: true, scan_source, data_status, opportunities: [] });
      assert.equal(result.action, "withhold");
      assert.equal(result.status, data_status);
      assert.ok(result.detail);
    }
  }
  assert.equal(screenerPublication({ data_status: "current", stale: true }).action, "withhold");
  assert.equal(screenerPublication({ data_status: "current", stale: true }).status, "stale");
});

test("manual/init progress remains distinct from an authoritative empty scan", () => {
  assert.equal(screenerPublication({ is_scanning: true, opportunities: [] }).action, "progress");
  assert.equal(screenerPublication({ is_scanning: false, opportunities: [] }).action, "replace");
});
