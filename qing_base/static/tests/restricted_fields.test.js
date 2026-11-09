/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { App, Component, effect, markRaw, proxy, Scope } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, findComponent, isSmall, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import { FormController } from "@web/views/form/form_controller";
import { Field } from "@web/views/fields/field";
import { ListController } from "@web/views/list/list_controller";
import { ViewButton } from "@web/views/view_button/view_button";
import { hasRestrictedExpression, isPartialRecord, isRestrictedField, RESTRICTED_FIELDS_KEY } from "@qing_base/js/restricted_fields";

const RES_MODEL = "qing.restricted.record";

function permissionRecord(data = {}, fields = {}) {
    return proxy({ data, fields: markRaw(fields), qingRestrictedFields: [] });
}

test("partial state never reads ordinary field values", () => {
    let reads = 0;
    const data = { name: "Readable" };
    Object.defineProperty(data, "count", { enumerable: true, get() { reads++; return 42; } });
    const record = permissionRecord(data, { name: { type: "char" }, count: { type: "integer" } });
    expect(isPartialRecord(record)).toBe(false);
    expect(isPartialRecord(record)).toBe(false);
    expect(reads).toBe(0);
    record.qingRestrictedFields.push("count");
    expect(isPartialRecord(record)).toBe(true);
    record.qingRestrictedFields.splice(0);
    expect(isPartialRecord(record)).toBe(false);
    expect(isPartialRecord(null)).toBe(false);
});

test("partial state follows nested masks and x2many membership synchronously", () => {
    const child = permissionRecord({ name: "Line" });
    const record = permissionRecord({ lines: { records: [child] } }, { lines: { type: "one2many" } });
    expect(isPartialRecord(record)).toBe(false);
    child.qingRestrictedFields = ["secret"];
    expect(isPartialRecord(record)).toBe(true);
    child.qingRestrictedFields = [];
    expect(isPartialRecord(record)).toBe(false);
    const blocked = permissionRecord();
    blocked.qingRestrictedFields = ["secret"];
    record.data.lines.records.push(blocked);
    expect(isPartialRecord(record)).toBe(true);
    record.data.lines.records.pop();
    expect(isPartialRecord(record)).toBe(false);
    child.data.tags = { records: [blocked] };
    child.fields.tags = { type: "many2many" };
    expect(isPartialRecord(record)).toBe(true);
    child.data.tags.records = [];
    expect(isPartialRecord(record)).toBe(false);
});

test("new relation keys, replacement data and views invalidate partial state", () => {
    const child = permissionRecord();
    child.qingRestrictedFields = ["secret"];
    const record = permissionRecord({ name: "Readable" });
    expect(isPartialRecord(record)).toBe(false);
    record.fields.lines = { type: "one2many" };
    record.data.lines = { records: [child] };
    expect(isPartialRecord(record)).toBe(true);
    delete record.data.lines;
    expect(isPartialRecord(record)).toBe(false);
    record.data = { lines: { records: [child] } };
    expect(isPartialRecord(record)).toBe(true);
    record.fields = markRaw({ lines: { type: "char" } });
    expect(isPartialRecord(record)).toBe(false);
    record.fields = markRaw({ lines: { type: "many2many" } });
    expect(isPartialRecord(record)).toBe(true);
    record.data.lines = { records: [] };
    expect(isPartialRecord(record)).toBe(false);
});

test("shared partial state survives the first caller scope being destroyed", async () => {
    const child = permissionRecord();
    const record = permissionRecord({ lines: { records: [child] } }, { lines: { type: "one2many" } });
    const first = new Scope(new App(Component));
    first.run(() => expect(isPartialRecord(record)).toBe(false));
    const observed = [];
    const stop = effect(() => observed.push(isPartialRecord(record)));
    try {
        first.finalize((error) => { throw error; });
        child.qingRestrictedFields = ["secret"];
        await animationFrame();
        expect(observed).toEqual([false, true]);
        child.qingRestrictedFields = [];
        await animationFrame();
        expect(observed).toEqual([false, true, false]);
    } finally {
        stop();
    }
});

test("150 ordinary line edits do not rescan child permission metadata", () => {
    let reads = 0;
    const children = Array.from({ length: 150 }, () => {
        const child = { data: { quantity: 1 }, fields: markRaw({ quantity: { type: "float" } }) };
        Object.defineProperty(child, "qingRestrictedFields", { get() { reads++; return []; } });
        return proxy(child);
    });
    const record = permissionRecord({ lines: { records: children } }, { lines: { type: "one2many" } });
    expect(isPartialRecord(record)).toBe(false);
    const initial = reads;
    for (const child of children) {
        child.data.quantity++;
        expect(isPartialRecord(record)).toBe(false);
    }
    expect(reads).toBe(initial);
    const blocked = permissionRecord();
    blocked.qingRestrictedFields = ["secret"];
    record.data.lines.records.push(blocked);
    expect(isPartialRecord(record)).toBe(true);
    record.data.lines.records.pop();
    expect(isPartialRecord(record)).toBe(false);
});

test("ordinary line edits do not rerun parent permission consumers", async () => {
    const child = permissionRecord({ quantity: 1 }, { quantity: { type: "float" } });
    const record = permissionRecord({ lines: { records: [child] } }, { lines: { type: "one2many" } });
    const observed = [];
    const stop = effect(() => observed.push(isPartialRecord(record)));
    try {
        child.data.quantity = 2;
        await animationFrame();
        expect(observed).toEqual([false]);
        child.data = { quantity: 3 };
        await animationFrame();
        expect(observed).toEqual([false]);
        child.qingRestrictedFields = ["secret"];
        await animationFrame();
        expect(observed).toEqual([false, true]);
        child.qingRestrictedFields = [];
        await animationFrame();
        expect(observed).toEqual([false, true, false]);
    } finally {
        stop();
    }
});

test("partial states stay isolated between otherwise identical records", () => {
    const readable = permissionRecord();
    const blocked = permissionRecord();
    expect(isPartialRecord(readable)).toBe(false);
    blocked.qingRestrictedFields = ["secret"];
    expect(isPartialRecord(blocked)).toBe(true);
    expect(isPartialRecord(readable)).toBe(false);
});

class RestrictedRecord extends models.Model {
    _name = "qing.restricted.record";
    name = fields.Char();
    count = fields.Integer();
    flag = fields.Boolean();
    amount = fields.Monetary({ currency_field: "currency_id" });
    currency_id = fields.Many2one({ relation: "res.currency" });
    secret_html = fields.Html();
    partner_id = fields.Many2one({ relation: "qing.restricted.partner" });
    secret_ids = fields.Many2many({ relation: "qing.restricted.partner" });
    line_ids = fields.One2many({ relation: "qing.restricted.line", relation_field: "parent_id" });
    _records = [
        { id: 1, name: "Readable", count: 12, flag: true, amount: 25, currency_id: 1, partner_id: 1, secret_ids: [1], line_ids: [1] },
        { id: 2, name: "Other", count: 7, flag: false, amount: 4, currency_id: 1 },
    ];
}

class RestrictedPartner extends models.Model {
    _name = "qing.restricted.partner";
    name = fields.Char();
    _records = [{ id: 1, name: "Partner" }];
}

class Currency extends models.Model {
    _name = "res.currency";
    name = fields.Char();
    symbol = fields.Char();
    position = fields.Selection({ selection: [["before", "Before"], ["after", "After"]] });
    _records = [{ id: 1, name: "USD", symbol: "$", position: "before" }];
}

class RestrictedLine extends models.Model {
    _name = "qing.restricted.line";
    name = fields.Char();
    count = fields.Integer();
    parent_id = fields.Many2one({ relation: RES_MODEL });
    _records = [{ id: 1, name: "Line", count: 8, parent_id: 1 }];
}

defineMailModels();
defineModels([RestrictedRecord, RestrictedPartner, RestrictedLine, Currency]);

function restrictReads(names, transform = (value) => value) {
    const mask = (value) => {
        value[RESTRICTED_FIELDS_KEY] = value.id === 1 ? [...names] : [];
        if (value.id === 1) {
            for (const name of names) {
                delete value[name];
            }
            transform(value);
        }
        return value;
    };
    onRpc("web_read", ({ model, parent }) => {
        if (model === RES_MODEL) {
            return parent().map(mask);
        }
    });
    onRpc("web_search_read", ({ model, parent }) => {
        if (model === RES_MODEL) {
            const result = parent();
            result.records = result.records.map(mask);
            return result;
        }
    });
}

async function form(arch) {
    const view = await mountView({ resModel: RES_MODEL, resId: 1, type: "form", arch });
    return findComponent(view, (component) => component instanceof FormController).model.root;
}

function expectRestrictedWidgetUnmounted(view, names) {
    for (const name of names) {
        const field = findComponent(view, (component) => component instanceof Field && component.props.name === name);
        expect(findComponent(field, (component) => component instanceof field.field.component)).toBe(null);
        expect(`[name='${name}'] > :not(.o_qing_permission_restricted)`).toHaveCount(0);
    }
}

test("omitted restricted values are absent from data and both eval contexts", async () => {
    restrictReads(["count", "flag", "secret_html", "secret_ids"]);
    const view = await mountView({ resModel: RES_MODEL, resId: 1, type: "form",
        arch: `<form><field name="name"/><field name="count"/><field name="flag"/><field name="secret_html"/><field name="secret_ids"/></form>` });
    const record = findComponent(view, (component) => component instanceof FormController).model.root;
    expect(record.qingRestrictedFields).toEqual(["count", "flag", "secret_html", "secret_ids"]);
    for (const name of record.qingRestrictedFields) {
        expect(Object.hasOwn(record.data, name)).toBe(false);
        expect(Object.hasOwn(record._values, name)).toBe(false);
        expect(Object.hasOwn(record.evalContext, name)).toBe(false);
        expect(Object.hasOwn(record.evalContextWithVirtualIds, name)).toBe(false);
        expect(`[name='${name}']`).toHaveText("Permission restricted");
    }
    expect(Object.hasOwn(record.data, RESTRICTED_FIELDS_KEY)).toBe(false);
    expect(".o_field_widget input").toHaveCount(0);
    expectRestrictedWidgetUnmounted(view, record.qingRestrictedFields);
});

test("deleting the only restricted line restores parent editing", async () => {
    onRpc("web_read", ({ model, parent }) => {
        if (model === RES_MODEL) {
            return parent().map((value) => {
                for (const line of value.line_ids || []) {
                    line[RESTRICTED_FIELDS_KEY] = ["count"];
                    delete line.count;
                }
                return value;
            });
        }
    });
    const record = await form(`<form><field name="name"/><field name="line_ids"><list editable="bottom"><field name="name"/><field name="count"/></list></field></form>`);
    expect(isPartialRecord(record)).toBe(true);
    expect("[name='name'] input").toHaveCount(0);
    const list = record.data.line_ids;
    await list.delete(list.records[0]);
    await animationFrame();
    expect(isPartialRecord(record)).toBe(false);
    expect("[name='name'] input").toHaveCount(1);
});

test("new line onchange restriction makes the parent readonly immediately", async () => {
    const record = await form(`<form><field name="name"/><field name="line_ids"><list editable="bottom"><field name="name"/><field name="count"/></list></field></form>`);
    expect(isPartialRecord(record)).toBe(false);
    onRpc("onchange", ({ model, parent }) => {
        if (model === "qing.restricted.line") {
            const result = parent();
            result.value[RESTRICTED_FIELDS_KEY] = ["count"];
            delete result.value.count;
            return result;
        }
    });
    await record.data.line_ids.addNewRecord({ position: "bottom" });
    await animationFrame();
    expect(isPartialRecord(record)).toBe(true);
    expect(record._isReadonly("name")).toBe(true);
    expect("[name='name'] input").toHaveCount(0);
});

test("force_save and withReadonly never serialize a blocked field", async () => {
    restrictReads(["count"]);
    const record = await form(`<form><field name="name"/><field name="count" force_save="1"/></form>`);
    const changes = record._getChanges({ name: "Allowed value", count: 0 }, { withReadonly: true });
    expect(changes).toEqual({ name: "Allowed value" });
    expect(record._getChanges({ count: false })).toEqual({});
});

test("partial records reject updates and clean saves never issue a write", async () => {
    restrictReads(["count", "secret_ids"]);
    let writes = 0;
    onRpc("web_save", () => writes++);
    const record = await form(`<form><field name="name"/><field name="count"/><field name="secret_ids"/></form>`);
    expect(await record.update({ name: "Must not change" })).toBe(false);
    expect(record.data.name).toBe("Readable");
    expect(await record.save()).toBe(true);
    expect(writes).toBe(0);
});

test("pending allowed changes are not reported as successfully saved", async () => {
    restrictReads(["count"]);
    let writes = 0;
    onRpc("web_save", () => writes++);
    const record = await form(`<form><field name="name"/><field name="count"/></form>`);
    record._changes.name = "Pending";
    record.dirty = true;
    expect(await record.save()).toBe(false);
    expect(record._changes.name).toBe("Pending");
    expect(writes).toBe(0);
});

test("clean partial saves with nextId load the next record like native saves", async () => {
    restrictReads(["count"]);
    let writes = 0;
    onRpc("web_save", () => writes++);
    const record = await form(`<form><field name="name"/><field name="count"/></form>`);
    await record.save({ nextId: 2 });
    expect(record.model.root.resId).toBe(2);
    expect(record.model.root.data.count).toBe(7);
    expect(record.model.root.qingRestrictedFields).toEqual([]);
    expect(writes).toBe(0);
});

test("an empty metadata snapshot clears the mask and restores editing on reload", async () => {
    const names = ["count"];
    restrictReads(names);
    const record = await form(`<form><field name="name"/><field name="count"/></form>`);
    expect("[name='count']").toHaveText("Permission restricted");
    names.length = 0;
    await record.load();
    await animationFrame();
    expect(record.qingRestrictedFields).toEqual([]);
    expect(record.data.count).toBe(12);
    expect("[name='count'] input").toHaveCount(1);
    expect("[name='name'] input").toHaveCount(1);
});

test("becoming restricted removes old data and stale eval-context keys", async () => {
    const names = [];
    restrictReads(names);
    const record = await form(`<form><field name="count"/></form>`);
    expect(record.evalContext.count).toBe(12);
    names.push("count");
    await record.load();
    await animationFrame();
    expect(Object.hasOwn(record.data, "count")).toBe(false);
    expect(Object.hasOwn(record.evalContext, "count")).toBe(false);
    expect("[name='count']").toHaveText("Permission restricted");
});

test("currency restriction masks monetary widgets even without backend closure", async () => {
    restrictReads(["currency_id"]);
    const view = await mountView({ resModel: RES_MODEL, resId: 1, type: "form",
        arch: `<form><field name="amount"/><field name="count" widget="monetary" options="{'currency_field': 'currency_id'}"/><field name="currency_id" invisible="1"/></form>` });
    const record = findComponent(view, (component) => component instanceof FormController).model.root;
    expect(isRestrictedField(record, "amount")).toBe(true);
    expect("[name='amount']").toHaveText("Permission restricted");
    expect("[name='count']").toHaveText("Permission restricted");
    expect(".o_field_widget input").toHaveCount(0);
    expectRestrictedWidgetUnmounted(view, ["amount", "count"]);
});

test("nested many2one metadata makes the parent field unavailable", async () => {
    restrictReads([], (value) => {
        value.partner_id = { id: 1, [RESTRICTED_FIELDS_KEY]: ["display_name"] };
    });
    const record = await form(`<form><field name="partner_id"/></form>`);
    expect(record.qingRestrictedFields).toEqual(["partner_id"]);
    expect(Object.hasOwn(record.data, "partner_id")).toBe(false);
    expect("[name='partner_id']").toHaveText("Permission restricted");
    expect("[name='partner_id'] a").toHaveCount(0);
});

test("critical unknown form modifiers hide actions without evaluating missing values", async () => {
    restrictReads(["count"]);
    const record = await form(`<form><header><button name="mutate" type="object" string="Mutate"/><button name="conditional" type="object" string="Conditional" invisible="count == 0"/></header><field name="count"/><field name="name" readonly="count &gt; 0" required="count == 0"/><group invisible="count &gt; 0"><label for="name" string="Hidden label"/></group></form>`);
    expect("button[name='conditional']").toHaveCount(0);
    expect("button[name='mutate']").toHaveAttribute("disabled");
    expect("[name='name'] input").toHaveCount(0);
    expect(hasRestrictedExpression("count == 0", record.evalContext)).toBe(true);
    expect(hasRestrictedExpression("name == 'count'", record.evalContext)).toBe(false);
    expect(hasRestrictedExpression("parent.count == 0", { parent: record.evalContext })).toBe(true);
});

test("ordinary current and parent contexts skip parsing entirely", async () => {
    restrictReads([]);
    const record = await form(`<form><field name="count"/></form>`);
    expect(hasRestrictedExpression("invalid syntax ?!", record.evalContext)).toBe(false);
    expect(hasRestrictedExpression("invalid syntax ?!", { parent: record.evalContext })).toBe(false);
});

test("unknown x2many field contexts do not evaluate omitted parent values", async () => {
    restrictReads(["count"]);
    const record = await form(`<form><field name="count"/><field name="secret_ids" context="{'default_name': count}"/></form>`);
    expect(record.data.secret_ids.records).toHaveLength(1);
    expect(Object.hasOwn(record.data.secret_ids.context, "default_name")).toBe(false);
    expect("[name='count']").toHaveText("Permission restricted");
});

test("onchange rollback restores the mask and data together", async () => {
    restrictReads([]);
    const record = await form(`<form><field name="count"/></form>`);
    const undo = record._applyChanges({}, { [RESTRICTED_FIELDS_KEY]: ["count"] });
    expect(Object.hasOwn(record.data, "count")).toBe(false);
    undo();
    expect(record.qingRestrictedFields).toEqual([]);
    expect(record.data.count).toBe(12);
    expect(record._values.count).toBe(12);
    expect(record.evalContext.count).toBe(12);
});

test("discard restores restricted savepoint metadata without recreating omitted values", async () => {
    restrictReads(["count"]);
    const record = await form(`<form><field name="count"/></form>`);
    record._addSavePoint();
    record._applyValues({ count: 9, [RESTRICTED_FIELDS_KEY]: [] });
    expect(record.data.count).toBe(9);
    record._discard();
    expect(record.qingRestrictedFields).toEqual(["count"]);
    expect(Object.hasOwn(record.data, "count")).toBe(false);
});

test("disabled ViewButton anchors and direct handler calls do not execute", async () => {
    restrictReads(["count"]);
    const view = await mountView({ resModel: RES_MODEL, resId: 1, type: "form",
        arch: `<form><a name="mutate" type="object" string="Mutate"/><field name="count"/></form>` });
    const button = findComponent(view, (component) => component instanceof ViewButton);
    let calls = 0;
    onRpc("mutate", () => calls++);
    button.onClick({ preventDefault() {} });
    expect(calls).toBe(0);
    expect("a[name='mutate']").toHaveAttribute("aria-disabled", "true");
});

test("direct list cells and aggregates never display zero or a readable-only sum", async () => {
    restrictReads(["count"]);
    const view = await mountView({ resModel: RES_MODEL, type: "list",
        arch: `<list editable="bottom"><field name="name"/><field name="count" sum="Total"/><button name="mutate" type="object" string="Mutate"/></list>` });
    const list = findComponent(view, (component) => component instanceof ListController).model.root;
    expect("tbody tr.o_data_row:eq(0) td[name='count']").toHaveText("Permission restricted");
    expect("tfoot .o_list_number").toHaveText("Permission restricted");
    expect("tbody tr.o_data_row:eq(0) button[name='mutate']").toHaveAttribute("disabled");
    expect(Object.hasOwn(list.records[0].data, "count")).toBe(false);
});

test("selection of only readable rows retains the native aggregate", async () => {
    restrictReads(["count"]);
    const view = await mountView({ resModel: RES_MODEL, type: "list",
        arch: `<list><field name="name"/><field name="count" sum="Total"/></list>` });
    if (isSmall()) {
        const list = findComponent(view, (component) => component instanceof ListController).model.root;
        await list.records[1].toggleSelection();
        await animationFrame();
    } else {
        await contains("tbody tr.o_data_row:eq(1) .o_list_record_selector input").click();
    }
    expect("tfoot .o_list_number").toHaveText("7");
});

test("currency-restricted list rows suppress monetary aggregates", async () => {
    restrictReads(["currency_id"]);
    await mountView({ resModel: RES_MODEL, type: "list",
        arch: `<list><field name="amount" sum="Total"/><field name="currency_id"/></list>` });
    expect("tbody tr.o_data_row:eq(0) td[name='amount']").toHaveText("Permission restricted");
    expect("tfoot .o_list_number").toHaveText("Permission restricted");
});

test("ordinary zero and False retain their native widgets and saving", async () => {
    restrictReads([]);
    let writes = 0;
    onRpc("web_save", () => { writes++; });
    const record = await form(`<form><field name="name"/><field name="count"/><field name="flag"/></form>`);
    await record.update({ count: 0, flag: false });
    expect(record.data.count).toBe(0);
    expect(record.data.flag).toBe(false);
    expect(await record.save()).toBe(true);
    expect(writes).toBe(1);
    expect(".o_qing_permission_restricted").toHaveCount(0);
});
