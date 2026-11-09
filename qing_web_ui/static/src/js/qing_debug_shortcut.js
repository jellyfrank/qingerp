import { Component, usePlugin } from "@odoo/owl";
import { router } from "@web/core/browser/router";
import { DebugModePlugin } from "@web/core/debug_mode_plugin";
import "@web/core/debug/debug_providers";
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
