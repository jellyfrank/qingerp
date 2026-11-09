/** @odoo-module **/
import { animationFrame, expect, test } from "@odoo/hoot";
import { queryOne } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, mountViewInDialog, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { statusBarField, StatusBarField } from "@web/views/fields/statusbar/statusbar_field";
import { qingHeaderActionIcon } from "@qing_web_ui/js/qing_header_action_icons";

class QingHeaderRecord extends models.Model {
    _name = "qing.header.record";
    name = fields.Char();
    state = fields.Selection({ selection: [["draft", "Draft"], ["waiting", "Waiting"], ["done", "Done"]] });
    request_count = fields.Integer();
    tax_invoice_count = fields.Integer();
    _records = [{ id: 1, name: "Delivery", state: "waiting" }];
}
defineModels([QingHeaderRecord]);
defineMailModels();

const arch = `<form><header>
    <button name="reserve" type="object" string="Reserve" class="oe_highlight"/>
    <button name="validate" type="object" string="Validate"/>
    <button name="action_cancel" type="object" string="Cancel" confirm="Cancel this record?"/>
    <field name="state" widget="statusbar" options="{'clickable': True}"/>
    </header><sheet><div name="button_box"><button name="moves" type="object" class="oe_stat_button" icon="fa-exchange" string="Moves"/></div><group><field name="name"/></group></sheet></form>`;

test.tags("desktop");
test("compact header mounts native workflow once and preserves editing and cancel confirmation", async () => {
    await mountView({ type: "form", resModel: "qing.header.record", resId: 1, arch });
    const left = queryOne(".o_control_panel_breadcrumbs");
    left.style.setProperty("width", "700px", "important");
    left.style.setProperty("max-width", "700px", "important");
    for (let i = 0; i < 5; i++) await animationFrame();
    expect(".o_qing_form_context .o_field_statusbar").toHaveCount(1);
    expect(".o_form_statusbar .o_field_statusbar").toHaveCount(0);
    expect(".o_field_statusbar").toHaveCount(1);
    expect(".o_qing_form_actions button[name=reserve]").toHaveCount(1);
    expect(".o_qing_form_actions button[name=reserve]").toHaveText("Reserve");
    expect(".o_qing_form_actions button[name=reserve].o_qing_icon_action").toHaveCount(0);
    expect(".o_qing_form_actions button[name=validate]").toHaveText("Validate");
    expect(".o_qing_form_actions button[name=action_cancel][aria-label='Cancel'] .oi[data-icon='close']").toHaveCount(1);
    expect(".o_qing_form_actions button[name=action_cancel]").toHaveCount(1);
    expect(".o_control_panel_actions button[name=moves]").toHaveCount(1);
    await contains(".o_qing_form_context button[data-value=done]").click();
    expect(".o_qing_form_context button[data-value=done]").toHaveAttribute("aria-checked", "true");
    expect(".o_form_button_save").toBeVisible();
    expect(".o_qing_form_header .o_form_button_save[aria-label='Save manually'] .oi[data-icon='save']").toHaveCount(1);
    expect(".o_qing_form_header .o_form_button_cancel .oi[data-icon='undo']").toHaveCount(1);
    await contains(".o_qing_form_actions button[name=action_cancel]").click();
    expect(".modal").toHaveText(/Cancel this record\?/);
});

test("generic action labels have distinct icons while business actions retain text", () => {
    expect(qingHeaderActionIcon(" New ")).toBe("add");
    expect(qingHeaderActionIcon("取消")).toBe("close");
    expect(qingHeaderActionIcon("Confirm")).toBe("check");
    expect(qingHeaderActionIcon("Print")).toBe("print");
    expect(qingHeaderActionIcon("预览")).toBe("visibility");
    expect(qingHeaderActionIcon("Create Invoice")).toBe(null);
    expect(qingHeaderActionIcon("Confirm Order")).toBe(null);
    expect(qingHeaderActionIcon("Send by Email")).toBe(null);
});

test.tags("desktop");
test("smart buttons keep semantic icons and stack stat counts above their labels", async () => {
    const statArch = `<form><sheet><div name="button_box">
        <button name="action_open_fapiao_requests" type="object" class="oe_stat_button" icon="request_quote">
            <field name="request_count" widget="statinfo" string="销售订单"/>
        </button>
        <button name="action_open_fapiao" type="object" class="oe_stat_button" icon="receipt_long">
            <div class="o_field_widget o_stat_info">
                <span class="o_stat_value">12</span><span class="o_stat_text">税票登记</span>
            </div>
        </button>
    </div><group><field name="name"/></group></sheet></form>`;
    await mountView({ type: "form", resModel: "qing.header.record", resId: 1, arch: statArch });
    // Scope the mounted view itself rather than the test runner's document body.
    const view = queryOne(".o_form_view");
    view.classList.add("o_qing_web_ui");
    // Wait for the native button-box portal to reach the control panel before checking its layout.
    await animationFrame();
    const fieldButton = queryOne(".oe_stat_button[name=action_open_fapiao_requests]");
    expect(fieldButton.querySelector(".oi[data-icon]")).toHaveAttribute("data-icon", "request_quote");
    const fieldStatInfo = fieldButton.querySelector(".o_field_statinfo");
    expect(getComputedStyle(fieldStatInfo).flexDirection).toBe("column");
    expect(getComputedStyle(fieldStatInfo.querySelector(".o_stat_text")).order).toBe("1");
    expect(getComputedStyle(fieldStatInfo.querySelector(".o_stat_value")).order).toBe("0");
    expect(getComputedStyle(fieldStatInfo).whiteSpace).toBe("nowrap");
    const customStatInfo = queryOne(".oe_stat_button[name=action_open_fapiao] > .o_stat_info");
    expect(queryOne(".oe_stat_button[name=action_open_fapiao] .oi[data-icon]")).toHaveAttribute("data-icon", "receipt_long");
    expect(getComputedStyle(customStatInfo).display).toBe("flex");
    expect(getComputedStyle(customStatInfo).flexDirection).toBe("column");
    expect(getComputedStyle(customStatInfo.querySelector(".o_stat_text")).order).toBe("1");
    expect(getComputedStyle(customStatInfo.querySelector(".o_stat_value")).order).toBe("0");
});

test.tags("desktop");
test("dialog form keeps its buttons and status field inside the dialog", async () => {
    await mountViewInDialog({ type: "form", resModel: "qing.header.record", resId: 1, arch });
    expect(".modal .o_form_statusbar .o_field_statusbar").toHaveCount(1);
    expect(".modal .o_form_statusbar button[name=action_cancel]").toHaveCount(1);
    expect(".o_qing_form_context").toHaveCount(0);
});

test.tags("desktop");
test("header without workflow renders no artificial status row", async () => {
    await mountView({ type: "form", resModel: "qing.header.record", resId: 1,
        arch: `<form><sheet><field name="name"/></sheet></form>` });
    expect(".o_qing_form_header").toHaveCount(1);
    expect(".o_qing_form_workflow > *").toHaveCount(0);
    expect(".o_form_statusbar").toHaveCount(0);
});

test.tags("desktop");
test("native statusbar subclasses keep their own field while moving to the context row", async () => {
    class DerivedStatusBar extends StatusBarField {}
    registry.category("fields").add("qing_derived_statusbar", { ...statusBarField, component: DerivedStatusBar });
    await mountView({ type: "form", resModel: "qing.header.record", resId: 1,
        arch: arch.replace('widget="statusbar"', 'widget="qing_derived_statusbar"') });
    expect(".o_qing_form_context [name=state] .o_qing_status_timeline").toHaveCount(1);
    expect(".o_form_statusbar [name=state]").toHaveCount(0);
});

test.tags("desktop");
test("business actions fold only for lack of space and return when the toolbar grows", async () => {
    await mountView({ type: "form", resModel: "qing.header.record", resId: 1, arch });
    const settle = async (width) => {
        queryOne(".o_control_panel_main").style.setProperty("width", width === 1000 ? "1600px" : "900px", "important");
        const actions = queryOne(".o_control_panel_breadcrumbs");
        actions.style.setProperty("width", `${width}px`, "important");
        actions.style.setProperty("max-width", `${width}px`, "important");
        // Allow ResizeObserver and the measure/render frames to settle.
        for (let i = 0; i < 5; i++) await animationFrame();
    };
    await settle(1000);
    expect(".o_qing_action_slot").toHaveCount(3);
    expect(".o_qing_form_business_buttons > button").toHaveCount(0);
    await settle(130);
    expect(queryOne(".o_control_panel_breadcrumbs").clientWidth).toBeLessThan(150);
    expect(".o_qing_form_business_buttons > button").toHaveCount(1);
    expect(".o_qing_form_actions button[name=action_cancel]").toHaveCount(0);
    await contains(".o_qing_form_business_buttons > button").click();
    expect("button[name=action_cancel]").toHaveCount(1);
    await contains(".o-dropdown--menu button[name=action_cancel]").click();
    expect(".modal").toHaveText(/Cancel this record\?/);
    await contains(".modal .btn-secondary").click();
    await settle(1000);
    expect(".o_qing_action_slot").toHaveCount(3);
    expect(".o_qing_form_business_buttons > button").toHaveCount(0);
});
