import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

// Keep the server-authorized menu tree intact; only its navigation presentation varies by viewport.
patch(NavBar.prototype, {
    get currentAppSections() {
        const sections = super.currentAppSections;
        return this.ui.isSmall ? sections : sections.filter((menu) => menu.xmlid !== "qing_stock_barcode.menu_scan_workbench");
    },
    set currentAppSections(value) { super.currentAppSections = value; },
});
