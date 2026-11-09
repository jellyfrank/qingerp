import { expect, test } from "@odoo/hoot";
import { SaleTemplateDropdown } from "@sale_management/views/components/template_dropdown";
import "@qing_rental/views/rental_template_dropdown";

function makeDropdown({ context = {}, saved = true, form = true } = {}) {
    const loads = [];
    const actions = [];
    const record = {
        context,
        // A rental opened from Sales must still use Sales creation defaults.
        data: { is_qing_rental: true },
        save: async () => saved,
        model: {
            async load(params) {
                loads.push(params);
                record.context = params.context;
            },
        },
    };
    const dropdown = Object.create(SaleTemplateDropdown.prototype);
    Object.defineProperties(dropdown, {
        props: { value: { record } },
        isFormView: { value: form },
        action: { value: {
            currentAction: { id: 123 },
            async doAction(...args) { actions.push(args); },
        } },
    });
    return { dropdown, loads, actions };
}

test("rental form New retains rental defaults through consecutive creates", async () => {
    const { dropdown, loads } = makeDropdown({ context: { default_is_qing_rental: true } });
    await dropdown.createQuotation();
    await dropdown.createQuotation();
    expect(loads).toEqual(Array.from({ length: 2 }, () => ({
        resId: false, mode: "edit",
        context: { default_is_qing_rental: true, default_sale_order_template_id: false },
    })));
});

test("rental template selection is preserved and next plain New clears the template", async () => {
    const { dropdown, loads } = makeDropdown({ context: { default_is_qing_rental: true } });
    const additionalContext = { default_sale_order_template_id: 42 };
    await dropdown.createQuotation({ additionalContext });
    await dropdown.createQuotation();
    expect(loads.map((item) => item.context)).toEqual([
        { default_is_qing_rental: true, default_sale_order_template_id: 42 },
        { default_is_qing_rental: true, default_sale_order_template_id: false },
    ]);
    expect(additionalContext).toEqual({ default_sale_order_template_id: 42 });
});

test("Sales form New stays a sales quotation even when the opened record is a rental", async () => {
    const { dropdown, loads } = makeDropdown();
    await dropdown.createQuotation();
    expect(loads[0].context).toEqual({ default_sale_order_template_id: false });
});

test("failed save does not load a new rental", async () => {
    const { dropdown, loads } = makeDropdown({ context: { default_is_qing_rental: true }, saved: false });
    await dropdown.createQuotation();
    expect(loads).toEqual([]);
});

test("list New keeps native action navigation", async () => {
    const { dropdown, loads, actions } = makeDropdown({ form: false });
    await dropdown.createQuotation();
    expect(loads).toEqual([]);
    expect(actions).toEqual([[{ id: 123 }, {
        viewType: "form", additionalContext: { default_sale_order_template_id: false },
    }]]);
});
