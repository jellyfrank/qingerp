/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { SearchBarDropdown } from "@web/search/search_bar_dropdown";

const DIALOG_MENU_CLASS = "o_qing_dialog_search_menu";
const MIN_DESKTOP_WIDTH = 768;

/**
 * In dialogs the filter menu is anchored to the toggler at the end of the
 * search pill, so a menu wider than the pill overhangs the dialog edge.
 * Pin it under the pill instead: same left edge, same width.
 */
export function alignMenuToSearchPill(menuEl, togglerEl) {
    const pillEl = togglerEl?.closest(".modal .o_cp_searchview");
    if (!menuEl?.classList.contains("o_search_bar_menu") || !pillEl) {
        return false;
    }
    if (window.innerWidth < MIN_DESKTOP_WIDTH) {
        menuEl.classList.remove(DIALOG_MENU_CLASS);
        menuEl.style.removeProperty("width");
        return false;
    }
    const pill = pillEl.getBoundingClientRect();
    menuEl.classList.add(DIALOG_MENU_CLASS);
    // Native w-md-auto sets width with !important.
    menuEl.style.setProperty("width", `${pill.width}px`, "important");
    menuEl.style.left = `${pill.left}px`;
    return true;
}

patch(SearchBarDropdown.prototype, {
    setTargetDirectionClass(direction) {
        super.setTargetDirectionClass(direction);
        alignMenuToSearchPill(this.menuRef?.(), this.target);
    },
});
