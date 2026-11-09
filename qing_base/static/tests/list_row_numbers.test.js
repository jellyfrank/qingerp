/** @odoo-module **/

import { expect, queryAllTexts, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
    pagerNext,
} from "@web/../tests/web_test_helpers";

class Parent extends models.Model {
    _name = "qing.row.number.parent";

    category = fields.Char();
    name = fields.Char();
    line_ids = fields.One2many({
        relation: "qing.row.number.line",
        relation_field: "parent_id",
    });

    _records = [
        { id: 1, name: "First", category: "A", line_ids: [1, 2] },
        { id: 2, name: "Second", category: "A" },
        { id: 3, name: "Third", category: "B" },
    ];
}

class Line extends models.Model {
    _name = "qing.row.number.line";

    name = fields.Char();
    parent_id = fields.Many2one({ relation: "qing.row.number.parent" });

    _records = [
        { id: 1, name: "Line A", parent_id: 1 },
        { id: 2, name: "Line B", parent_id: 1 },
    ];
}

defineMailModels();
defineModels([Parent, Line]);

test("generic list row numbers follow pagination", async () => {
    await mountView({
        resModel: "qing.row.number.parent",
        type: "list",
        arch: `<list limit="2"><field name="name"/></list>`,
    });

    expect("thead th.o_qing_row_number").toHaveCount(1);
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["1", "2"]);

    await pagerNext();
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["3"]);
});

test("editable one2many row numbers include unsaved lines", async () => {
    await mountView({
        resModel: "qing.row.number.parent",
        resId: 1,
        type: "form",
        arch: `<form><field name="line_ids"><list editable="bottom"><field name="name"/></list></field></form>`,
    });

    expect(queryAllTexts(".o_field_x2many_list tr.o_data_row td.o_qing_row_number")).toEqual([
        "1",
        "2",
    ]);
    await contains(".o_field_x2many_list_row_add button").click();
    expect(queryAllTexts(".o_field_x2many_list tr.o_data_row td.o_qing_row_number")).toEqual([
        "1",
        "2",
        "3",
    ]);
});

test("grouped list numbers records within each group", async () => {
    await mountView({
        resModel: "qing.row.number.parent",
        type: "list",
        groupBy: ["category"],
        arch: `<list><field name="name"/><field name="category"/></list>`,
    });

    await contains(".o_group_header:eq(0)").click();
    await contains(".o_group_header:eq(1)").click();
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual([
        "1",
        "2",
        "1",
    ]);
});

test("row numbers can be disabled by action context", async () => {
    await mountView({
        resModel: "qing.row.number.parent",
        type: "list",
        context: { qing_hide_row_number: true },
        arch: `<list><field name="name"/></list>`,
    });

    expect(".o_qing_row_number").toHaveCount(0);
});
