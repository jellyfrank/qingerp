import { Component, onMounted, onPatched, onWillStart, onWillDestroy, proxy, signal, t, usePlugin, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useOwnedDialogs, useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { browser } from "@web/core/browser/browser";
import { BarcodePlugin } from "@barcodes/barcode_plugin";
import { BarcodeDialog } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { Dialog } from "@web/core/dialog/dialog";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ScanRequestQueue } from "./request_queue";

const MODEL = "qing.stock.scan.session";

export class ScanModeDialog extends Component {
    static template = "qing_stock_barcode.ScanModeDialog";
    static components = { Dialog };
    props = useProps({ close: t.function(), choices: t.array(), selected: t.string(), onSelect: t.function() });
    title = _t("Scan mode");
    select(value) {
        this.props.onSelect(value);
        this.props.close();
    }
}

export class ScanRecoveryDialog extends Component {
    static template = "qing_stock_barcode.ScanRecoveryDialog";
    static components = { Dialog };
    props = useProps({ close: t.function(), state: t.object(), retry: t.function() });
    title = _t("Review pending requests");
    async retry() {
        if (await this.props.retry()) { this.props.close(); }
    }
}

export class WarehouseWorkbench extends Component {
    static template = "qing_stock_barcode.Workbench";
    props = useProps(standardActionServiceProps);
    scannerInput = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.actions = useService("action");
        this.addDialog = useOwnedDialogs();
        this.state = proxy({
            page: "incoming", showCompleted: true, dashboard: null, session: null, barcode: "", mode: "auto", quantity: 1,
            selectedTransfer: false, selectedLocation: false, selectedLine: false, expandedProgress: false, search: "", productSearch: "", products: [], locations: [], stock: null,
            message: "", messageType: "info", messageId: 0, recoveryError: "", pending: 0, paused: false, busy: false, loading: false,
            online: browser.navigator.onLine, countValues: {}, showProducts: false,
            pda: false, manualKeyboard: false,
            deviceName: "", cameraOpen: false,
        });
        const company = user.activeCompany?.id || user.context.allowed_company_ids?.[0];
        this.storageKey = `qing_scan:${session.db}:${user.userId}:${company}`;
        this.state.pda = browser.localStorage.getItem(`${this.storageKey}:pda`) === "true";
        this.state.deviceName = browser.localStorage.getItem(`${this.storageKey}:device`) || "";
        this.destroyed = false;
        this.queue = new ScanRequestQueue({
            storage: browser.localStorage, storageKey: `${this.storageKey}:requests`,
            call: (method, args) => this.orm.call(MODEL, method, args),
            onChange: (queue) => {
                this.state.pending = queue.items.length;
                this.state.paused = queue.paused;
                this.state.busy = queue.running;
            },
            onResult: (result, payload) => this.acceptResult(result, payload),
        });
        this.state.pending = this.queue.items.length;
        this.state.paused = this.queue.paused;
        useBus(usePlugin(BarcodePlugin).bus, "barcode_scanned", (event) => {
            // PDA text input owns framing; the native global listener must not count it again.
            if (!this.state.pda && !this.scanningObscured) {
                this.onBarcode(event.detail.barcode);
            }
        });
        this.onConnectionChange = () => {
            this.state.online = browser.navigator.onLine;
            this.setFeedback(this.state.online ? _t("Connection restored.") : _t("Offline. Scanning is paused until the connection is restored."), this.state.online ? "success" : "warning");
        };
        this.onVisibilityChange = () => {
            this.state.barcode = "";
            if (this.scannerInput()) { this.scannerInput().value = ""; }
            this.focusScanner();
        };
        window.addEventListener("online", this.onConnectionChange);
        window.addEventListener("offline", this.onConnectionChange);
        document.addEventListener("visibilitychange", this.onVisibilityChange);
        let scanEnabled = false;
        const restoreEnabledScanner = () => {
            // Wait until the DOM input is enabled; a pre-patch focus is ignored by browsers.
            const enabled = this.canScan;
            if (enabled && !scanEnabled) { this.focusScanner(); }
            scanEnabled = enabled;
            if (!this.state.paused) { this.recoveryDismissed = false; }
            if (this.state.paused && !this.state.busy && !this.recoveryOpen && !this.recoveryDismissed) {
                this.openRecovery();
            }
        };
        onMounted(restoreEnabledScanner);
        onPatched(restoreEnabledScanner);
        onWillDestroy(() => {
            this.destroyed = true;
            window.removeEventListener("online", this.onConnectionChange);
            window.removeEventListener("offline", this.onConnectionChange);
            document.removeEventListener("visibilitychange", this.onVisibilityChange);
            browser.cancelAnimationFrame(this.focusFrame);
            browser.cancelAnimationFrame(this.dialogFocusFrame);
            browser.clearTimeout(this.feedbackTimer);
            this.audio?.close();
        });
        onWillStart(async () => {
            const params = this.props.action?.params || {};
            if (["incoming", "outgoing", "internal"].includes(params.operation)) { this.state.page = params.operation; }
            await this.refreshDashboard();
            const pendingSession = this.queue.items[0]?.sessionId;
            const saved = Number(browser.localStorage.getItem(`${this.storageKey}:session`));
            if (pendingSession) {
                await this.openSession(pendingSession);
                if (params.picking_id && params.picking_id !== this.state.session?.picking_id) {
                    this.showMessage(_t("Resolve pending scans before changing operations."), false);
                }
            } else if (params.picking_id) {
                if (params.picking_state === "done") { await this.viewCompleted(params.picking_id); }
                else {
                    await this.startPicking(params.picking_id);
                    if (params.reservation_warning && this.state.session) {
                        this.setFeedback(_t("Some products could not be fully reserved. Scan only reserved quantities, or review availability in Inventory."), "warning");
                    }
                }
            } else if (!params.list_only && (params.session_id || saved)) {
                await this.openSession(params.session_id || saved);
            }
        });
    }

    get scanningObscured() {
        return this.destroyed || document.hidden || this.state.cameraOpen || document.body.classList.contains("modal-open");
    }

    focusScanner(force = false) {
        browser.cancelAnimationFrame(this.focusFrame);
        this.focusFrame = browser.requestAnimationFrame(() => {
            const input = this.scannerInput();
            if (!this.state.pda || !input || !this.canScan || this.scanningObscured) { return; }
            const active = document.activeElement;
            if (!force && active !== input && active?.matches("input, textarea, select, [contenteditable='true']")) { return; }
            input.focus({ preventScroll: true });
        });
    }

    restoreScannerAfterDialog() {
        // Let the overlay finish restoring its previous focus before refocusing the PDA input.
        browser.cancelAnimationFrame(this.dialogFocusFrame);
        this.dialogFocusFrame = browser.requestAnimationFrame(() => this.focusScanner());
    }

    setPdaMode(event) {
        this.state.pda = event.target.checked;
        this.state.barcode = "";
        this.state.manualKeyboard = false;
        this.guarded(() => browser.localStorage.setItem(`${this.storageKey}:pda`, String(this.state.pda)));
        this.focusScanner(true);
    }

    onScannerInput(event) {
        const input = event.target;
        this.state.barcode = input.value;
        if (!this.state.pda || event.isComposing || this.composing) { return; }
        // Text-injection scanners can send whole strings without any printable keydown events.
        // Only explicit CR/LF/Tab delimit records. Preserve ASCII 29 (GS1), spaces and short codes.
        const parts = input.value.split(/[\r\n\t]+/);
        const remainder = parts.pop();
        if (!parts.length) { return; }
        input.value = "";
        for (const code of parts.filter(Boolean)) { this.onBarcode(code); }
        this.state.barcode = input.value = remainder;
    }

    onScannerKeydown(event) {
        if (event.isComposing || this.composing || event.keyCode === 229) { return; }
        if (event.key !== "Enter" && !(this.state.pda && event.key === "Tab")) { return; }
        event.preventDefault();
        event.stopPropagation();
        if (event.repeat) { return; }
        this.submitBarcode();
    }

    submitBarcode() {
        const input = this.scannerInput();
        const code = input?.value || this.state.barcode;
        if (!this.canScan || this.composing || !code.trim()) { return; }
        this.state.barcode = "";
        if (input) { input.value = ""; }
        this.onBarcode(code);
        this.focusScanner();
    }

    toggleManualKeyboard() {
        this.state.manualKeyboard = !this.state.manualKeyboard;
        // Android applies the new inputmode when focus enters the field again.
        this.scannerInput()?.blur();
        this.focusScanner(true);
    }

    openCamera() {
        if (!this.canScan || this.scanningObscured) { return; }
        if (!isBarcodeScannerSupported()) {
            this.showMessage(_t("Camera scanning requires HTTPS and camera access in this browser."), false);
            return;
        }
        this.state.cameraOpen = true;
        let accepted = false;
        this.addDialog(BarcodeDialog, {
            facingMode: "environment",
            onResult: (code) => {
                if (accepted || this.destroyed) { return; }
                accepted = true;
                this.state.cameraOpen = false;
                // Native dialog closes before its result callback; ignore its remaining close animation.
                this.onBarcode(code, true);
            },
            onError: (error) => this.showMessage(error.message, false),
        }, { onClose: () => { this.state.cameraOpen = false; this.restoreScannerAfterDialog(); } });
    }

    get canSubmitBarcode() {
        return this.canScan && Boolean(this.state.barcode.trim());
    }

    get canValidate() {
        const session = this.state.session;
        return session?.kind === "picking" && ["scanning", "applied"].includes(session.state)
            && (session.owned || session.manager) && session.validation_blocker === false
            && this.state.online && !this.state.paused && !this.state.pending && !this.state.busy
            && !this.state.loading && !this.state.cameraOpen;
    }

    async validateTransfer() {
        if (!this.canValidate || this.scanningObscured) { return; }
        await this.command({ op: "finish" });
    }

    get showScanner() {
        return this.state.session ? this.state.session.state === "scanning" : this.state.page === "lookup";
    }

    get canScan() {
        const current = this.state.session;
        return this.showScanner && this.state.online && !this.state.paused && !this.state.cameraOpen && !this.state.loading && (!current ||
            (current.state === "scanning" && (current.owned || current.manager)));
    }

    get scanModes() {
        return [
            { value: "auto", label: _t("Automatic") }, { value: "product", label: _t("Product") },
            { value: "source", label: _t("Source location") }, { value: "destination", label: _t("Destination location") },
            { value: "lot", label: _t("Lot / Serial") }, { value: "package", label: _t("Whole package") },
        ];
    }

    get modeLabel() { return this.scanModes.find((mode) => mode.value === this.state.mode)?.label; }

    openModeSelector() {
        if (this.state.busy || this.state.pending) { return; }
        this.addDialog(ScanModeDialog, {
            choices: this.scanModes, selected: this.state.mode,
            onSelect: (value) => { this.state.mode = value; },
        }, { onClose: () => this.restoreScannerAfterDialog() });
    }

    get selectedTransfer() {
        return this.state.dashboard?.transfers.find((transfer) => transfer.id === this.state.selectedTransfer);
    }

    get pages() {
        return [
            { id: "incoming", label: _t("Receive"), icon: "M12 3v12m-4-4 4 4 4-4M4 14v6h16v-6" },
            { id: "outgoing", label: _t("Deliver"), icon: "M12 15V3m-4 4 4-4 4 4M4 14v6h16v-6" },
            { id: "internal", label: _t("Move stock"), icon: "M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4" },
            { id: "inventory", label: _t("Count"), icon: "M8 5H5v16h14V5h-3M8 3h8v4H8zM8 12l2 2 5-4M8 18h7" },
            { id: "lookup", label: _t("Find Stock"), icon: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6" },
        ];
    }

    progressPercent(move) {
        const planned = Number(move.planned);
        const scanned = Number(move.scanned);
        if (!Number.isFinite(planned) || !Number.isFinite(scanned) || planned <= 0) { return 0; }
        return Math.max(0, Math.min(100, Math.floor(scanned / planned * 1000) / 10));
    }

    get progressRows() {
        const session = this.state.session;
        if (!session) { return []; }
        if (session.kind === "inventory") {
            return session.lines.map((line) => ({ ...line, key: `line-${line.id}`, percent: line.counted ? 100 : 0, lines: [line] }));
        }
        const linesByMove = new Map();
        for (const line of session.lines) {
            if (!linesByMove.has(line.move_id)) { linesByMove.set(line.move_id, []); }
            linesByMove.get(line.move_id).push(line);
        }
        return session.moves.map((move) => ({ ...move, key: `move-${move.id}`, percent: this.progressPercent(move), lines: linesByMove.get(move.id) || [] }));
    }

    get documentProgress() {
        if (this.state.session?.picking_state === "done") { return 100; }
        const rows = this.progressRows;
        // Equal weight per operation avoids adding incompatible units; excess on one row cannot cover another.
        return rows.length ? Math.floor(rows.reduce((total, row) => total + row.percent, 0) / rows.length * 10) / 10 : 0;
    }

    toggleProgress(key) {
        if (this.state.busy || this.state.pending) { return; }
        this.state.expandedProgress = this.state.expandedProgress === key ? false : key;
        this.state.selectedLine = false;
    }

    get stateLabel() {
        return { scanning: _t("Scanning"), review: _t("Awaiting review"), applied: _t("Awaiting validation"),
            done: _t("Done"), cancelled: _t("Cancelled") }[this.state.session?.state] || "";
    }

    async guarded(work) {
        try {
            return await work();
        } catch (error) {
            const message = error.data?.message || error.message || _t("The request could not be completed.");
            if (this.state.paused) {
                const connectionLost = error.name === "ConnectionLostError" || message === "user is not connected" || message.startsWith("Connection to ");
                this.state.recoveryError = connectionLost ? _t("Connection lost. Reconnect before retrying.") : message;
                this.dismissFeedback();
            } else {
                this.showMessage(message, false);
            }
            return null;
        }
    }

    dismissFeedback() {
        browser.clearTimeout(this.feedbackTimer);
        this.state.message = "";
    }

    setFeedback(message, type = "info") {
        if (this.destroyed) { return; }
        this.dismissFeedback();
        this.state.message = message || "";
        this.state.messageType = type;
        this.state.messageId++;
        if (message) { this.feedbackTimer = browser.setTimeout(() => this.dismissFeedback(), 5000); }
    }

    openRecovery() {
        if (this.recoveryOpen || !this.state.paused || this.destroyed) { return; }
        this.recoveryOpen = true;
        this.addDialog(ScanRecoveryDialog, { state: this.state, retry: () => this.recover() }, {
            onClose: () => { this.recoveryOpen = false; this.recoveryDismissed = true; this.restoreScannerAfterDialog(); },
        });
    }

    showMessage(message, ok = true) {
        this.setFeedback(message, ok ? "success" : "danger");
        try {
            browser.navigator.vibrate?.(ok ? 60 : [90, 70, 90]);
            const Audio = window.AudioContext || window.webkitAudioContext;
            if (Audio) {
                this.audio ||= new Audio();
                const oscillator = this.audio.createOscillator();
                const gain = this.audio.createGain();
                oscillator.frequency.value = ok ? 880 : 220;
                gain.gain.value = 0.04;
                oscillator.connect(gain);
                gain.connect(this.audio.destination);
                oscillator.start();
                oscillator.stop(this.audio.currentTime + (ok ? 0.07 : 0.18));
            }
        } catch {
            // Text feedback remains available when device feedback is unavailable.
        }
    }

    async refreshDashboard() {
        const operation = ["incoming", "outgoing", "internal"].includes(this.state.page) ? this.state.page : "incoming";
        this.state.dashboard = await this.orm.call(MODEL, "dashboard", [operation, this.state.search, this.state.showCompleted]);
        if (!this.selectedTransfer) { this.state.selectedTransfer = false; }
    }

    async toggleCompleted(event) {
        if (this.state.busy || this.state.pending || this.state.loading) { return; }
        this.state.showCompleted = event.target.checked;
        await this.loadSession(() => this.refreshDashboard());
    }

    async viewCompleted(id) {
        if (this.state.busy || this.state.pending || this.state.loading || !this.state.online) { return; }
        await this.loadSession(async () => {
            this.setSession(await this.orm.call(MODEL, "get_completed_picking", [id]));
        });
    }

    async refreshSession() {
        if (this.state.session.readonly) { await this.viewCompleted(this.state.session.picking_id); }
        else { await this.openSession(this.state.session.id); }
    }

    async changePage(page) {
        if (this.state.pending) {
            this.showMessage(_t("Resolve pending scans before changing operations."), false);
            return;
        }
        if (this.state.busy || this.state.loading) { return; }
        this.dismissFeedback();
        this.state.barcode = "";
        this.state.showProducts = false;
        this.state.page = page;
        this.state.selectedTransfer = false;
        this.state.selectedLocation = false;
        this.state.session = null;
        this.state.expandedProgress = false;
        this.state.selectedLine = false;
        this.state.search = "";
        this.state.stock = null;
        browser.localStorage.removeItem(`${this.storageKey}:session`);
        await this.guarded(async () => {
            if (page === "inventory") {
                this.state.locations = await this.orm.call(MODEL, "find_locations", [""]);
            } else {
                await this.refreshDashboard();
            }
        });
    }

    setSession(data) {
        if (this.destroyed) {
            return;
        }
        if (this.state.session?.id !== data.id) {
            this.state.expandedProgress = false;
            this.state.selectedLine = false;
        }
        this.state.session = data;
        if (!this.progressRows.some((row) => row.key === this.state.expandedProgress)) {
            this.state.expandedProgress = false;
        }
        if (data.operation) { this.state.page = data.operation; }
        if (data.state === "done" && !data.readonly) {
            this.setFeedback(_t("Operation completed."), "success");
        }
        if (data.readonly) { browser.localStorage.removeItem(`${this.storageKey}:session`); }
        else { browser.localStorage.setItem(`${this.storageKey}:session`, String(data.id)); }
        this.focusScanner();
        if (!data.lines.some((line) => line.id === this.state.selectedLine)) {
            this.state.selectedLine = false;
        }
    }

    async editInternal(id = false) {
        if (this.state.page !== "internal" || this.state.loading || this.state.busy || this.state.pending || !this.state.online) { return; }
        await this.loadSession(async () => {
            const action = await this.orm.call("stock.picking", id ? "action_qing_edit_internal" : "action_qing_new_internal", id ? [[id]] : []);
            await this.actions.doAction(action);
        });
    }

    async startTransfer(transfer) {
        if (transfer.state === "done" || this.state.loading || this.state.busy || this.state.pending || !this.state.online) { return; }
        if (transfer.state === "draft") { await this.editInternal(transfer.id); return; }
        this.state.selectedTransfer = transfer.id;
        if (transfer.session_id) { await this.openSession(transfer.session_id); }
        else { await this.startPicking(transfer.id); }
        // A transfer can be completed on the PC between loading the list and clicking its action.
        if (!this.state.session) { await this.guarded(() => this.refreshDashboard()); }
    }

    async startPicking(id) {
        await this.loadSession(async () => {
            const data = await this.orm.call(MODEL, "start_picking", [id, crypto.randomUUID(), this.state.deviceName || (this.state.pda ? "Android / PDA" : "Web scanner")]);
            this.setSession(data);
            this.setFeedback(_t("Scan a product or location to begin."));
        });
    }

    async startLocation(location) {
        if (this.state.loading || this.state.busy || this.state.pending || !this.state.online) { return; }
        this.state.selectedLocation = location.id;
        if (location.session_id) { await this.openSession(location.session_id); }
        else { await this.startCount(location.id); }
    }

    async startCount(id) {
        await this.loadSession(async () => {
            const data = await this.orm.call(MODEL, "start_count", [id, crypto.randomUUID(), this.state.deviceName || (this.state.pda ? "Android / PDA" : "Web scanner")]);
            this.setSession(data);
            this.setFeedback(_t("Count this location. Uncounted items will remain unchanged."));
        });
    }

    async openSession(id) {
        await this.loadSession(async () => this.setSession(await this.orm.call(MODEL, "get_session", [[id]])));
    }

    async loadSession(work) {
        this.state.loading = true;
        try {
            await this.guarded(work);
        } finally {
            this.state.loading = false;
            this.focusScanner();
        }
    }

    async onBarcode(code, fromCamera = false) {
        if (!this.showScanner || this.destroyed || document.hidden || (!fromCamera && this.scanningObscured)) { return; }
        if (!code || !this.canScan || this.state.loading) {
            this.showMessage(_t("Scanning is paused. Resolve the pending operation before continuing."), false);
            return;
        }
        this.state.barcode = "";
        if (this.state.session) {
            await this.command({ op: "scan", code, kind: this.state.mode,
                quantity: Number(this.state.quantity), line_id: this.state.selectedLine || false });
            return;
        }
        this.state.loading = true;
        await this.guarded(async () => {
            if (this.state.page === "lookup") {
                this.state.stock = await this.orm.call(MODEL, "lookup_stock", [code]);
            } else if (this.state.page === "inventory") {
                this.state.locations = await this.orm.call(MODEL, "find_locations", [code]);
            } else {
                const id = await this.orm.call(MODEL, "find_picking", [code]);
                await this.startPicking(id);
            }
        });
        this.state.loading = false;
        this.focusScanner();
    }

    async command(payload) {
        if (!this.state.online) {
            this.showMessage(_t("You are offline. Reconnect before scanning."), false);
            return;
        }
        await this.guarded(() => this.queue.enqueue(this.state.session.id, payload));
    }

    async recover() {
        this.state.recoveryError = "";
        await this.guarded(() => this.queue.drain(true));
        return !this.state.paused && !this.state.pending;
    }

    async acceptResult(response, payload) {
        this.setSession(response.session);
        this.showMessage(response.result.message, response.ok);
        if (response.result.package_preview) {
            const contents = response.result.package_preview.map((line) => `${line.product}: ${line.quantity} ${line.unit}`).join("\n");
            this.addDialog(ConfirmationDialog, {
                title: _t("Confirm package contents"), body: contents,
                confirm: () => this.command({ ...payload, confirm_package: true }),
            }, { onClose: () => this.restoreScannerAfterDialog() });
        }
        const action = response.result.action;
        if (response.ok && action) {
            await this.actions.doAction(action, { onClose: () => this.openSession(response.session.id) });
        }
    }

    confirmOperation(op) {
        const labels = {
            finish: _t("Apply the scanned quantities and continue with native stock validation?"),
            cancel: _t("Cancel this scan session? Stock will remain unchanged."),
            return: _t("Create a native return from this completed transfer?"),
            take_over: _t("Assign this session to yourself? The previous operator will lose edit access."),
            submit_review: _t("Submit the count for review? Uncounted items will remain unchanged."),
            release: _t("Release scanner control? Applied quantities will remain on the native transfer. Review and correct them in Inventory before validating."),
        };
        this.addDialog(ConfirmationDialog, { title: _t("Confirm operation"), body: labels[op],
            confirm: () => this.command({ op }) }, { onClose: () => this.restoreScannerAfterDialog() });
    }

    async searchRecords() {
        await this.guarded(async () => {
            if (this.state.page === "inventory") {
                this.state.locations = await this.orm.call(MODEL, "find_locations", [this.state.search]);
                if (!this.state.locations.some((location) => location.id === this.state.selectedLocation)) {
                    this.state.selectedLocation = false;
                }
            } else {
                await this.refreshDashboard();
            }
        });
    }

    async searchProducts() {
        this.state.showProducts = true;
        await this.guarded(async () => {
            this.state.products = await this.orm.call(MODEL, "find_products", [this.state.productSearch]);
        });
    }

    async selectProduct(product) {
        this.state.showProducts = false;
        await this.command({ op: "scan", product_id: product.id, quantity: Number(this.state.quantity),
            line_id: this.state.selectedLine || false });
    }

    async setCount(line) {
        const value = this.state.countValues[line.id];
        if (value === undefined || value === "") {
            this.showMessage(_t("Enter the actual count, including zero when appropriate."), false);
            return;
        }
        await this.command({ op: "set_count", line_id: line.id, quantity: Number(value) });
    }

    async printLabels() {
        await this.guarded(async () => {
            const action = await this.orm.call(MODEL, "print_labels", [[this.state.session.id]]);
            await this.actions.doAction(action);
        });
    }

    async openNative() {
        await this.actions.doAction({ type: "ir.actions.act_window", res_model: "stock.picking",
            res_id: this.state.session.picking_id, views: [[false, "form"]], target: "new" },
            { onClose: () => this.refreshSession() });
    }
}

registry.category("actions").add("qing_stock_barcode.workbench", WarehouseWorkbench);
