import { SaleTemplateDropdown } from "@sale_management/views/components/template_dropdown";
import { patch } from "@web/core/utils/patch";

patch(SaleTemplateDropdown.prototype, {
    async createQuotation({ additionalContext = {} } = {}) {
        // The native form button replaces the record context when loading a new
        // quotation. Keep the rental action default through consecutive creates.
        if (this.isFormView && this.props.record.context.default_is_qing_rental) {
            additionalContext = {
                default_is_qing_rental: true,
                ...additionalContext,
            };
        }
        return super.createQuotation({ additionalContext });
    },
});
