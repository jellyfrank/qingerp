/** @odoo-module **/

import { onMounted, onPatched } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { ListRenderer } from "@web/views/list/list_renderer";

patch(ListRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        let previousWidth;
        const indexWidth = () => this.columns.find((column) => column.type === "qing_row_number")?.attrs.width;
        onMounted(() => { previousWidth = indexWidth(); });
        onPatched(() => {
            const width = indexWidth();
            if (width !== previousWidth) {
                previousWidth = width;
                this.columnWidths.resetWidths();
            }
        });
    },

    getActiveColumns() {
        // Adapt Qing Base's existing display column through the native width
        // hook. Size for the total count, so long indexes remain readable and
        // the hook cannot distribute spare table width into this utility column.
        const width = Math.max(28, String(this.props.list.count || 0).length * 10) + "px";
        return super.getActiveColumns().map((column) =>
            column.type === "qing_row_number"
                ? { ...column, attrs: { ...column.attrs, width } }
                : column
        );
    },
});
