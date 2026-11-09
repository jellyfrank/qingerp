/** @odoo-module **/

import { proxy } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { exprToBoolean } from "@web/core/utils/strings";
import { useService } from "@web/core/utils/hooks";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { SearchBarMenu } from "@web/search/search_bar_menu/search_bar_menu";
import { ListController } from "@web/views/list/list_controller";
import { qingHeaderActionIcon } from "./qing_header_action_icons";

// Dialog searches and mobile keep the native compact search panel.
function splitSearch(component) {
    return ["list", "kanban", "graph"].includes(component.env.config.viewType) &&
        !(component.ui || component.uiService).isSmall && !component.env.inDialog;
}

patch(SearchBar.prototype, {
    get qingSplitSearch() {
        return splitSearch(this);
    },
});

patch(SearchBarMenu.prototype, {
    setup() {
        super.setup(...arguments);
        this.uiService = useService("ui");
        this.qingMenu = proxy({ category: "filter" });
    },
    get qingSplitSearch() {
        return splitSearch(this);
    },
    get qingFilterCount() {
        return this.filterItems.filter((item) => item.isActive).length;
    },
    openQingCategory(category) {
        if (this.qingMenu.category === category && this.props.dropdownState.isOpen) {
            this.props.dropdownState.close();
            return;
        }
        this.qingMenu.category = category;
        this.props.dropdownState.open();
    },
});

patch(ListController.prototype, {
    qingHeaderActionIcon(button) {
        return qingHeaderActionIcon(button.string) || button.icon;
    },
    qingIsCommonAction(button) {
        return Boolean(qingHeaderActionIcon(button.string));
    },
    qingHeaderActionClass(button) {
        return [button.className, this.qingIsCommonAction(button) && "o_qing_icon_action"]
            .filter(Boolean).join(" ");
    },
    qingHeaderActionAttrs(button) {
        return this.qingIsCommonAction(button)
            ? { ...button.attrs, "aria-label": button.string }
            : button.attrs;
    },
    get qingCanExport() {
        return !this.env.inDialog && !this.uiService.isSmall && !this.editedRecord &&
            !this.offlinePlugin.isOffline() && this.isExportEnable &&
            exprToBoolean(this.env.config.viewArch.getAttribute("export_xlsx"), true);
    },
});
