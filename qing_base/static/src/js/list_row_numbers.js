/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ListRenderer } from "@web/views/list/list_renderer";

const ROW_NUMBER_COLUMN = {
    id: "qing_row_number",
    type: "qing_row_number",
    attrs: { width: "56px" },
};

patch(ListRenderer.prototype, {
    get qingShowRowNumber() {
        // Business renderers (sections, notes, dashboards, etc.) own their column layout.
        return this.constructor === ListRenderer && !this.props.list.context?.qing_hide_row_number;
    },

    processAllColumn(allColumns, list) {
        const columns = super.processAllColumn(allColumns, list);
        return this.qingShowRowNumber ? [ROW_NUMBER_COLUMN, ...columns] : columns;
    },
});
