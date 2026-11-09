/** @odoo-module **/
import { Component, onWillStart, onWillDestroy, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export class BankWorkbench extends Component {
    static template = "qing_account.BankWorkbench";
    props = useProps();
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = proxy({ lines: [], journals: [], company: "", journal: "", from: "", to: "",
            offset: 0, more: false, line: null, candidates: [], selected: null, query: "",
            notice: "", busy: false, loading: false, lastMatched: null, skipped: [] });
        this.generation = 0;
        onWillDestroy(() => { this.generation++; });
        onWillStart(() => this.reload());
    }
    async reload(reset = false) {
        if (this.state.busy) return;
        const generation = ++this.generation;
        this.state.loading = true;
        this.state.selected = null;
        if (reset) { this.state.offset = 0; this.state.skipped = []; }
        try {
            const data = await this.orm.call("account.bank.statement.line", "qing_workbench_queue", [], {
                journal_id: Number(this.state.journal) || false, date_from: this.state.from || false,
                date_to: this.state.to || false, offset: this.state.offset,
            });
            if (generation !== this.generation) return;
            Object.assign(this.state, { lines: data.lines, journals: data.journals, company: data.company, more: data.more,
                line: null, candidates: [], notice: "" });
            const next = data.lines.find(line => !this.state.skipped.includes(line.id));
            if (next) await this.selectLine(next);
        } finally { if (generation === this.generation) this.state.loading = false; }
    }
    async selectLine(line) {
        if (this.state.busy) return;
        this.state.line = line;
        this.state.query = "";
        await this.findCandidates();
    }
    async findCandidates() {
        if (!this.state.line || this.state.busy) return;
        const generation = ++this.generation;
        const id = this.state.line.id;
        this.state.loading = true;
        this.state.selected = null;
        this.state.candidates = [];
        try {
            const data = await this.orm.call("account.bank.statement.line", "qing_workbench_candidates", [[id]], { query: this.state.query });
            if (generation !== this.generation) return;
            Object.assign(this.state, { line: data.line, candidates: data.candidates, notice: data.notice });
        } finally { if (generation === this.generation) this.state.loading = false; }
    }
    async confirm() {
        if (!this.state.selected || this.state.busy || this.state.loading) return;
        const id = this.state.line.id;
        const selected = this.state.selected;
        this.state.busy = true;
        try {
            await this.orm.call("account.bank.statement.line", "qing_workbench_match", [[id], selected.id, selected.token]);
            this.state.lastMatched = id;
            this.state.selected = null;
            this.notification.add("已完成银行匹配", { type: "success" });
        } finally { this.state.busy = false; }
        await this.reload();
    }
    async skip() {
        if (!this.state.line || this.state.busy || this.state.loading) return;
        this.state.skipped.push(this.state.line.id);
        const next = this.state.lines.find(line => !this.state.skipped.includes(line.id));
        if (next) { await this.selectLine(next); return; }
        this.state.line = null;
        this.state.selected = null;
        this.state.candidates = [];
    }
    async page(delta) {
        if (this.state.busy || this.state.loading) return;
        this.state.offset = Math.max(0, this.state.offset + delta * 30);
        this.state.skipped = [];
        await this.reload();
    }
    undo() {
        if (!this.state.lastMatched || this.state.busy || this.state.loading) return;
        const id = this.state.lastMatched;
        this.dialog.add(ConfirmationDialog, {
            body: "撤销上次银行匹配？已登记收付款及其发票核销会保留。",
            confirm: async () => {
                this.state.busy = true;
                try {
                    await this.orm.call("account.bank.statement.line", "action_qing_undo_match", [[id]]);
                    this.state.lastMatched = null;
                } finally { this.state.busy = false; }
                await this.reload(true);
            },
        });
    }
    openPayment() {
        if (!this.state.selected) return;
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.payment",
            res_id: this.state.selected.id, views: [[false, "form"]], target: "new" });
    }
}
registry.category("actions").add("qing_account.bank_workbench", BankWorkbench);
