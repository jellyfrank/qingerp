/** @odoo-module **/

import { onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { WebClient } from "@web/webclient/webclient";

// mommy_base handles the title and user menu itself on upgraded databases.
if (!session.qing_legacy_mommy_base) {
    const userMenu = registry.category("user_menuitems");
    for (const item of ["documentation", "support", "shortcuts", "odoo_account"]) {
        if (userMenu.contains(item)) {
            userMenu.remove(item);
        }
    }

    patch(WebClient.prototype, {
        setup() {
            super.setup(...arguments);
            onWillStart(() => {
                if (session.qing_web_title) {
                    this.title.setParts({ zopenerp: session.qing_web_title });
                }
            });
        },
    });
}
