import assert from "node:assert/strict";
import test from "node:test";
import { AccountScope } from "../app/lib/accountScope.ts";

test("account tickets invalidate pending reads and writes across logout and A-B-A switches", () => {
  const scope = new AccountScope();
  scope.setIdentity("A");
  const oldMutation = scope.capture();
  assert.equal(scope.isCurrent(oldMutation), true);
  scope.setIdentity(null);
  assert.equal(scope.isCurrent(oldMutation), false);
  scope.setIdentity("B");
  const b = scope.capture();
  assert.equal(scope.isCurrent(oldMutation), false);
  scope.setIdentity("A");
  assert.equal(scope.isCurrent(oldMutation), false);
  assert.equal(scope.isCurrent(b), false);
});

test("same-account token refresh preserves valid work without granting another identity", () => {
  const scope = new AccountScope();
  scope.setIdentity("A");
  const ticket = scope.capture();
  assert.equal(scope.setIdentity("A"), false);
  assert.equal(scope.isCurrent(ticket), true);
  assert.equal(scope.isCurrent({ ...ticket, userId: "B" }), false);
});
