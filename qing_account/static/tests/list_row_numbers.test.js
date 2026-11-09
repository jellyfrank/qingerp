/** @odoo-module **/

import { expect, queryAllTexts, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    defineModels,
    fields,
    isSmall,
    models,
    mountView,
    pagerNext,
} from "@web/../tests/web_test_helpers";

class AccountMove extends models.Model {
    _name = "account.move";

    amount_total = fields.Float();
    name = fields.Char();
    state = fields.Selection({ selection: [["draft", "Draft"], ["posted", "Posted"]] });

    _records = [
        { id: 1, name: "INV/001", state: "draft", amount_total: 10 },
        { id: 2, name: "INV/002", state: "posted", amount_total: 20 },
        { id: 3, name: "INV/003", state: "posted", amount_total: 30 },
    ];
}

defineMailModels();
defineModels([AccountMove]);

const invoiceArch = `<list js_class="account_tree" limit="2"><field name="name"/><field name="amount_total" sum="Total"/></list>`;

test("invoice row numbers follow pagination and keep totals aligned", async () => {
    await mountView({
        resModel: "account.move",
        type: "list",
        context: { default_move_type: "out_invoice" },
        arch: invoiceArch,
    });

    expect("thead th.o_qing_row_number").toHaveCount(1);
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["1", "2"]);
    expect("tfoot tr td:first-child").toHaveAttribute("colspan", isSmall() ? "2" : "3");
    expect("tfoot tr td:last-child").toHaveText("30.00");

    await pagerNext();
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["3"]);
    expect("tfoot tr td:last-child").toHaveText("30.00");
});

test("vendor bill lists reuse invoice row numbering and selection", async () => {
    await mountView({
        resModel: "account.move",
        type: "list",
        context: { default_move_type: "in_invoice" },
        arch: invoiceArch,
    });

    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["1", "2"]);
    if (isSmall()) {
        const { drop } = await contains("tbody tr.o_data_row:first-child").drag();
        await drop();
    } else {
        await contains("tbody tr.o_data_row:first-child .o_list_record_selector input").click();
    }
    expect("tbody tr.o_data_row.o_data_row_selected td.o_qing_row_number").toHaveText("1");
    expect("tfoot tr td:last-child").toHaveText("10.00");
});

test("grouped invoice lists number records within each group", async () => {
    await mountView({
        resModel: "account.move",
        type: "list",
        context: { default_move_type: "out_invoice" },
        groupBy: ["state"],
        arch: `<list js_class="account_tree"><field name="name"/><field name="state"/></list>`,
    });

    await contains(".o_group_header:eq(0)").click();
    await contains(".o_group_header:eq(1)").click();
    expect(queryAllTexts("tbody tr.o_data_row td.o_qing_row_number")).toEqual(["1", "1", "2"]);
});

test("invoice row numbers respect the action opt-out", async () => {
    await mountView({
        resModel: "account.move",
        type: "list",
        context: { default_move_type: "out_invoice", qing_hide_row_number: true },
        arch: invoiceArch,
    });

    expect(".o_qing_row_number").toHaveCount(0);
});
