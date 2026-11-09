/** @odoo-module **/

import { describe, expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, mountWithCleanup } from "@web/../tests/web_test_helpers";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

// Native many2one dropdown tests run on desktop; mobile selection is checked in the browser.
describe.current.tags("desktop");

class QingAutocompletePartner extends models.Model {
    _name = "qing.autocomplete.partner";
    name = fields.Char();
    _records = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, name: `Partner ${i + 1}` }));
    _views = { list: '<list><field name="name"/></list>', search: '<search><field name="name"/></search>' };
}
class QingAutocompleteRecord extends models.Model {
    _name = "qing.autocomplete.record";
    name = fields.Char();
    partner_id = fields.Many2one({ relation: "qing.autocomplete.partner" });
    _records = [{ id: 1, name: "Autocomplete" }];
}
defineModels([QingAutocompletePartner, QingAutocompleteRecord]);
defineMailModels();

test("themed search more retains native selection and domain", async () => {
    await mountView({
        type: "form", resModel: "qing.autocomplete.record", resId: 1,
        arch: `<form><sheet><field name="partner_id" domain="[('id', '&gt;=', 3)]" options="{'no_create': True}"/></sheet></form>`,
    });
    await contains('[name="partner_id"] input').click();
    expect(".o_m2o_dropdown_option_search_more").toHaveText("Search more...");
    await contains(".o_m2o_dropdown_option_search_more").click();
    expect(".modal .o_data_row").toHaveCount(8);
    await contains('.modal .o_data_cell:contains("Partner 3")').click();
    expect(".modal").toHaveCount(0);
    expect('[name="partner_id"] input').toHaveValue("Partner 3");
});

test("custom search more labels remain available", async () => {
    await mountWithCleanup(Many2XAutocomplete, { props: {
        resModel: "qing.autocomplete.partner", fieldString: "Partners", getDomain: () => [],
        update: () => {}, activeActions: {}, searchMoreLabel: "Browse locations",
    }});
    await contains(".o-autocomplete input").click();
    expect(".o_m2o_dropdown_option_search_more").toHaveText("Browse locations");
});
