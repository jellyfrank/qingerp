/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";

const ENQUEUE_ROUTE = "/qing/telemetry/enqueue";
const ATTEMPT_INTERVAL_MS = 60 * 60 * 1000;
const STORAGE_PREFIX = "qing_web_ui_telemetry_enqueue";

/**
 * Ask Qing Base to schedule a due heartbeat after the web client is ready.
 * The browser never sends customer details or an online-service credential.
 */
export async function requestQingTelemetryEnqueue({
    send = rpc,
    storage,
    now = Date.now(),
} = {}) {
    if (!session.qing_base_version || !user.isSystem) {
        return false;
    }

    const key = `${STORAGE_PREFIX}:${session.db || "default"}`;
    try {
        storage ||= browser.localStorage;
        const lastAttempt = Number(storage?.getItem(key));
        if (lastAttempt > 0 && now >= lastAttempt && now - lastAttempt < ATTEMPT_INTERVAL_MS) {
            return false;
        }
        storage?.setItem(key, String(now));
    } catch (_error) {
        // Private browsing may deny storage; the server still limits requests.
    }

    try {
        await send(ENQUEUE_ROUTE, {}, { silent: true });
        return true;
    } catch (_error) {
        try {
            storage?.removeItem(key);
        } catch (_storageError) {
            // Telemetry must never interrupt normal business use.
        }
        return false;
    }
}

export const qingTelemetryService = {
    start(env) {
        env.bus.addEventListener(
            "WEB_CLIENT_READY",
            () => {
                void requestQingTelemetryEnqueue();
            },
            { once: true }
        );
    },
};

registry.category("services").add("qing_telemetry", qingTelemetryService);
