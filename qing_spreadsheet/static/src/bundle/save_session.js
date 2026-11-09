/** One immutable request survives lost responses. Never replace its UUID on retry. */
export class SaveSession {
    constructor({ call, storage, key, version, token, onChange = () => {} }) {
        Object.assign(this, { call, storage, key, version, token, onChange });
        try {
            this.pending = JSON.parse(storage.getItem(key) || "null");
        } catch (error) {
            this.pending = null;
            this.storageError = error;
        }
        this.running = false;
    }
    persist() {
        try {
            if (this.pending) {
                this.storage.setItem(this.key, JSON.stringify(this.pending));
            } else {
                this.storage.removeItem(this.key);
            }
        } catch (error) {
            // A full browser store must not prevent a server save. Keep this request in memory.
            this.storageError = error;
        } finally {
            this.onChange();
        }
    }
    async submit(data, method = "save_workbook") {
        if (this.pending || this.running) {
            throw new Error("Resolve the pending save first.");
        }
        this.pending = { data: structuredClone(data), method, version: this.version, requestId: crypto.randomUUID() };
        this.persist();
        return this.retry();
    }
    async retry() {
        if (!this.pending || this.running) {
            return;
        }
        this.running = true;
        this.onChange();
        try {
            const pending = this.pending;
            const status = await this.call("save_status", [pending.requestId]);
            let result;
            if (status.saved) {
                if (status.current_version !== status.version) {
                    throw new Error("A newer server version exists. Keep this draft as a copy before reloading.");
                }
                result = { ...status.result, version: status.version };
            } else {
                if (status.current_version !== pending.version) {
                    throw new Error("The server version changed. Keep this draft as a copy before reloading.");
                }
                const args = [pending.version, this.token, pending.requestId];
                if (pending.method !== "reload_record") { args.unshift(pending.data); }
                result = await this.call(pending.method, args);
            }
            this.version = result.version;
            this.pending = null;
            this.persist();
            return result;
        } finally {
            this.running = false;
            this.onChange();
        }
    }
    discard() {
        if (this.running) {
            throw new Error("Wait for the current request to finish.");
        }
        this.pending = null;
        this.persist();
    }
}
