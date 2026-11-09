/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

function previewUrl(action) {
    const context = action.context || {};
    const docids = context.active_ids?.length
        ? context.active_ids
        : context.active_id
          ? [context.active_id]
          : [];
    let path = `/report/pdf/${encodeURIComponent(action.report_name)}`;
    if (docids.length) {
        path += `/${docids.join(",")}`;
    }
    const params = new URLSearchParams({ context: JSON.stringify(context) });
    if (action.data) {
        params.set("options", JSON.stringify(action.data));
    }
    return `${path}?${params.toString()}`;
}

// The native /report/pdf route checks report and record access and returns the
// PDF inline, so the browser opens it in a new tab instead of downloading it.
registry.category("ir.actions.report handlers").add("qing_base_print_preview", async (action, _options, env) => {
    if (action.report_type !== "qweb-pdf" || !action.qing_print_preview_effective) {
        return false;
    }
    window.open(previewUrl(action), "_blank", "noopener");
    env.services.notification.add(_t("The PDF report opened in a new tab."), {
        title: action.name || _t("Report Preview"),
        type: "info",
    });
    return true;
});
