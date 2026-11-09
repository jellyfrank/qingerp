import { Component, onWillStart, onWillDestroy, proxy, t, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useOwnedDialogs, useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { useSetupAction } from "@web/search/action_hook";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { download } from "@web/core/network/download";
import { SpreadsheetComponent } from "@spreadsheet/actions/spreadsheet_component";
import { OdooSpreadsheetModel } from "@spreadsheet/model";
import { OdooDataProvider } from "@spreadsheet/data_sources/odoo_data_provider";
import { waitForDataLoaded } from "@spreadsheet/helpers/model";
import { SourceDialog, SourcesDialog } from "./source_dialog";
import { SaveSession } from "./save_session";

export class HistoryDialog extends Component {
    static template = "qing_spreadsheet.HistoryDialog";
    static components = { Dialog };
    props = useProps({ close: t.function(), versions: t.array(), canRestore: t.boolean(), restore: t.function() });
    title = _t("Version history");
    async restore(id) { await this.props.restore(id); this.props.close(); }
}

export class ReviewDialog extends Component {
    static template = "qing_spreadsheet.ReviewDialog";
    static components = { Dialog };
    props = useProps({ close: t.function(), review: t.object(), apply: t.function() });
    title = _t("Review record changes");
    async apply() { await this.props.apply(); this.props.close(); }
}

/** Reusable editor: a business addon can mount this component with workbookId. */
export class WorkbookEditor extends Component {
    static template = "qing_spreadsheet.WorkbookEditor";
    static components = { SpreadsheetComponent };
    props = useProps({ ...standardActionServiceProps, action: t.object().optional(), workbookId: t.number().optional() });
    actionLabels = {
        save: _t("Save"),
        retryEditing: _t("Try editing"),
        metadata: _t("Details and sharing"),
        history: _t("History"),
        workbook: _t("Workbook"),
        insertSource: _t("Insert Odoo data"),
        manageSources: _t("Data sources"),
        refresh: _t("Refresh data"),
        reviewRecord: _t("Review and apply to record"),
        reloadRecord: _t("Reload record"),
    };
    setup() {
        this.orm = useService("orm");
        this.actions = useService("action");
        this.http = useService("http");
        this.notification = useService("notification");
        this.addDialog = useOwnedDialogs();
        this.id = this.props.workbookId || this.props.action.params.workbook_id;
        this.storage = window.sessionStorage;
        this.storageKey = `qing_sheet:${session.db}:${user.userId}:${this.id}`;
        this.state = proxy({ name: "", editable: false, canManage: false, canExport: false, bound: false, busy: false, dirty: false, pending: false, error: "", message: "", modelKey: 0, version: 0 });
        this.call = (method, args = []) => this.orm.call("qing.spreadsheet", method, [[this.id], ...args]);
        this.destroyed = false;
        useSetupAction({
            beforeLeave: () => this.beforeLeave(),
            beforeUnload: (event) => {
                if (this.state.dirty || this.state.pending) {
                    this.keepDraft();
                    event.preventDefault();
                    event.returnValue = "";
                }
            },
        });
        onWillStart(() => this.load());
        onWillDestroy(() => {
            this.destroyed = true;
            clearTimeout(this.autosaveTimer);
            clearTimeout(this.draftTimer);
            clearInterval(this.heartbeat);
            this.provider?.removeEventListener("data-source-updated", this.onDataUpdate);
            this.model?.off("update", this);
            this.model?.leaveSession({ shouldSnapshot: false });
            if (this.token) {
                this.call("release_lease", [this.token]).catch(() => {});
            }
        });
    }
    get status() {
        if (this.state.busy) { return _t("Saving…"); }
        if (this.state.pending) { return _t("Save needs recovery"); }
        if (!this.state.editable) { return _t("View only"); }
        return this.state.dirty ? _t("Unsaved changes") : _t("Saved · version %s", this.state.version);
    }
    async load({ recover = true } = {}) {
        clearInterval(this.heartbeat);
        const book = await this.call("open_workbook");
        this.book = book;
        Object.assign(this.state, { name: book.name, bound: book.bound, canManage: book.can_manage, canExport: book.can_export, version: book.version, editable: false, message: "", error: "" });
        const previousLease = this.token;
        this.token = null;
        if (book.can_edit && window.innerWidth >= 768) {
            const previousToken = this.storage.getItem(`${this.storageKey}:token`);
            let lease;
            try { lease = await this.call("acquire_lease", [previousToken]); }
            catch { lease = await this.call("acquire_lease"); }
            if (lease.acquired) {
                this.token = lease.token;
                this.storage.setItem(`${this.storageKey}:token`, lease.token);
                this.state.editable = true;
                // A write may have committed between the initial read and lock acquisition.
                if (book.version !== lease.version) { return this.load({ recover }); }
                this.heartbeat = setInterval(() => this.renew(), 30000);
            } else {
                this.state.message = _t("%s is editing. You can view this workbook or try editing again after they close it.", lease.user);
            }
        } else if (window.innerWidth < 768) {
            if (previousLease) { await this.call("release_lease", [previousLease]).catch(() => {}); }
            this.state.message = _t("Use a desktop browser to edit. Mobile viewing is available.");
        }
        this.saveSession = new SaveSession({ call: this.call, storage: this.storage, key: `${this.storageKey}:pending`, version: book.version, token: this.token,
            onChange: () => {
                this.state.pending = Boolean(this.saveSession?.pending);
                if (this.saveSession?.storageError) {
                    this.state.message = _t("This browser could not retain the draft. Keep this tab open until the server confirms a save.");
                }
            } });
        let data = book.data;
        const draft = recover ? JSON.parse(this.storage.getItem(`${this.storageKey}:draft`) || "null") : null;
        if (draft) {
            data = draft.data;
            this.state.message = draft.version === book.version ? _t("Recovered unsaved changes from this tab. Review and save them.") : _t("Recovered draft differs from the server version. Save a copy or reload; it will not overwrite the server.");
            this.saveSession.version = draft.version;
        }
        if (recover && this.saveSession.pending?.data?.sheets) { data = this.saveSession.pending.data; }
        this.setModel(data);
        this.state.dirty = Boolean(draft || this.saveSession.pending);
        this.state.pending = Boolean(this.saveSession.pending);
        this.lastSavedRevision = book.data.revisionId;
    }
    setModel(data) {
        this.provider?.removeEventListener("data-source-updated", this.onDataUpdate);
        this.model?.off("update", this);
        this.model?.leaveSession({ shouldSnapshot: false });
        const companyId = this.book.company_id;
        const orm = this.orm;
        const workbookId = this.id;
        // All native providers in this editor share a company-scoped, current-user ORM.
        const scopedOrm = new Proxy(orm, { get(target, key) {
            if (key !== "call") {
                const value = target[key];
                return typeof value === "function" ? value.bind(scopedOrm) : value;
            }
            return (model, method, args = [], kwargs = {}, options) => {
                const limit = kwargs.limit;
                if (["web_search_read", "search_read"].includes(method) && (!limit || limit > 5000)) {
                    return Promise.reject(new Error(_t("Record lists are limited to 5,000 rows. Use a pivot table for larger totals.")));
                }
                if (["web_search_read", "search_read", "search_count", "formatted_read_group", "formatted_read_grouping_sets"].includes(method)) {
                    return orm.call("qing.spreadsheet", "query_data", [[workbookId], model, method, args, kwargs]);
                }
                return orm.call(model, method, args, { ...kwargs, context: { ...kwargs.context, allowed_company_ids: [companyId] } }, options);
            };
        } });
        const env = { ...this.env, services: { ...this.env.services, orm: scopedOrm } };
        this.provider = new OdooDataProvider(env);
        this.model = new OdooSpreadsheetModel(data, { mode: this.state.editable ? "normal" : "readonly", custom: { env, odooDataProvider: this.provider } });
        this.onDataUpdate = () => this.model.dispatch("EVALUATE_CELLS");
        this.provider.addEventListener("data-source-updated", this.onDataUpdate);
        this.currentRevision = this.model.session.getRevisionId();
        this.model.on("update", this, () => {
            const revision = this.model.session.getRevisionId();
            if (revision === this.currentRevision || !this.state.editable) { return; }
            this.currentRevision = revision;
            this.state.dirty = true;
            clearTimeout(this.draftTimer);
            this.draftTimer = setTimeout(() => this.keepDraft(), 750);
            clearTimeout(this.autosaveTimer);
            this.autosaveTimer = setTimeout(() => {
                if (!this.state.pending && !this.state.busy) { this.save(); }
            }, 15000);
        });
        this.state.modelKey++;
    }
    keepDraft() {
        if (!this.model || !this.state.dirty) { return; }
        try {
            this.storage.setItem(`${this.storageKey}:draft`, JSON.stringify({ version: this.saveSession.version, data: this.model.exportData() }));
        } catch {
            this.state.error = _t("This browser could not retain the draft. Keep this tab open until the server confirms a save.");
        }
    }
    error(error) {
        const recoverable = {
            "Resolve the pending save first.": _t("Recover the pending save before submitting another change."),
            "A newer server version exists. Keep this draft as a copy before reloading.": _t("The server version changed. Keep your draft as a copy and reload."),
            "The server version changed. Keep this draft as a copy before reloading.": _t("The server version changed. Keep your draft as a copy and reload."),
            "Wait for the current request to finish.": _t("Wait for the current request to finish."),
        };
        this.state.error = recoverable[error.message] || error.data?.message || error.message || String(error);
    }
    async run(callback) {
        if (this.state.busy) { return; }
        this.state.busy = true;
        this.state.error = "";
        try { return await callback(); }
        catch (error) { this.error(error); return false; }
        finally { if (!this.destroyed) { this.state.busy = false; } }
    }
    async save() {
        if (!this.state.editable || !this.state.dirty) { return !this.state.dirty; }
        return this.run(async () => {
            const data = this.model.exportData();
            this.keepDraft();
            await this.saveSession.submit(data);
            this.saved(data.revisionId);
            return true;
        });
    }
    saved(revision) {
        this.state.version = this.saveSession.version;
        this.state.dirty = this.model.session.getRevisionId() !== revision;
        if (!this.state.dirty) { this.storage.removeItem(`${this.storageKey}:draft`); this.state.error = ""; }
        else { this.keepDraft(); }
        this.state.message = "";
    }
    async recoverSave() {
        return this.run(async () => {
            const pending = this.saveSession.pending;
            await this.saveSession.retry();
            if (pending.method === "save_workbook") { this.saved(pending.data.revisionId); }
            else { this.storage.removeItem(`${this.storageKey}:draft`); await this.load({ recover: false }); }
        });
    }
    async renew() {
        if (this.destroyed || !this.token) { return; }
        try {
            const lease = await this.call("renew_lease", [this.token]);
            if (lease.version !== this.state.version && !this.saveSession.running) {
                throw new Error(_t("The server version changed. Keep your draft as a copy and reload."));
            }
        } catch (error) {
            this.keepDraft();
            clearInterval(this.heartbeat);
            this.state.editable = false;
            this.model.updateMode("readonly");
            this.error(error);
        }
    }
    async beforeLeave() {
        if (this.state.busy) { return false; }
        if (!this.state.dirty && !this.state.pending) { return true; }
        this.keepDraft();
        return new Promise((resolve) => this.addDialog(ConfirmationDialog, {
            body: _t("This workbook has unsaved changes. Stay to save or recover them, or leave with the draft retained in this browser tab."),
            confirmLabel: _t("Leave"), cancelLabel: _t("Stay"), confirm: () => resolve(true), cancel: () => resolve(false),
        }));
    }
    confirm(body, action) { this.addDialog(ConfirmationDialog, { body, confirm: () => this.run(action) }); }
    reload() {
        this.confirm(_t("Discard the local draft and pending request, then load the latest saved version?"), async () => {
            this.saveSession.discard();
            this.storage.removeItem(`${this.storageKey}:draft`);
            await this.load({ recover: false });
        });
    }
    retryEditing() { return this.run(() => this.load()); }
    async metadata() {
        await this.actions.doAction({ type: "ir.actions.act_window", res_model: "qing.spreadsheet", res_id: this.id, views: [[false, "form"]], target: "new" });
    }
    async copyDraft() {
        return this.run(async () => {
            const action = await this.orm.call("qing.spreadsheet", "import_workbook", [_t("%s (copy)", this.state.name), this.model.exportData(), this.book.company_id]);
            this.notification.add(_t("A private copy was created."), { type: "success" });
            // Keep the original draft until the user explicitly reloads it.
            this.actions.doAction(action, { newWindow: true });
        });
    }
    insertSource() {
        this.addDialog(SourceDialog, { companyId: this.book.company_id, onInsert: ({ definition, kind, limit }) => {
            const position = { sheetId: this.model.getters.getActiveSheetId(), ...this.model.getters.getActivePosition() };
            let result;
            if (kind === "list") {
                result = this.model.dispatch("INSERT_ODOO_LIST", { ...position, listId: this.model.getters.getNextListId(), linesNumber: limit, definition, mode: "dynamic" });
            } else {
                const pivotId = crypto.randomUUID();
                result = this.model.dispatch("ADD_PIVOT", { pivotId, pivot: definition });
                if (result.isSuccessful) {
                    this.model.dispatch("UPDATE_CELL", { ...position, content: `=PIVOT(${this.model.getters.getPivotFormulaId(pivotId)})` });
                }
            }
            if (!result.isSuccessful) { this.state.error = _t("The data could not be inserted at this position."); }
        } });
    }
    manageSources() {
        const sources = this.model.getters.getListIds().map((id) => ({ id, kind: "list", definition: this.model.getters.getListDefinition(id) }));
        for (const id of this.model.getters.getPivotIds()) {
            const definition = this.model.getters.getPivotCoreDefinition(id);
            if (definition.type === "ODOO") { sources.push({ id, kind: "pivot", definition }); }
        }
        this.addDialog(SourcesDialog, { sources, edit: (source) => this.addDialog(SourceDialog, {
            initial: source.definition, kind: source.kind, companyId: this.book.company_id,
            onInsert: ({ definition }) => {
                const result = source.kind === "list"
                    ? this.model.dispatch("UPDATE_ODOO_LIST", { listId: source.id, list: { ...source.definition, ...definition } })
                    : this.model.dispatch("UPDATE_PIVOT", { pivotId: source.id, pivot: { ...source.definition, ...definition } });
                if (!result.isSuccessful && !result.isCancelledBecause("ListDefinitionUnchanged")) { this.state.error = _t("The data source could not be updated."); }
            },
        }) });
    }
    refresh() { this.model.dispatch("REFRESH_ALL_DATA_SOURCES"); }
    async history() {
        return this.run(async () => {
            const versions = await this.call("list_versions");
            this.addDialog(HistoryDialog, { versions, canRestore: this.state.editable && !this.state.pending, restore: (id) => {
                this.confirm(_t("Replace the current draft with this version? A new version will be saved."), async () => {
                    await this.saveSession.submit(id, "restore_version");
                    this.storage.removeItem(`${this.storageKey}:draft`);
                    await this.load({ recover: false });
                });
            } });
        });
    }
    async importFile(event) {
        const file = event.target.files[0];
        event.target.value = "";
        if (!file) { return; }
        return this.run(async () => {
            if (file.size > 20 * 1024 * 1024) { throw new Error(_t("The file exceeds the 20 MB limit.")); }
            const response = await this.http.post("/qing_spreadsheet/import", { file, csrf_token: odoo.csrf_token }, "json");
            if (response.error) { throw new Error(response.error); }
            const imported = new OdooSpreadsheetModel(response.data, { custom: { env: this.env, odooDataProvider: new OdooDataProvider(this.env) } });
            let data;
            try { data = imported.exportData(); } finally { imported.leaveSession({ shouldSnapshot: false }); }
            const action = await this.orm.call("qing.spreadsheet", "import_workbook", [response.name, data, this.book.company_id]);
            this.notification.add(_t("Imported as a new workbook. Review formulas, dates and charts before relying on its results."), { type: "warning", sticky: true });
            this.state.busy = false;
            await this.actions.doAction(action);
        });
    }
    async exportFile(format) {
        return this.run(async () => {
            const data = { format };
            if (format === "xlsx") {
                let timeout;
                try {
                    await Promise.race([waitForDataLoaded(this.model), new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error(_t("Data is still loading. Refresh and try exporting again."))), 30000); })]);
                } finally { clearTimeout(timeout); }
                const exported = await this.model.exportXLSX();
                data.files = new Blob([JSON.stringify(exported.files)], { type: "application/json" });
            }
            await download({ url: `/qing_spreadsheet/${this.id}/export`, data });
        });
    }
    recordData() { return this.model._exportData(false); }
    async reviewRecord() {
        return this.run(async () => {
            const data = this.recordData();
            const review = await this.call("preview_record", [data, this.saveSession.version, this.token]);
            this.addDialog(ReviewDialog, { review, apply: () => this.run(async () => {
                await this.saveSession.submit(data, "apply_record");
                this.storage.removeItem(`${this.storageKey}:draft`);
                await this.load({ recover: false });
            }) });
        });
    }
    reloadRecord() {
        this.confirm(_t("Replace this draft with the current business record? Saved versions remain available."), async () => {
            await this.saveSession.submit(null, "reload_record");
            this.storage.removeItem(`${this.storageKey}:draft`);
            await this.load({ recover: false });
        });
    }
}
registry.category("lazy_components").add("qing_spreadsheet.WorkbookEditor", WorkbookEditor);
