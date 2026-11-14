/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { WarningDialog } from "@web/core/errors/error_dialogs";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

export class QingAccessErrorDialog extends WarningDialog {
    static template = "qing_web_ui.AccessErrorDialog";

    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.title = _t("Permission required");
    }

    get canRequest() {
        return !!session.qing_access_requests;
    }

    requestAccess() {
        const info = this.props.data?.context?.qing_access || {};
        this.dialog.add(FormViewDialog, {
            resModel: "qing.access.request",
            title: _t("Request access"),
            canExpand: false,
            context: {
                default_model_name: info.model || "",
                default_operation: info.operation || "other",
                default_record_id: info.record_id || 0,
            },
        });
        this.props.close();
    }
}

registry.category("error_dialogs").add(
    "odoo.exceptions.AccessError", QingAccessErrorDialog, { force: true }
);
