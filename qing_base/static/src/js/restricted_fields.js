/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { computed, getScope, markRaw, proxy, Scope, toRaw } from "@odoo/owl";
import { Domain } from "@web/core/domain";
import { evaluateBooleanExpr, evaluateExpr, parseExpr } from "@web/core/py_js/py";
import { patch } from "@web/core/utils/patch";
import { Record } from "@web/model/relational_model/record";
import { getBasicEvalContext, getFieldContext } from "@web/model/relational_model/utils";
import { Field } from "@web/views/fields/field";
import { FormLabel } from "@web/views/form/form_label";
import { FormRenderer } from "@web/views/form/form_renderer";
import { ListRenderer } from "@web/views/list/list_renderer";
import { ViewButton } from "@web/views/view_button/view_button";

export const RESTRICTED_FIELDS_KEY = "__qing_restricted_fields";
const contextRecords = new WeakMap();
const partialRecordChecks = new WeakMap();
const aggregateTypes = new Set(["integer", "float", "monetary"]);

export function isRestrictedField(record, name, fieldInfo = {}) {
    if (record?.qingRestrictedFields?.includes(name)) {
        return true;
    }
    if (record?.fields?.[name]?.type === "monetary" || fieldInfo.widget === "monetary") {
        const currencyField = fieldInfo.options?.currency_field ||
            record.fields[name].currency_field || "currency_id";
        return !!record.qingRestrictedFields?.includes(currencyField);
    }
    return false;
}

export function isPartialRecord(record) {
    if (!record) {
        return false;
    }
    const key = toRaw(record);
    let check = partialRecordChecks.get(key);
    if (!check) {
        const reactiveRecord = proxy(record);
        const derive = () => computed(() => {
            if (reactiveRecord.qingRestrictedFields?.length) {
                return true;
            }
            const data = reactiveRecord.data || {};
            // Track keys and relations, not every ordinary field value.
            return Object.keys(data).some((name) =>
                ["one2many", "many2many"].includes(reactiveRecord.fields?.[name]?.type) &&
                data[name]?.records?.some(isPartialRecord)
            );
        });
        // A shared record must not inherit the first field component's lifetime.
        const scope = getScope();
        check = scope ? new Scope(scope.app).run(derive) : derive();
        partialRecordChecks.set(key, check);
    }
    return check();
}

function lookupPath(ast) {
    if (ast.type === 5) {
        return [ast.value];
    }
    if (ast.type === 15) {
        const path = lookupPath(ast.obj);
        return path && [...path, ast.key];
    }
    if (ast.type === 12 && ast.key.type === 1) {
        const path = lookupPath(ast.target);
        return path && [...path, ast.key.value];
    }
}

function hasRestrictedContext(context) {
    let current = context;
    // Normal root contexts take one lookup and never reach the parser.
    for (let depth = 0; current && typeof current === "object" && depth < 32; depth++) {
        if (contextRecords.get(current)?.qingRestrictedFields?.length) {
            return true;
        }
        current = current.parent;
    }
    return false;
}

export function hasRestrictedExpression(expr, context) {
    if (!expr || !context || typeof expr !== "string" || !hasRestrictedContext(context)) {
        return false;
    }
    const visit = (node) => {
        if (!node || typeof node !== "object") {
            return false;
        }
        const path = !Array.isArray(node) && lookupPath(node);
        if (path) {
            let current = context;
            for (const name of path) {
                if (contextRecords.get(current)?.qingRestrictedFields?.includes(name)) {
                    return true;
                }
                current = current?.[name];
                if (!current || typeof current !== "object") {
                    break;
                }
            }
        }
        return Object.values(node).some(visit);
    };
    return visit(parseExpr(expr));
}

function withoutRestricted(record, values = {}) {
    return Object.fromEntries(Object.entries(values || {}).filter(([name]) =>
        name !== RESTRICTED_FIELDS_KEY && !record.qingRestrictedFields?.includes(name)
    ));
}

function readMetadata(record, values, replace = false) {
    if (replace || Object.hasOwn(values, RESTRICTED_FIELDS_KEY)) {
        const names = Object.hasOwn(values, RESTRICTED_FIELDS_KEY) ? values[RESTRICTED_FIELDS_KEY] : [];
        if (!Array.isArray(names) || names.some((name) => typeof name !== "string")) {
            throw new Error("Invalid restricted-field metadata");
        }
        record.qingRestrictedFields = [...new Set(names.filter((name) => name in record.fields))];
    }
    const unavailable = Object.entries(values).filter(([name, value]) =>
        ["many2one", "reference", "many2one_reference"].includes(record.fields[name]?.type) &&
        Array.isArray(value?.[RESTRICTED_FIELDS_KEY]) && value[RESTRICTED_FIELDS_KEY].length
    ).map(([name]) => name);
    if (unavailable.length) {
        record.qingRestrictedFields = [...new Set([...(record.qingRestrictedFields || []), ...unavailable])];
    }
}

function eraseRestricted(record) {
    for (const values of [record._values, record._changes, record.data, record._textValues,
        record._initialTextValues, record.evalContext, record.evalContextWithVirtualIds]) {
        if (values) {
            delete values[RESTRICTED_FIELDS_KEY];
            for (const name of record.qingRestrictedFields || []) {
                delete values[name];
            }
        }
    }
}

patch(Record.prototype, {
    _setData(values, options) {
        readMetadata(this, values, true);
        eraseRestricted(this);
        super._setData(withoutRestricted(this, values), options);
    },

    _getDefaultValues(...args) {
        return withoutRestricted(this, super._getDefaultValues(...args));
    },

    _parseServerValues(values, options) {
        if (values) {
            readMetadata(this, values);
            eraseRestricted(this);
        }
        return super._parseServerValues(withoutRestricted(this, values), options);
    },

    _setEvalContext() {
        eraseRestricted(this);
        contextRecords.set(this.evalContext, this);
        contextRecords.set(this.evalContextWithVirtualIds, this);
        if (!hasRestrictedContext(this.evalContext)) {
            return super._setEvalContext();
        }
        // Native propagation evaluates x2many contexts even on readonly records.
        const basic = getBasicEvalContext(this.config);
        const data = this._computeDataContext();
        Object.assign(this.evalContext, basic, data.withoutVirtualIds);
        Object.assign(this.evalContextWithVirtualIds, basic, data.withVirtualIds);
        this._isEvalContextReady = true;
        if (!this._parentRecord || this._parentRecord._isEvalContextReady) {
            for (const [name, value] of Object.entries(toRaw(this.data))) {
                if (["one2many", "many2many"].includes(this.fields[name].type)) {
                    const expr = this.activeFields[name].context;
                    value._updateContext(getFieldContext(this, name,
                        hasRestrictedExpression(expr, this.evalContext) ? "{}" : expr));
                }
            }
        }
    },

    _getChanges(changes = this._changes, options) {
        // Native force_save and onchange's withReadonly must not override the mask.
        return withoutRestricted(this, super._getChanges(withoutRestricted(this, changes), options));
    },

    _isReadonly(name) {
        return isPartialRecord(this) ||
            hasRestrictedExpression(this.activeFields[name]?.readonly, this.evalContextWithVirtualIds) ||
            super._isReadonly(name);
    },

    _isInvisible(name) {
        return isRestrictedField(this, name) ||
            hasRestrictedExpression(this.activeFields[name]?.invisible, this.evalContextWithVirtualIds) ||
            super._isInvisible(name);
    },

    _isRequired(name) {
        return !isPartialRecord(this) &&
            !hasRestrictedExpression(this.activeFields[name]?.required, this.evalContextWithVirtualIds) &&
            super._isRequired(name);
    },

    _checkValidity(...args) {
        return isPartialRecord(this) || super._checkValidity(...args);
    },

    async _update(...args) {
        if (isPartialRecord(this)) {
            return false;
        }
        return super._update(...args);
    },

    update(...args) {
        return isPartialRecord(this) ? Promise.resolve(false) : super.update(...args);
    },

    async _save(options) {
        if (!isPartialRecord(this)) {
            return super._save(options);
        }
        if (options?.nextId && this.isNew) {
            throw new Error("Cannot set nextId on a new record");
        }
        // Do not enter native x2many/save loops with deliberately omitted fields.
        if (this.isNew || Object.keys(this._getChanges(this._changes, { withReadonly: true })).length) {
            this.model.notification.add(_t("Permission restricted"), { type: "warning" });
            return false;
        }
        if (options?.nextId) {
            return this.model.load({ resId: options.nextId });
        }
        this._changes = markRaw({});
        this.data = { ...this._values };
        this.dirty = false;
        return true;
    },

    _applyChanges(...args) {
        const restricted = [...(this.qingRestrictedFields || [])];
        const values = { ...this._values };
        const undo = super._applyChanges(...args);
        return () => {
            this.qingRestrictedFields = restricted;
            this._values = markRaw(values);
            undo();
            eraseRestricted(this);
        };
    },

    _addSavePoint() {
        super._addSavePoint();
        this._savePoint.qingRestrictedFields = [...(this.qingRestrictedFields || [])];
        this._savePoint.qingValues = { ...this._values };
    },

    _discard() {
        if (this._savePoint?.qingRestrictedFields) {
            this.qingRestrictedFields = this._savePoint.qingRestrictedFields;
            this._values = markRaw(this._savePoint.qingValues);
        }
        eraseRestricted(this);
        super._discard();
    },
});

function hasUnknownFieldModifier(record, fieldInfo = {}) {
    return ["readonly", "required", "invisible", "context", "domain"].some((name) =>
        hasRestrictedExpression(fieldInfo[name], record.evalContextWithVirtualIds)
    );
}

patch(Field.prototype, {
    get qingRestricted() {
        return isRestrictedField(this.props.record, this.props.name, this.props.fieldInfo);
    },

    get qingRestrictedLabel() {
        return _t("Permission restricted");
    },

    get qingReadonly() {
        return isPartialRecord(this.props.record) ||
            hasUnknownFieldModifier(this.props.record, this.props.fieldInfo);
    },

    get classNames() {
        if (!this.qingReadonly) {
            return super.classNames;
        }
        return {
            o_field_widget: true,
            o_readonly_modifier: true,
            o_qing_restricted_field: this.qingRestricted,
            [`o_field_${this.type}`]: true,
            [this.props.class]: !!this.props.class,
        };
    },

    get qingFieldComponentProps() {
        if (!this.qingReadonly) {
            return this.fieldComponentProps;
        }
        const props = { ...this.props };
        for (const name of ["style", "class", "showTooltip", "fieldInfo", "attrs", "type", "readonly"]) {
            delete props[name];
        }
        const { fieldInfo, attrs } = this.props;
        const record = props.record;
        let extracted = {};
        if (fieldInfo && this.field.extractProps) {
            const context = record.evalContext;
            const safeInfo = { ...fieldInfo, readonly: "True", required: "False",
                attrs: { ...fieldInfo.attrs, ...attrs } };
            extracted = this.field.extractProps(safeInfo, {
                context: hasRestrictedExpression(fieldInfo.context, context) ? {} :
                    getFieldContext(record, fieldInfo.name, fieldInfo.context),
                domain: () => hasRestrictedExpression(fieldInfo.domain, context) ? [] :
                    fieldInfo.domain ? new Domain(evaluateExpr(fieldInfo.domain, context)).toList() : undefined,
                required: false,
                readonly: true,
            });
        }
        return { ...extracted, ...props, readonly: true };
    },
});

patch(FormLabel.prototype, {
    get className() {
        if (isPartialRecord(this.props.record) ||
            hasUnknownFieldModifier(this.props.record, this.props.fieldInfo)) {
            return `${this.props.className || ""} o_form_label_readonly`;
        }
        return super.className;
    },
});

patch(FormRenderer.prototype, {
    setup() {
        super.setup();
        this.evaluateBooleanExpr = (expr, context) =>
            hasRestrictedExpression(expr, context) || evaluateBooleanExpr(expr, context);
    },
});

function aggregateColumns(renderer) {
    return renderer.columns.flatMap((column) =>
        column.type === "column_group" ? column.fields : column.type === "field" ? [column] : []
    ).filter((column) => aggregateTypes.has(renderer.fields[column.name]?.type) &&
        ["sum", "avg", "min", "max"].some((aggregate) => column.attrs?.[aggregate]));
}

patch(ListRenderer.prototype, {
    qingCellInvisible(column, record) {
        return this.evalInvisible(column.invisible, record) ||
            (!isRestrictedField(record, column.name, column) && !(column.name in record.data));
    },

    isColumnGroupFieldVisible(column, fieldInfo, record) {
        if (isRestrictedField(record, fieldInfo.name, fieldInfo)) {
            return !this.evalInvisible(fieldInfo.invisible, record) &&
                !(fieldInfo.optional && !this.optionalActiveFields[fieldInfo.name]);
        }
        return super.isColumnGroupFieldVisible(...arguments);
    },

    getFormattedValue(column, record) {
        return isRestrictedField(record, column.name, column) ? _t("Permission restricted") :
            super.getFormattedValue(...arguments);
    },

    isRecordReadonly(record) {
        return isPartialRecord(record) || super.isRecordReadonly(record);
    },

    isFieldReadonly(column, record) {
        return isPartialRecord(record) || hasUnknownFieldModifier(record, column) ||
            super.isFieldReadonly(column, record);
    },

    evalInvisible(expr, record) {
        return hasRestrictedExpression(expr, record.evalContextWithVirtualIds) ||
            super.evalInvisible(...arguments);
    },

    evalColumnInvisible(expr) {
        return hasRestrictedExpression(expr, this.props.list.evalContext) ||
            super.evalColumnInvisible(expr);
    },

    getCellClass(column, record) {
        if (!isPartialRecord(record) && !hasUnknownFieldModifier(record, column)) {
            return super.getCellClass(...arguments);
        }
        return ["o_data_cell", "o_readonly_modifier", column.type === "button_group" ? "o_list_button" : "o_field_cell",
            aggregateTypes.has(this.fields[column.name]?.type) ? "o_list_number" : "",
            isRestrictedField(record, column.name, column) ? "o_qing_restricted_field" : "",
            column.attrs?.class || ""].filter(Boolean).join(" ");
    },

    getColumnGroupFieldClasses(fieldInfo, record) {
        return isPartialRecord(record) || hasUnknownFieldModifier(record, fieldInfo) ? "o_readonly_modifier" :
            super.getColumnGroupFieldClasses(...arguments);
    },

    getRowClass(record) {
        if (!isPartialRecord(record)) {
            return super.getRowClass(record);
        }
        return [record.selected ? "table-info o_data_row_selected" : "", "o_qing_partial_record"].join(" ");
    },

    getDecorationClassNames(column, record) {
        return isPartialRecord(record) ? [] : super.getDecorationClassNames(...arguments);
    },

    get canResequenceRows() {
        return !this.props.list.records.some(isPartialRecord) && super.canResequenceRows;
    },

    computeAggregates() {
        const result = super.computeAggregates();
        const list = this.props.list;
        const records = list.selection.length ? list.selection : list.records;
        for (const column of aggregateColumns(this)) {
            if (records.some((record) => isRestrictedField(record, column.name, column))) {
                result[column.name] = { value: _t("Permission restricted"), help: _t("Permission restricted"), multiCurrency: false };
            }
        }
        return result;
    },

    formatGroupAggregate(group, column) {
        const columns = column.type === "column_group" ? column.fields : [column];
        if (columns.some((field) => group.list.records.some((record) => isRestrictedField(record, field.name, field)))) {
            return { value: _t("Permission restricted"), multiCurrency: false };
        }
        return super.formatGroupAggregate(...arguments);
    },
});

patch(ViewButton.prototype, {
    get qingRestricted() {
        const record = this.props.record;
        return this.clickParams.special !== "cancel" && (isPartialRecord(record) ||
            ["invisible", "column_invisible", "readonly", "required"].some((name) =>
                hasRestrictedExpression(this.props.attrs?.[name], record?.evalContextWithVirtualIds)) ||
            hasRestrictedExpression(this.clickParams.context, record?.evalContextWithVirtualIds));
    },

    get disabled() {
        return this.qingRestricted || super.disabled;
    },

    onClick(...args) {
        if (this.disabled) {
            return;
        }
        return super.onClick(...args);
    },
});
