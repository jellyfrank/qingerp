/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { defineComboModels } from "@product/../tests/product_combo_test_helpers";
import { saleManagementModels as saleModels } from "@sale_management/../tests/sale_management_test_helpers";
import { contains, defineModels, mountView, onRpc, pagerNext } from "@web/../tests/web_test_helpers";

class SaleOrderLine extends saleModels.SaleOrderLine {
    _records = [
        { id: 1, name: "Section", display_type: "line_section", sequence: 1 },
        { id: 2, name: "Product A", product_id: 1, sequence: 2 },
        { id: 3, name: "Note", display_type: "line_note", sequence: 3 },
        { id: 4, name: "Product B", product_id: 2, sequence: 4 },
    ];
}
class SaleOrder extends saleModels.SaleOrder {
    _records = [
        { id: 1, name: "S001", order_line: [1, 2, 3, 4] },
        { id: 2, name: "S002" },
        { id: 3, name: "S003" },
    ];
}
defineComboModels();
defineMailModels();
defineModels({ SaleOrder, SaleOrderLine,
    SaleOrderTemplate: saleModels.SaleOrderTemplate,
    SaleOrderTemplateLine: saleModels.SaleOrderTemplateLine,
});

const rowNumbers = () => queryAllTexts("tr.o_data_row td.o_qing_row_number");
const formArch = `<form><field name="company_id" invisible="1"/><field name="order_line" widget="sol_o2m"><list editable="bottom" limit="200">
    <field name="sequence" widget="handle"/>
    <widget name="line_number"/>
    <column name="product_and_description"><field name="name"/><field name="label"/></column>
    <field name="display_type" column_invisible="1"/>
    <field name="linked_line_id" column_invisible="1"/>
    <field name="product_type" column_invisible="1"/>
    <field name="combo_item_id" column_invisible="1"/>
</list></field></form>`;

for (const jsClass of ["sale_file_upload_list", "sale_onboarding_list", "sale_dashboard_list"]) {
    test(`${jsClass} preserves pagination and sorting`, async () => {
        onRpc("sale.order", "retrieve_sale_dashboard", () => ({
            orders: { total: 3, gain: null }, sales_amount: { total: 0, gain: null }, currency_id: 1,
        }));
        await mountView({ resModel: "sale.order", type: "list",
            arch: `<list js_class="${jsClass}" limit="2"><field name="name"/></list>` });
        expect(rowNumbers()).toEqual(["1", "2"]);
        await contains("th[data-name='name']").click();
        expect(rowNumbers()).toEqual(["1", "2"]);
        await pagerNext();
        expect(rowNumbers()).toEqual(["3"]);
    });
}

test("sales x2many numbers sections, notes and new rows without duplicate native index", async () => {
    await mountView({ resModel: "sale.order", resId: 1, type: "form", arch: formArch });
    expect(rowNumbers()).toEqual(["1", "2", "3", "4"]);
    expect(".o_line_number").toHaveCount(0);
    expect("thead .o_qing_row_number").toHaveCount(1);
    await contains(".o_field_x2many_list_row_add button").click();
    expect(rowNumbers()).toEqual(["1", "2", "3", "4", "5"]);
});

test("sales x2many respects opt out and keeps native index", async () => {
    await mountView({ resModel: "sale.order", resId: 1, type: "form", arch: formArch,
        context: { qing_hide_row_number: true } });
    expect(".o_qing_row_number").toHaveCount(0);
    expect(".o_line_number").toHaveCount(3);
});

for (const size of [50, 150]) {
    test(`sales x2many renders ${size} lines without stored numbering`, async () => {
        SaleOrderLine._records = Array.from({ length: size }, (_, index) => ({
            id: index + 1, name: `Line ${index + 1}`, product_id: 1, sequence: index + 1,
        }));
        SaleOrder._records[0].order_line = SaleOrderLine._records.map((record) => record.id);
        await mountView({ resModel: "sale.order", resId: 1, type: "form", arch: formArch });
        expect(rowNumbers()).toEqual(Array.from({ length: size }, (_, index) => String(index + 1)));
    });
}


test("quotation template x2many keeps section and note indexes", async () => {
    saleModels.SaleOrderTemplateLine._records = [
        { id: 1, name: "Section", display_type: "line_section", sequence: 1 },
        { id: 2, name: "Product", product_id: 1, sequence: 2 },
        { id: 3, name: "Note", display_type: "line_note", sequence: 3 },
    ];
    saleModels.SaleOrderTemplate._records = [{
        id: 1, name: "Template", sale_order_template_line_ids: [1, 2, 3],
    }];
    await mountView({ resModel: "sale.order.template", resId: 1, type: "form",
        arch: `<form><field name="sale_order_template_line_ids" widget="so_template_line_o2m">
            <list editable="bottom">
                <field name="sequence" widget="handle"/>
                <column name="product_and_description"><field name="name"/><field name="label"/></column>
                <field name="display_type" column_invisible="1"/>
            </list>
        </field></form>` });
    expect(rowNumbers()).toEqual(["1", "2", "3"]);
    expect("thead .o_qing_row_number").toHaveCount(1);
});
