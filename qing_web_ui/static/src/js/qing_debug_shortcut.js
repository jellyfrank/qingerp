import { Component, usePlugin } from "@odoo/owl";
import { router } from "@web/core/browser/router";
import { DebugModePlugin } from "@web/core/debug_mode_plugin";
import "@web/core/debug/debug_providers";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";

patch(registry.category("command_provider").get("debug"), {
    provide(options) {
        if (!user.isInternalUser || !user.isSystem) {
            return [];
        }
        return super.provide(options);
    },
});

export class QingDebugShortcut extends Component {
    static template = "qing_web_ui.DebugShortcut";

    activate() {
        // Native routing retains the current action, record and company context.
        router.pushState({ debug: "1" }, { reload: true });
    }
}

registry.category("systray").add("qing_web_ui.debug_shortcut", {
    Component: QingDebugShortcut,
    isDisplayed() {
        const debugMode = usePlugin(DebugModePlugin);
        // Once active, the native developer menu occupies this same position.
        return user.isInternalUser && user.isSystem && !debugMode.isActive();
    },
}, { sequence: 100 });

// Phones hide the developer shortcut and the native developer menu in the top
// bar (see mobile_shell.scss); the avatar menu keeps the switch reachable.
// User menu items run inside the menu's plugin scope, as isDisplayed does above.
export function qingDebugMenuItem(env) {
    const active = usePlugin(DebugModePlugin).isActive();
    return {
        type: "item",
        id: "qing_debug_mode",
        description: active ? _t("Leave developer mode") : _t("Developer mode"),
        hide: !env.services.ui.isSmall || !user.isInternalUser || !user.isSystem,
        callback: () => router.pushState({ debug: active ? 0 : "1" }, { reload: true }),
        sequence: 48,
    };
}

registry.category("user_menuitems").add("qing_debug_mode", qingDebugMenuItem);
