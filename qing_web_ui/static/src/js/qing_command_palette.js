/** @odoo-module **/
import { CommandPalette } from "@web/core/commands/command_palette";
import { patch } from "@web/core/utils/patch";

patch(CommandPalette.prototype, {
    onKeyDown(event) {
        if (event.key === "Escape") {
            event.stopPropagation();
            this.props.close();
            return;
        }
        super.onKeyDown(event);
    },
});
