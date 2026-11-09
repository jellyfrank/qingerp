/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, proxy, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";


export class FinancialReport extends Component {
    static template = "qing_account_reports.FinancialReport";
    props = useProps();

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.detailsRef = signal.ref();
        this.reportType = this.props.action.params?.report_type || "balance";
        this.state = proxy({
            date: "", dateFrom: "", mode: "monthly", data: null, selectedRow: null,
            selectedColumn: 1, loading: false, error: "",
        });
        this.generation = 0;
        onWillDestroy(() => { this.generation++; });
        onWillStart(() => this.reload());
    }

    get assetRows() {
        return this.state.data?.rows.filter((row) => row.side === "asset") || [];
    }

    get liabilityRows() {
        return this.state.data?.rows.filter((row) => row.side === "liability") || [];
    }

    get selectedDetails() {
        if (!this.state.selectedRow || !this.state.data) return [];
        return (this.state.data.details[String(this.state.selectedRow.code)] || []).filter(
            (detail) => detail.amounts[this.state.selectedColumn] !== ""
        );
    }

    async reload() {
        const generation = ++this.generation;
        this.state.loading = true;
        this.state.error = "";
        this.state.selectedRow = null;
        try {
            const data = await this.orm.call("qing.financial.report", "get_report", [], {
                report_type: this.reportType, date_to: this.state.date || false,
                date_from: this.reportType === "trial_balance" ? this.state.dateFrom || false : false,
                report_mode: this.state.mode,
            });
            if (generation !== this.generation) return;
            this.state.data = data;
            if (!this.state.date && data.date_to) this.state.date = data.date_to;
            if (!this.state.dateFrom && data.date_from) this.state.dateFrom = data.date_from;
        } catch (error) {
            if (generation !== this.generation) return;
            this.state.data = null;
            this.state.error = error.message || String(error);
        } finally {
            if (generation === this.generation) this.state.loading = false;
        }
    }

    changeDate(event) {
        this.state.date = event.target.value;
        this.reload();
    }

    changeTrialDateFrom(event) {
        this.state.dateFrom = event.target.value;
    }

    changeTrialDateTo(event) {
        this.state.date = event.target.value;
    }

    get trialDateRangeValid() {
        return Boolean(this.state.dateFrom && this.state.date && this.state.dateFrom <= this.state.date);
    }

    get trialScopeIsCurrent() {
        return this.state.data?.date_from === this.state.dateFrom
            && this.state.data?.date_to === this.state.date;
    }

    changeMode(event) {
        this.state.mode = event.target.value;
        if (this.state.mode === "annual" && !this.state.date.endsWith("-12-31")) {
            const today = new Date();
            const year = Number(this.state.date.slice(0, 4)) || today.getFullYear();
            this.state.date = `${year - 1}-12-31`;
        }
        this.reload();
    }

    selectRow(row, column) {
        this.state.selectedRow = row;
        this.state.selectedColumn = column;
        requestAnimationFrame(() => this.detailsRef()?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }

    async openRow(row, column) {
        const action = await this.orm.call("qing.financial.report", "open_row_source", [], {
            report_type: this.reportType, date_to: this.state.date,
            row_code: row.code, column, report_mode: this.state.mode,
        });
        return this.action.doAction(action);
    }

    async openAccount(accountId, partnerId, moveLineId) {
        const sourceScope = this.reportType === "balance"
            ? (this.state.selectedColumn === 1 ? "primary" : "secondary")
            : (this.state.selectedColumn === 0 ? "primary" : "secondary");
        const action = await this.orm.call("qing.financial.report", "open_source", [], {
            report_type: this.reportType, date_to: this.state.date,
            account_id: accountId, partner_id: partnerId || false,
            report_mode: this.state.mode, scope: sourceScope,
            move_line_id: moveLineId || false,
        });
        return this.action.doAction(action);
    }

    async classifyCash(moveLineId) {
        const action = await this.orm.call("qing.financial.report", "open_cash_classification", [], {
            date_to: this.state.date, move_line_id: moveLineId,
            report_mode: this.state.mode,
        });
        return this.action.doAction(action);
    }

    async exportCsv() {
        const action = await this.orm.call("qing.financial.report", "export_csv", [], {
            report_type: this.reportType,
            date_to: this.reportType === "trial_balance" ? this.state.data.date_to : this.state.date,
            date_from: this.reportType === "trial_balance" ? this.state.data.date_from : false,
            report_mode: this.state.mode,
        });
        return this.action.doAction(action);
    }

    async openTrialBalanceSources() {
        const action = await this.orm.call("qing.financial.report", "open_trial_balance_lines", [], {
            date_from: this.state.data.date_from, date_to: this.state.data.date_to,
        });
        return this.action.doAction(action);
    }

    async exportDiagnostics() {
        const action = await this.orm.call("qing.financial.report", "export_diagnostics", [], {
            report_type: this.reportType, date_to: this.state.date,
            report_mode: this.state.mode,
        });
        return this.action.doAction(action);
    }

    async exportFormatted(fileType) {
        const action = await this.orm.call("qing.financial.report", "export_formatted", [], {
            report_type: this.reportType, date_to: this.state.date,
            report_mode: this.state.mode, file_type: fileType,
        });
        return this.action.doAction(action);
    }

    openSettings() {
        return this.action.doAction("qing_account_reports.action_financial_report_scheme");
    }
}

registry.category("actions").add("qing_account_reports.financial_report", FinancialReport);
