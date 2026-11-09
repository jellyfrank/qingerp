import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Many2OneField, buildM2OFieldDescription } from "@web/views/fields/many2one/many2one_field";


export class StockMoveProductLink extends Many2OneField {
    static template = "qing_stock.StockMoveProductLink";
    static components = Many2OneField.components;

    setup() {
        this.actionService = useService("action");
    }

    get product() {
        return this.props.record.data[this.props.name];
    }

    get productUrl() {
        return this.product ? `/odoo/m-product.product/${this.product.id}` : "#";
    }

    async openProduct(event) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        await this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: this.product.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("fields").add(
    "qing_stock_move_product_link",
    buildM2OFieldDescription(StockMoveProductLink)
);
