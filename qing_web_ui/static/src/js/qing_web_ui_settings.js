/** @odoo-module **/

import { Component, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

const DENSITY_OPTIONS = [
    {
        value: "standard",
        label: _t("Standard"),
        hint: _t("Balanced spacing for everyday use."),
    },
    {
        value: "compact",
        label: _t("Compact"),
        hint: _t("Tighter rows and smaller paddings."),
    },
    {
        value: "relaxed",
        label: _t("Relaxed"),
        hint: _t("More spacing and larger touch targets."),
    },
];

const SIDEBAR_OPTIONS = [
    {
        value: "expanded",
        label: _t("Expanded"),
        hint: _t("Show full app menu labels."),
    },
    {
        value: "collapsed",
        label: _t("Collapsed"),
        hint: _t("Keep only app icons in the left rail."),
    },
];

const SIDEBAR_GROUP_OPTIONS = [
    {
        value: "collapsed",
        label: _t("All collapsed"),
        hint: _t("Start with every menu section collapsed."),
    },
    {
        value: "expanded",
        label: _t("All expanded"),
        hint: _t("Start with every menu section expanded."),
    },
];

const SIDEBAR_STYLE_OPTIONS = [
    {
        value: "tree",
        label: _t("Menu tree"),
        hint: _t("Indented groups that open and close in place."),
    },
    {
        value: "islands",
        label: _t("Grouped cards"),
        hint: _t("Each menu group sits on its own card; nested groups are not indented."),
    },
    {
        value: "rail",
        label: _t("Icon rail"),
        hint: _t("Choose a group from the icon rail and browse or search its menus beside it."),
    },
];

const THEME_OPTIONS = [
    { value: "system", label: _t("Follow system") },
    { value: "light", label: _t("Qing Light") },
    { value: "dark", label: _t("Qing Dark") },
];

const ACCENT_OPTIONS = [
    { value: "teal", label: _t("Teal"), color: "#2b6c6a" },
    { value: "blue", label: _t("Blue"), color: "#2f6aff" },
    { value: "purple", label: _t("Purple"), color: "#7b507a" },
    { value: "amber", label: _t("Amber"), color: "#c27a06" },
    { value: "green", label: _t("Green"), color: "#2c9660" },
];

const TEXT_SIZE_OPTIONS = [
    { value: "small", label: _t("Small") },
    { value: "medium", label: _t("Medium") },
    { value: "large", label: _t("Large") },
];

class QingThemeSettingsDialog extends Component {
    static template = "qing_web_ui.QingThemeSettingsDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        title: t.string().optional(),
    });

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const current = window.qingWebUi ? window.qingWebUi.getPreferences() : {};
        this.state = proxy({
            saving: false,
            density: current.density || "standard",
            sidebarState: current.sidebarState || "expanded",
            sidebarGroupsDefault: current.sidebarGroupsDefault || "collapsed",
            sidebarStyle: current.sidebarStyle || "tree",
            listActionsAlignment: current.listActionsAlignment || "right",
            chatterPosition: current.chatterPosition || "auto",
            reducedMotion: Boolean(current.reducedMotion),
            themeMode: current.themeMode || "system",
            accentColor: current.accentColor || "teal",
            textSize: current.textSize || "medium",
            autoOpenLauncher: Boolean(current.autoOpenLauncher),
            swipeActions: Boolean(current.swipeActions),
        });
    }

    get title() {
        return this.props.title || _t("Qing Theme");
    }

    get densityHint() {
        const selected = this.densityOptions.find((option) => option.value === this.state.density);
        return selected ? selected.hint : "";
    }

    get labels() {
        return {
            density: _t("Density"),
            navigation: _t("Navigation"),
            menuGroupsDefault: _t("Default menu group state"),
            sidebarStyle: _t("Sidebar style"),
            sidebarStyleHint: _t("A collapsed sidebar always shows icons only."),
            reducedMotion: _t("Reduce motion"),
            reducedMotionHint: _t("Disable non-essential transition animations."),
            theme: _t("Theme"),
            accent: _t("Accent color"),
            textSize: _t("Text size"),
            navigationBehavior: _t("Navigation behavior"),
            autoOpenLauncher: _t("Open launcher on launch"),
            autoOpenLauncherHint: _t("Show the application drawer when the backend starts."),
            swipeActions: _t("Swipe actions"),
            swipeActionsHint: _t("Enable horizontal gestures on supported list rows."),
            apply: _t("Apply"),
            restoreDefaults: _t("Restore defaults"),
            cancel: _t("Cancel"),
            description: _t(
                "Save your theme preferences to your account and use them in other browsers."
            ),
        };
    }

    get densityOptions() {
        return DENSITY_OPTIONS;
    }

    get sidebarOptions() {
        return SIDEBAR_OPTIONS;
    }

    get sidebarStyleOptions() {
        return SIDEBAR_STYLE_OPTIONS;
    }

    get sidebarGroupOptions() {
        return SIDEBAR_GROUP_OPTIONS;
    }

    get themeOptions() {
        return THEME_OPTIONS;
    }

    get accentOptions() {
        return ACCENT_OPTIONS;
    }

    get textSizeOptions() {
        return TEXT_SIZE_OPTIONS;
    }

    setDensity(event) {
        this.state.density = event.currentTarget.value;
    }

    setSidebarState(event) {
        this.state.sidebarState = event.currentTarget.value;
    }

    setSidebarStyle(event) {
        this.state.sidebarStyle = event.currentTarget.value;
    }

    setSidebarGroupsDefault(event) {
        this.state.sidebarGroupsDefault = event.currentTarget.value;
    }

    setReducedMotion(event) {
        this.state.reducedMotion = event.currentTarget.checked;
    }

    setThemeMode(event) {
        this.state.themeMode = event.currentTarget.value;
    }

    setAccentColor(event) {
        this.state.accentColor = event.currentTarget.value;
    }

    setTextSize(event) {
        this.state.textSize = event.currentTarget.value;
    }

    setBooleanPreference(name, event) {
        this.state[name] = event.currentTarget.checked;
    }

    getThemePreferenceChanges(reset = false) {
        const defaults = window.qingWebUi.getDefaults();
        const values = reset ? defaults : Object.fromEntries(
            Object.keys(defaults).map((key) => [
                key, key === "schema" ? defaults.schema : this.state[key],
            ])
        );
        return { base: values };
    }

    applySavedThemePreferences(saved) {
        window.qingWebUi.setPreferences(saved.base);
    }

    async saveThemePreferences(reset = false) {
        if (this.state.saving) return;
        this.state.saving = true;
        try {
            const saved = await this.orm.call("res.users.settings", "set_qing_theme_preferences", [
                this.getThemePreferenceChanges(reset),
            ]);
            this.applySavedThemePreferences(saved);
            this.props.close();
        } catch (_error) {
            this.notification.add(_t("Could not save theme preferences. Please try again."), { type: "danger" });
        } finally {
            this.state.saving = false;
        }
    }

    applyPreferences() {
        return this.saveThemePreferences();
    }

    resetPreferences() {
        return this.saveThemePreferences(true);
    }
}

function settingsMenuItem(env) {
    return {
        type: "item",
        id: "qing_theme_settings",
        description: _t("Qing Theme"),
        callback: () => {
            env.services.dialog.add(QingThemeSettingsDialog, {
                title: _t("Qing Theme Settings"),
            });
        },
        sequence: 52,
    };
}

registry.category("user_menuitems").add("qing_theme_settings", settingsMenuItem);

export { QingThemeSettingsDialog };
