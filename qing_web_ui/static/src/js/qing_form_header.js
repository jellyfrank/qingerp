/** @odoo-module **/
import { useSubEnv } from "@web/owl2/utils";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { FormCompiler } from "@web/views/form/form_compiler";
import { createElement } from "@web/core/utils/xml";
import { registry } from "@web/core/registry";
import { StatusBarButtons } from "@web/views/form/status_bar_buttons/status_bar_buttons";
import { StatusBarField } from "@web/views/fields/statusbar/statusbar_field";
import { ButtonBox } from "@web/views/form/button_box/button_box";
import { onMounted, onPatched, onWillUnmount, onWillUpdateProps, proxy, signal } from "@odoo/owl";
import { qingHeaderActionIcon } from "./qing_header_action_icons";

let nextHeaderId = 0;

patch(FormCompiler.prototype, {
    compileHeader(el, params) {
        if (!params.isSubView) {
            for (const button of el.querySelectorAll(":scope > button[string]")) {
                const label = button.getAttribute("string");
                const icon = qingHeaderActionIcon(label);
                if (!icon) continue;
                button.classList.add("o_qing_icon_action");
                button.setAttribute("icon", icon);
                if (!button.hasAttribute("title")) {
                    button.setAttribute("title", label);
                }
                button.setAttribute("aria-label", label);
            }
        }
        const header = super.compileHeader(...arguments);
        if (params.isSubView) return header;
        const desktop = "__comp__.env.qingFormHeader && !__comp__.uiService.isSmall";
        // Move only native workflow fields. Other header fields keep their own
        // layout, visibility modifiers, record context and validation behavior.
        const fieldIds = new Set([...el.querySelectorAll(":scope > field[widget]")]
            .filter(field => {
                const component = registry.category("fields").get(field.getAttribute("widget"), null)?.component;
                return component === StatusBarField || component?.prototype instanceof StatusBarField;
            }).map(field => `'${field.getAttribute("field_id")}'`));
        for (const field of [...header.children]) {
            if (field.tagName !== "Field" || !fieldIds.has(field.getAttribute("id"))) continue;
            const branch = createElement("t", { "t-if": desktop });
            const portal = createElement("t", {
                "t-custom-portal": "'#' + __comp__.env.qingFormHeader.workflowId",
            });
            portal.append(field.cloneNode(true));
            branch.append(portal);
            const fallback = createElement("t", { "t-else": "" });
            field.replaceWith(branch, fallback);
            fallback.append(field);
        }
        return header;
    },
});

patch(ButtonBox.prototype, {
    setup() {
        super.setup(...arguments);
        this.qingButtonBoxRef = signal.ref();
        this.qingCapacity = proxy({ count: 8 });
        let frame;
        const schedule = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                const main = this.qingButtonBoxRef()?.closest('.o_control_panel_main');
                if (!main || this.ui.isSmall) return;
                const left = main.querySelector('.o_control_panel_breadcrumbs');
                const navigation = main.querySelector('.o_control_panel_navigation');
                const business = main.querySelector('.o_qing_form_business_buttons');
                // Narrow toolbars give related records their own centered row.
                // On a shared row, reserve symmetrical space on both sides.
                const leftItems = [...(left?.children || [])].filter(el => el.getBoundingClientRect().width);
                const leftWidth = leftItems.reduce((sum, el) => sum + el.getBoundingClientRect().width, 0)
                    + Math.max(0, leftItems.length - 1) * (parseFloat(getComputedStyle(left).columnGap) || 0);
                const side = Math.max(leftWidth, navigation?.scrollWidth || 0);
                const available = main.clientWidth <= 900 ? main.clientWidth
                    : business ? main.clientWidth * 0.4
                    : main.clientWidth - 2 * (side + 12);
                this.qingCapacity.count = Math.max(1, Math.floor(available / 144));
            });
        };
        const observer = new ResizeObserver(schedule);
        onMounted(() => {
            const main = this.qingButtonBoxRef()?.closest('.o_control_panel_main');
            if (main) observer.observe(main);
            schedule();
        });
        onPatched(schedule);
        onWillUnmount(() => { observer.disconnect(); cancelAnimationFrame(frame); });
    },

    get visibleButtons() {
        if (!this.env.qingFormHeader || this.ui.isSmall) {
            return super.visibleButtons;
        }
        return this.qingButtonLayout.visibleButtons;
    },

    get additionalButtons() {
        if (!this.env.qingFormHeader || this.ui.isSmall) {
            return super.additionalButtons;
        }
        return this.qingButtonLayout.additionalButtons;
    },

    get qingButtonLayout() {
        const all = Object.entries(this.props.slots)
            .filter(([, slot]) => this.isSlotVisible(slot))
            .map(([name]) => name);
        const limit = Math.min(this.qingCapacity.count, all.length);
        if (all.length <= limit) {
            return { visibleButtons: all, additionalButtons: [] };
        }
        const splitIndex = Math.max(0, limit - 1);
        return {
            visibleButtons: all.slice(0, splitIndex),
            additionalButtons: all.slice(splitIndex),
        };
    },
});
patch(StatusBarField.prototype, {
    adjustVisibleItems() {
        if (!this.env.qingFormHeader) return super.adjustVisibleItems(...arguments);
        // Retain native selection, visibility and folded-stage semantics; only
        // replace width-based folding with a scrollable process line.
        const root = this.rootRef();
        // Primary-inherited templates (e.g. stock's locked statusbar) can be
        // compiled before our XML extension. Cover every native subclass here.
        root.classList.add('o_qing_status_timeline');
        const wrappers = root.querySelectorAll('.o_arrow_button_wrap');
        wrappers.forEach(el => el.classList.remove('d-none'));
        const buttons = root.querySelectorAll('.o_arrow_button_wrap .o_arrow_button');
        this.dropdownRef().classList.add('d-none');
        this.beforeRef().classList.add('d-none');
        this.afterRef().classList.toggle('d-none', !this.items.folded.length);
        this.items.before = [];
        this.items.after = [...this.items.folded];
        buttons.forEach((button, index) => button.classList.toggle('o_first', index === 0 && !this.items.folded.length));
        const current = root.querySelector('.o_arrow_button_current');
        if (current) {
            const bounds = root.getBoundingClientRect();
            const item = current.getBoundingClientRect();
            if (item.left < bounds.left) root.scrollLeft += item.left - bounds.left;
            else if (item.right > bounds.right) root.scrollLeft += item.right - bounds.right;
        }
    },
});
patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        // A per-controller portal prevents actions from attaching to another form.
        // Nested dialogs and forms without a control panel retain native rendering.
        const id = ++nextHeaderId;
        useSubEnv({
            qingFormHeader: this.display.controlPanel && !this.env.inDialog
                ? { portalId: `o_qing_form_actions_${id}`, workflowId: `o_qing_form_workflow_${id}` }
                : false,
        });
    },
});

patch(StatusBarButtons.prototype, {
    get qingMainSlotNames() {
        return this.visibleSlotNames;
    },

    get qingAdditionalSlotNames() {
        const inline = new Set(this.qingMainSlotNames.slice(0, this.qingOverflow.count));
        return this.visibleSlotNames.filter(name => !inline.has(name));
    },

    setup() {
        super.setup(...arguments);
        this.qingOverflow = proxy({ count: Number.MAX_SAFE_INTEGER });
        if (!this.env.qingFormHeader) return;
        let frame;
        let fullWidths = [];
        const observer = new ResizeObserver(() => schedule());
        let observed = [];
        const schedule = (remeasure = false) => {
            if (remeasure) fullWidths = [];
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                const host = document.getElementById(this.env.qingFormHeader.portalId);
                const actions = host?.parentElement;
                if (!host || !actions || this.uiService.isSmall) return;
                const siblings = [...actions.children].filter(el => el !== host && el.getBoundingClientRect().width);
                const targets = [actions, ...siblings];
                if (targets.length !== observed.length || targets.some((el, i) => el !== observed[i])) {
                    observer.disconnect();
                    targets.forEach(el => observer.observe(el));
                    observed = targets;
                }
                // Cache full-size slots so resizing a folded toolbar does not
                // repeatedly expand and collapse it through ResizeObserver.
                const slots = [...host.querySelectorAll('.o_qing_action_slot')];
                if (slots.length === this.qingMainSlotNames.length) {
                    fullWidths = slots.map(el => el.getBoundingClientRect().width);
                }
                if (fullWidths.length !== this.qingMainSlotNames.length) {
                    if (this.qingOverflow.count !== Number.MAX_SAFE_INTEGER) {
                        this.qingOverflow.count = Number.MAX_SAFE_INTEGER;
                    }
                    return;
                }
                if (!fullWidths.length) return;
                const style = getComputedStyle(actions);
                const hostStyle = getComputedStyle(host);
                const gap = parseFloat(getComputedStyle(host.firstElementChild).columnGap) || 0;
                const available = actions.clientWidth - (parseFloat(style.paddingLeft) || 0)
                    - (parseFloat(style.paddingRight) || 0)
                    - siblings.reduce((sum, el) => sum + el.getBoundingClientRect().width, 0)
                    - siblings.length * (parseFloat(style.columnGap) || 0)
                    - (parseFloat(hostStyle.paddingLeft) || 0) - (parseFloat(hostStyle.borderLeftWidth) || 0);
                const total = fullWidths.reduce((a, b) => a + b, 0) + Math.max(0, fullWidths.length - 1) * gap;
                // Reserve the same square control size used by the overflow trigger.
                const more = host.firstElementChild.getBoundingClientRect().height || 36;
                if (total <= available) {
                    if (this.qingOverflow.count !== Number.MAX_SAFE_INTEGER) {
                        this.qingOverflow.count = Number.MAX_SAFE_INTEGER;
                    }
                    return;
                }
                let used = more;
                let count = 0;
                while (count < fullWidths.length && used + gap + fullWidths[count] <= available) {
                    used += gap + fullWidths[count++];
                }
                if (this.qingOverflow.count !== count) this.qingOverflow.count = count;
            });
        };
        onMounted(() => schedule(true));
        onPatched(() => schedule());
        onWillUpdateProps(() => schedule(true));
        const preferencesChanged = () => schedule(true);
        window.addEventListener('qing-web-ui:preferences-changed', preferencesChanged);
        onWillUnmount(() => {
            cancelAnimationFrame(frame);
            observer.disconnect();
            window.removeEventListener('qing-web-ui:preferences-changed', preferencesChanged);
        });
    },
});
