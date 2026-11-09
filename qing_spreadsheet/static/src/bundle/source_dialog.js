import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { DomainSelector } from "@web/core/domain_selector/domain_selector";
import { Domain } from "@web/core/domain";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class SourceDialog extends Component {
    static template = "qing_spreadsheet.SourceDialog";
    static components = { Dialog, DomainSelector };
    props = useProps({ close: t.function(), onInsert: t.function(), companyId: t.number(), initial: t.object().optional(), kind: t.string().optional() });
    get title() { return this.props.initial ? _t("Edit data source") : _t("Insert Odoo data"); }
    setup() {
        this.orm = useService("orm");
        this.state = proxy({ sources: [], model: "", fields: [], kind: "list", group: "", measure: "__count", aggregate: "sum", limit: 100, domain: "[]", error: "" });
        onWillStart(async () => {
            this.state.sources = await this.orm.call("qing.spreadsheet.source", "available_sources", []);
            this.selectModel(this.props.initial?.model || this.state.sources[0]?.model || "");
            if (this.props.initial) {
                const initial = this.props.initial;
                this.state.kind = this.props.kind;
                this.state.domain = new Domain(initial.domain || []).toString();
                if (this.state.kind === "list") {
                    this.state.fields = initial.columns.map((column) => typeof column === "string" ? column : column.name);
                } else {
                    this.state.group = initial.rows[0]?.fieldName || this.state.group;
                    this.state.measure = initial.measures[0]?.fieldName || "__count";
                    this.state.aggregate = initial.measures[0]?.aggregator || "sum";
                }
            }
        });
    }
    get source() { return this.state.sources.find((source) => source.model === this.state.model); }
    get numericFields() { return this.source?.fields.filter((field) => ["float", "integer", "monetary"].includes(field.type)) || []; }
    selectModel(model) {
        this.state.model = model;
        this.state.fields = this.source?.fields.slice(0, 3).map((field) => field.name) || [];
        this.state.group = this.source?.fields[0]?.name || "";
        this.state.domain = "[]";
    }
    toggleField(name, checked) {
        this.state.fields = checked ? [...this.state.fields, name] : this.state.fields.filter((field) => field !== name);
    }
    async insert() {
        try {
            if (!this.source || (this.state.kind === "list" && !this.state.fields.length)) {
                throw new Error(_t("Choose at least one field."));
            }
            const limit = Number(this.state.limit);
            if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
                throw new Error(_t("Choose between 1 and 5,000 rows."));
            }
            const definition = {
                name: this.source.name, model: this.source.model, domain: new Domain(this.state.domain).toList(),
                context: { allowed_company_ids: [this.props.companyId] }, orderBy: [],
                columns: this.state.fields.map((name) => ({ name, string: this.source.fields.find((field) => field.name === name).string })),
            };
            if (this.state.kind === "pivot") {
                const group = this.source.fields.find((field) => field.name === this.state.group);
                Object.assign(definition, {
                    type: "ODOO", columns: [], rows: [{ fieldName: group.name, ...( ["date", "datetime"].includes(group.type) ? { granularity: "month" } : {} ) }],
                    measures: [{ id: this.state.measure === "__count" ? "__count" : `${this.state.measure}:${this.state.aggregate}`, fieldName: this.state.measure, aggregator: this.state.measure === "__count" ? "count" : this.state.aggregate }],
                });
            }
            await this.orm.call("qing.spreadsheet.source", "validate_source", [[this.source.id], definition, this.props.companyId]);
            this.props.onInsert({ definition, kind: this.state.kind, limit });
            this.props.close();
        } catch (error) {
            this.state.error = error.data?.message || error.message;
        }
    }
}

export class SourcesDialog extends Component {
    static template = "qing_spreadsheet.SourcesDialog";
    static components = { Dialog };
    props = useProps({ close: t.function(), sources: t.array(), edit: t.function() });
    title = _t("Data sources");
    edit(source) { this.props.close(); this.props.edit(source); }
}
