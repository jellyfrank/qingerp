/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { UserMenu } from "@web/webclient/user_menu/user_menu";

const userMenuRegistry = registry.category("user_menuitems");
const SUPPORT_URL = "https://www.qingsolution.com";

function helpSupportMenuItem() {
    return {
        type: "item",
        id: "qing_help_support",
        description: _t("Help & Support"),
        href: SUPPORT_URL,
        callback: () => browser.open(SUPPORT_URL, "_blank"),
        sequence: 55,
    };
}

// Filter after all addons have registered their user menu entries.
patch(UserMenu.prototype, {
    getElements() {
        return super.getElements(...arguments).filter(
            (item) => !["mysubscription_user_menu", "odoo_account", "support"].includes(item.id)
        );
    },
});

userMenuRegistry.add("qing_help_support", helpSupportMenuItem);
