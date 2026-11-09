/** @odoo-module **/

import { Component, proxy } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

export class QingThemeShortcut extends Component {
    static template = "qing_web_ui.ThemeShortcut";
    static components = { Dropdown, DropdownItem };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = proxy({ mode: window.qingWebUi.getPreferences().themeMode, saving: false });
        useBus(window, "qing-web-ui:preferences-changed", () => {
            this.state.mode = window.qingWebUi.getPreferences().themeMode;
        });
    }

    get options() {
        return [
            { value: "light", label: _t("Light") },
            { value: "dark", label: _t("Dark") },
            { value: "system", label: _t("Follow system") },
        ];
    }

    get label() {
        return _t("Theme: %s", this.options.find((option) => option.value === this.state.mode).label);
    }

    async selectMode(mode) {
        if (this.state.saving || mode === this.state.mode) return;
        this.state.saving = true;
        try {
            // The account API merges this one base key and preserves optional appearances.
            const saved = await this.orm.call("res.users.settings", "set_qing_theme_preferences", [{
                base: { themeMode: mode },
            }]);
            window.qingWebUi.setPreferences(saved.base);
        } catch (_error) {
            this.notification.add(_t("Could not save theme preferences. Please try again."), { type: "danger" });
        } finally {
            this.state.saving = false;
        }
    }
}

registry.category("systray").add("qing_web_ui.theme_shortcut", {
    Component: QingThemeShortcut,
    isDisplayed: () => user.isInternalUser,
}, { sequence: 105 });
