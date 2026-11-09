/** @odoo-module **/

import { AccountUploadListRenderer } from "@account/views/account_upload_list/account_upload_list_renderer";
import { patch } from "@web/core/utils/patch";

patch(AccountUploadListRenderer.prototype, {
    get qingShowRowNumber() {
        // Invoice and bill lists retain the standard table and record-row templates.
        return !this.props.list.context?.qing_hide_row_number;
    },
});
