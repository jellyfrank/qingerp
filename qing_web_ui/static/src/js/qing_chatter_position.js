/** @odoo-module **/
import "@mail/chatter/web/form_renderer";
import { onWillUnmount } from "@odoo/owl";
import { SIZES } from "@web/core/ui/ui_utils";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { FormRenderer } from "@web/views/form/form_renderer";
import { render } from "@web/owl2/utils";

function chatterPosition() {
    return window.qingWebUi?.getPreferences().chatterPosition || "auto";
}

// "right" widens the native side-chatter range (XXL only) down to XL.
export function qingForcesSideChatter(env, size) {
    return !env.inDialog && chatterPosition() === "right" && size === SIZES.XL;
}

function rerenderOnPreferenceChange(component) {
    const update = () => render(component);
    window.addEventListener("qing-web-ui:preferences-changed", update);
    onWillUnmount(() => window.removeEventListener("qing-web-ui:preferences-changed", update));
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        rerenderOnPreferenceChange(this);
    },
    get className() {
        const result = super.className;
        if (qingForcesSideChatter(this.env, this.ui.size)) {
            // Native row layout that hosts an aside chatter.
            result["o_xxl_form_view h-100"] = true;
        }
        return result;
    },
});

patch(FormRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        rerenderOnPreferenceChange(this);
    },
    mailLayout(hasAttachmentContainer) {
        const layout = super.mailLayout(hasAttachmentContainer);
        if (this.env.inDialog) return layout;
        if (layout === "SIDE_CHATTER" && chatterPosition() === "bottom") {
            return "BOTTOM_CHATTER";
        }
        if (qingForcesSideChatter(this.env, this.uiService.size)) {
            if (layout === "EXTERNAL_COMBO") return "EXTERNAL_COMBO_XXL";
            // An attachment preview needs the native XXL width; keep it below.
            if (layout === "BOTTOM_CHATTER" && !(hasAttachmentContainer && this.hasFile())) {
                return "SIDE_CHATTER";
            }
        }
        return layout;
    },
});
