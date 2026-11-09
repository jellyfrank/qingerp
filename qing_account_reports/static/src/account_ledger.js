/** @odoo-module **/

import { Component, onWillStart, onWillDestroy, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class AccountLedger extends Component {
    static template = "qing_account_reports.AccountLedger";
    props = useProps();

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        const today = new Date();
        const end = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
        this.state = proxy({ accounts: [], account: "", start: `${today.getFullYear()}-01-01`, end,
            data: null, loading: false, error: "" });
        this.request = 0;
        onWillDestroy(() => this.request++);
        onWillStart(async () => {
            try {
                this.state.accounts = await this.orm.call("qing.account.ledger", "get_accounts", []);
                this.state.account = String(this.state.accounts[0]?.id || "");
                if (this.state.account) await this.load();
            } catch (error) {
                this.state.error = error.data?.message || error.message;
            }
        });
    }

    get scope() { return [Number(this.state.account), this.state.start, this.state.end]; }
    changeScope(field, event) { this.state[field] = event.target.value; }
    get valid() { return this.state.account && this.state.start && this.state.end && this.state.start <= this.state.end; }
    get current() {
        const data = this.state.data;
        return data && data.account_id === Number(this.state.account) && data.date_from === this.state.start && data.date_to === this.state.end;
    }
    money(value) { return value === false ? "" : Number(value).toLocaleString(undefined, { minimumFractionDigits: this.state.data.precision, maximumFractionDigits: this.state.data.precision }); }

    async load(offset = 0) {
        const request = ++this.request;
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("qing.account.ledger", "get_report", [...this.scope, offset]);
            if (request === this.request) this.state.data = data;
        } catch (error) {
            if (request === this.request) this.state.error = error.data?.message || error.message;
        } finally {
            if (request === this.request) this.state.loading = false;
        }
    }

    async download() {
        try {
            const action = await this.orm.call("qing.account.ledger", "export_csv", this.scope);
            await this.action.doAction(action);
        } catch (error) { this.state.error = error.data?.message || error.message; }
    }

    openMove(row) {
        return this.action.doAction({ type: "ir.actions.act_window", name: _t("Journal Entry"),
            res_model: "account.move", res_id: row.move_id, views: [[false, "form"]] });
    }
}

registry.category("actions").add("qing_account_reports.account_ledger", AccountLedger);
