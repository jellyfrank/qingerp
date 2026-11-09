import { _t } from "@web/core/l10n/translation";

/** Persist a request before sending it; reconcile the original UUID after uncertainty. */
export class ScanRequestQueue {
    constructor({ storage, storageKey, call, onChange = () => {}, onResult = () => {} }) {
        Object.assign(this, { storage, storageKey, call, onChange, onResult });
        this.items = JSON.parse(storage.getItem(storageKey) || "[]");
        this.running = false;
        this.paused = this.items.length > 0;
    }

    persist() {
        this.storage.setItem(this.storageKey, JSON.stringify(this.items));
        this.onChange(this);
    }

    async enqueue(sessionId, payload) {
        if (this.paused || this.items.length >= 50) {
            throw new Error(_t("Resolve pending requests before scanning more items."));
        }
        const item = { sessionId, key: crypto.randomUUID(), payload };
        this.items.push(item);
        try {
            this.persist();
        } catch (error) {
            this.items.pop();
            throw error;
        }
        return this.drain();
    }

    async drain(reconcile = false) {
        if (this.running) {
            return;
        }
        this.running = true;
        this.paused = false;
        this.onChange(this);
        try {
            while (this.items.length) {
                const item = this.items[0];
                let response = reconcile ? await this.call("get_event", [[item.sessionId], item.key]) : false;
                if (!response) {
                    response = await this.call("execute", [[item.sessionId], item.key, item.payload]);
                }
                // Do not generate a new key if acknowledgement persistence fails.
                this.storage.setItem(this.storageKey, JSON.stringify(this.items.slice(1)));
                this.items.shift();
                this.onChange(this);
                await this.onResult(response, item.payload);
                reconcile = false;
                if (!response.ok && this.items.length) {
                    // A rejected scan must be acknowledged before subsequent captured scans proceed.
                    this.paused = true;
                    break;
                }
            }
        } catch (error) {
            this.paused = this.items.length > 0;
            throw error;
        } finally {
            this.running = false;
            this.onChange(this);
        }
    }
}
