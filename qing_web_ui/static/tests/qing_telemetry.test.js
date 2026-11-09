/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { requestQingTelemetryEnqueue } from "@qing_web_ui/js/qing_telemetry";

function makeStorage() {
    const values = new Map();
    return {
        getItem: (key) => values.get(key) || null,
        setItem: (key, value) => values.set(key, value),
        removeItem: (key) => values.delete(key),
    };
}

test("telemetry fallback is restricted to administrators with Qing Base", async () => {
    patchWithCleanup(session, { db: "qing_test", qing_base_version: null });
    patchWithCleanup(user, { isSystem: true });
    const calls = [];
    const options = {
        send: async (...args) => calls.push(args),
        storage: makeStorage(),
        now: 1000000,
    };

    expect(await requestQingTelemetryEnqueue(options)).toBe(false);
    session.qing_base_version = "20.0.1.0.39";
    user.isSystem = false;
    expect(await requestQingTelemetryEnqueue(options)).toBe(false);
    user.isSystem = true;
    expect(await requestQingTelemetryEnqueue(options)).toBe(true);
    expect(calls).toEqual([["/qing/telemetry/enqueue", {}, { silent: true }]]);
    expect(registry.category("services").contains("qing_telemetry")).toBe(true);
});

test("telemetry fallback throttles attempts and retries after a failure", async () => {
    patchWithCleanup(session, { db: "qing_test", qing_base_version: "20.0.1.0.39" });
    patchWithCleanup(user, { isSystem: true });
    const storage = makeStorage();
    let calls = 0;
    const send = async () => {
        calls++;
    };

    expect(await requestQingTelemetryEnqueue({ send, storage, now: 1000000 })).toBe(true);
    expect(await requestQingTelemetryEnqueue({ send, storage, now: 1000001 })).toBe(false);
    expect(calls).toBe(1);
    expect(await requestQingTelemetryEnqueue({ send, storage, now: 1000000 + 60 * 60 * 1000 })).toBe(true);
    expect(calls).toBe(2);

    const failure = async () => {
        calls++;
        throw new Error("offline");
    };
    expect(await requestQingTelemetryEnqueue({ send: failure, storage, now: 1000000 + 2 * 60 * 60 * 1000 })).toBe(false);
    expect(await requestQingTelemetryEnqueue({ send, storage, now: 1000000 + 2 * 60 * 60 * 1000 + 1 })).toBe(true);
    expect(calls).toBe(4);
});
