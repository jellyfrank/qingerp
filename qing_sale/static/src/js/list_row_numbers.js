/** @odoo-module **/

import "@qing_base/js/list_row_numbers";
import { patch } from "@web/core/utils/patch";
import { SaleFileUploadListRenderer } from "@sale/views/sale_file_upload_list/sale_file_upload_list_renderer";
import { SaleOrderTemplateLineListRenderer } from "@sale_management/fields/sale_order_template_line_field/sale_order_template_line_field";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";

// Reuse Qing Base's display-only column, pagination and templates.
patch(SaleFileUploadListRenderer.prototype, {
    get qingShowRowNumber() {
        return !this.props.list.context?.qing_hide_row_number;
    },
});

patch(SaleOrderLineListRenderer, {
    template: "qing_sale.OrderLineListRenderer",
    recordRowTemplate: "qing_sale.OrderLineRecordRow",
});

for (const Renderer of [SaleOrderLineListRenderer, SaleOrderTemplateLineListRenderer]) {
    patch(Renderer.prototype, {
        get qingShowRowNumber() {
            return !this.props.list.context?.qing_hide_row_number;
        },

        getActiveColumns() {
            const columns = super.getActiveColumns();
            // Avoid a second index when the native report line-number option is enabled.
            return this.qingShowRowNumber
                ? columns.filter((column) => column.name !== "line_number")
                : columns;
        },

        getSectionAndNoteColumns(columns, record) {
            const numberColumn = columns.find((column) => column.type === "qing_row_number");
            if (!numberColumn) {
                return super.getSectionAndNoteColumns(columns, record);
            }
            // Reserve an index cell without inflating the description's colspan.
            const contentColumns = columns.filter((column) => column !== numberColumn);
            return [numberColumn, ...super.getSectionAndNoteColumns(contentColumns, record)];
        },
    });
}

patch(SaleOrderTemplateLineListRenderer, {
    template: "qing_sale.OrderLineListRenderer",
    recordRowTemplate: "qing_sale.TemplateLineRecordRow",
});
