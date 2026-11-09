/** @odoo-module **/
import { expect, test, animationFrame } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { user } from "@web/core/user";
import { registry } from "@web/core/registry";
import { QingThemeShortcut } from "@qing_web_ui/js/qing_theme_shortcut";

defineMailModels();

test("theme shortcut saves one key and reflects settings changes", async () => {
    const old = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ themeMode: "light", accentColor: "green" });
        onRpc("res.users.settings", "set_qing_theme_preferences", ({ args }) => {
            expect(args).toEqual([{ base: { themeMode: "dark" } }]);
            return { base: { ...window.qingWebUi.getPreferences(), ...args[0].base } };
        });
        await mountWithCleanup(QingThemeShortcut);
        await contains(".o_qing_theme_shortcut").click();
        expect(".o_qing_theme_choice").toHaveCount(3);
        expect('[data-theme-mode="light"][aria-checked="true"]').toHaveCount(1);
        await contains('[data-theme-mode="dark"]').click();
        expect(window.qingWebUi.getPreferences().themeMode).toBe("dark");
        expect(window.qingWebUi.getPreferences().accentColor).toBe("green");
        expect('.o_qing_theme_shortcut [data-theme-icon="dark"]').toHaveCount(1);
        window.qingWebUi.setPreferences({ themeMode: "system" });
        await animationFrame();
        expect('.o_qing_theme_shortcut [data-theme-icon="system"]').toHaveCount(1);
    } finally { window.qingWebUi.setPreferences(old); }
});

test("pending theme save prevents a second write", async () => {
    const old = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ themeMode: "light" });
        let finish, count = 0;
        onRpc("res.users.settings", "set_qing_theme_preferences", ({ args }) => {
            count++;
            return new Promise((resolve) => { finish = () => resolve({ base: { ...window.qingWebUi.getPreferences(), ...args[0].base } }); });
        });
        const shortcut = await mountWithCleanup(QingThemeShortcut);
        const saving = shortcut.selectMode("dark");
        await animationFrame();
        expect(".o_qing_theme_shortcut").toHaveAttribute("aria-disabled", "true");
        await shortcut.selectMode("system");
        expect(count).toBe(1);
        finish(); await saving;
        await animationFrame();
        expect(window.qingWebUi.getPreferences().themeMode).toBe("dark");
        expect(shortcut.state.saving).toBe(false);
    } finally { window.qingWebUi.setPreferences(old); }
});

test("failed theme save leaves appearance intact and can be retried", async () => {
    const old = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ themeMode: "light" });
        const shortcut = await mountWithCleanup(QingThemeShortcut);
        patchWithCleanup(shortcut.orm, { async call() { throw new Error("QA offline"); } });
        let notice;
        patchWithCleanup(shortcut.notification, { add(message, options) { notice = options.type; } });
        await shortcut.selectMode("dark");
        expect(window.qingWebUi.getPreferences().themeMode).toBe("light");
        expect(shortcut.state.saving).toBe(false);
        expect(notice).toBe("danger");
    } finally { window.qingWebUi.setPreferences(old); }
});

test("theme shortcut is available to internal users only", () => {
    const entry = registry.category("systray").get("qing_web_ui.theme_shortcut");
    patchWithCleanup(user, { isInternalUser: true, isSystem: false });
    expect(entry.isDisplayed()).toBe(true);
    patchWithCleanup(user, { isInternalUser: false });
    expect(entry.isDisplayed()).toBe(false);
});
