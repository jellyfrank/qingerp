/** @odoo-module **/

import { Component, proxy, xml } from "@odoo/owl";
import { DebugMenu } from "@web/core/debug/debug_menu";
import { useOwnDebugContext } from "@web/core/debug/debug_context";
import { animationFrame, expect, mockMatchMedia, test } from "@odoo/hoot";
import { press, resize } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { registry } from "@web/core/registry";
import { assignDialogTestEnv, clearRegistry, contains, defineMenus, defineModels, fields, getService, makeTestApp, models, onRpc, mountView, mountWithCleanup, mountWithSearch, patchWithCleanup, serverState } from "@web/../tests/web_test_helpers";
import { buildQingSidebarRows, buildQingSidebarSections, fitQingApps, orderQingApps, moveQingApp } from "@qing_web_ui/js/qing_web_ui_navbar";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { cookie } from "@web/core/browser/cookie";
import { router } from "@web/core/browser/router";
import "@qing_web_ui/js/qing_debug_shortcut";
import { Breadcrumbs } from "@web/search/breadcrumbs/breadcrumbs";
import { NavBar } from "@web/webclient/navbar/navbar";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { UserMenu } from "@web/webclient/user_menu/user_menu";
import { SwitchCompanyMenu } from "@web/webclient/switch_company_menu/switch_company_menu";
import { WebClient } from "@web/webclient/webclient";
import { QingThemeSettingsDialog } from "@qing_web_ui/js/qing_web_ui_settings";
import { qingForcesSideChatter } from "@qing_web_ui/js/qing_chatter_position";
import { SIZES } from "@web/core/ui/ui_utils";
import { menuIconHref } from "@qing_web_ui/js/qing_menu_icons";

test("invoice interface menus use dedicated icons", () => {
    expect(menuIconHref({ xmlid: "mommy_fapiao.menu_fapiao_order" })).toBe(
        "/qing_web_ui/static/src/icons/menu.svg?v=8#invoiceClock"
    );
    expect(menuIconHref({ xmlid: "mommy_fapiao.menu_fapiao_provider" })).toBe(
        "/qing_web_ui/static/src/icons/menu.svg?v=8#key"
    );
});

class QingSearchRecord extends models.Model {
    _name = "qing.search.record";
    name = fields.Char();
    _records = [{ id: 1, name: "Order" }];
}

defineModels([QingSearchRecord]);
defineMailModels();

test("sidebar groups keep the active menu visible and fold unrelated sections", () => {
    const menus = [
        { id: 1, name: "SMS", childrenTree: [{ id: 2, actionID: 20 }] },
        { id: 3, name: "Mail", childrenTree: [{ id: 4, actionID: 40 }] },
    ];
    expect(buildQingSidebarRows(menus, {}, 20).map((row) => row.menu.id)).toEqual([1, 2, 3]);
    expect(buildQingSidebarRows(menus, { 1: false, 3: true }, 20).map((row) => row.menu.id)).toEqual([1, 3, 4]);
    expect(buildQingSidebarRows(menus, { 1: false }, 20, true).map((row) => row.menu.id)).toEqual([1, 2, 3, 4]);
});

test("sidebar group defaults can collapse or expand every section", () => {
    const menus = [
        { id: 1, name: "SMS", childrenTree: [{ id: 2, actionID: 20 }] },
        { id: 3, name: "Mail", childrenTree: [{ id: 4, actionID: 40 }] },
    ];
    expect(buildQingSidebarRows(menus, {}, 20, false, "collapsed").map((row) => row.menu.id)).toEqual([1, 3]);
    expect(buildQingSidebarRows(menus, {}, 20, false, "expanded").map((row) => row.menu.id)).toEqual([1, 2, 3, 4]);
    expect(buildQingSidebarRows(menus, { 1: true, 3: false }, 20, false, "collapsed").map((row) => row.menu.id)).toEqual([1, 2, 3]);
    expect(buildQingSidebarRows(menus, {}, 20, true, "collapsed").map((row) => row.menu.id)).toEqual([1, 2, 3, 4]);
});

test("navigating to a menu opens its containing sidebar section", () => {
    const navbar = {
        currentAppSections: [
            { id: 1, childrenTree: [{ id: 2, actionID: 20 }] },
            { id: 3, childrenTree: [{ id: 4, actionID: 40 }] },
        ],
        qingSidebarGroups: {},
    };
    NavBar.prototype.openQingActiveSidebarGroups.call(navbar, 20);
    expect(navbar.qingSidebarGroups).toEqual({ 1: true });
});

test("navbar renders groups using the saved theme default", () => {
    const menus = [
        { id: 1, name: "SMS", childrenTree: [{ id: 2, actionID: 20 }] },
        { id: 3, name: "Mail", childrenTree: [{ id: 4, actionID: 40 }] },
    ];
    const navbar = {
        currentAppSections: menus,
        qingSidebarGroups: {},
        actionService: { currentController: { action: { id: 20 } } },
    };
    const getter = Object.getOwnPropertyDescriptor(NavBar.prototype, "qingSidebarRows").get;
    const original = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ sidebarGroupsDefault: "collapsed" });
        expect(getter.call(navbar).map((row) => row.menu.id)).toEqual([1, 3]);
        window.qingWebUi.setPreferences({ sidebarGroupsDefault: "expanded" });
        expect(getter.call(navbar).map((row) => row.menu.id)).toEqual([1, 2, 3, 4]);
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

const SECTION_MENUS = [
    { id: 1, name: "Contacts", actionID: 10 },
    { id: 2, name: "Configuration", childrenTree: [
        { id: 3, name: "Tags", actionID: 30 },
        { id: 4, name: "Localization", childrenTree: [
            { id: 5, name: "Countries", actionID: 50 },
            { id: 6, name: "China", childrenTree: [{ id: 7, name: "Cities", actionID: 70 }] },
        ] },
        { id: 8, name: "Empty", childrenTree: [{ id: 9, name: "Deep", childrenTree: [{ id: 10, name: "Leaf", actionID: 100 }] }] },
    ] },
];

test("sidebar sections flatten nested groups for cards and the icon rail", () => {
    const sections = buildQingSidebarSections({ id: 99, name: "Contacts" }, SECTION_MENUS);
    expect(sections.map((section) => [section.key, section.title, section.parent, section.items.map((menu) => menu.id)])).toEqual([
        ["root", "", "", [1]],
        ["menu-2", "Configuration", "", [3]],
        ["menu-4", "Localization", "Configuration", [5]],
        ["menu-6", "China", "Localization", [7]],
        ["menu-9", "Deep", "Empty", [10]],
    ]);
    expect(sections[3].path).toEqual(["Configuration", "Localization"]);
    expect(buildQingSidebarSections(null, [])).toEqual([]);
});

test("icon rail follows the current page until a group is picked and searches every group", () => {
    const sections = buildQingSidebarSections({ id: 99, name: "Contacts" }, SECTION_MENUS);
    const proto = NavBar.prototype;
    const navbar = {
        qingSidebarSections: sections,
        qingRailState: { key: null, query: "" },
        currentApp: { name: "Contacts" },
        actionService: { currentController: { action: { id: 70 } } },
        qingSectionLabel: proto.qingSectionLabel,
        qingSectionPath: proto.qingSectionPath,
    };
    const current = Object.getOwnPropertyDescriptor(proto, "qingRailCurrent").get;
    const results = Object.getOwnPropertyDescriptor(proto, "qingRailResults").get;
    expect(current.call(navbar).key).toBe("menu-6");
    proto.selectQingRailSection.call(navbar, sections[1]);
    expect(current.call(navbar).key).toBe("menu-2");
    navbar.qingRailState.key = "menu-404";
    expect(current.call(navbar).key).toBe("menu-6");
    expect(results.call(navbar)).toBe(null);
    navbar.qingRailState.query = " CIT ";
    expect(results.call(navbar).map((result) => [result.menu.id, result.path])).toEqual([
        [7, "Configuration › Localization › China"],
    ]);
    expect(proto.qingSectionLabel.call(navbar, sections[0])).toBe("Contacts");
});

test("sidebar style is a saved preference and collapse always uses the icon tree", () => {
    const original = window.qingWebUi.getPreferences();
    const style = Object.getOwnPropertyDescriptor(NavBar.prototype, "qingSidebarStyle").get;
    try {
        expect(window.qingWebUi.getDefaults().sidebarStyle).toBe("tree");
        for (const value of ["tree", "islands", "rail"]) {
            expect(window.qingWebUi.setPreferences({ sidebarStyle: value, sidebarState: "expanded" }).sidebarStyle).toBe(value);
            expect(document.body.dataset.qingSidebarStyle).toBe(value);
            expect(style.call({ qingSidebarCollapsed: false })).toBe(value);
            expect(style.call({ qingSidebarCollapsed: true })).toBe("tree");
        }
        expect(window.qingWebUi.setPreferences({ sidebarStyle: "cards" }).sidebarStyle).toBe("tree");
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

test("default app follows the saved personal menu order", () => {
    const apps = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const selected = [];
    const webClient = {
        menuService: {
            getApps: () => apps,
            selectMenu: (app) => selected.push(app.id),
        },
    };
    patchWithCleanup(session, { qing_app_menu_order: [999, 3, 1, 2] });
    WebClient.prototype._loadDefaultApp.call(webClient);
    expect(selected).toEqual([3]);

    session.qing_app_menu_order = [999];
    WebClient.prototype._loadDefaultApp.call(webClient);
    expect(selected).toEqual([3, 1]);
});

test("sidebar logo follows the company image revision, not the user avatar", async () => {
    patchWithCleanup(user, { activeCompany: { id: 1 }, writeDate: "2026-09-01 00:00:00" });
    patchWithCleanup(session, { qing_company_logo_versions: { 1: "logo-10-before", 2: "logo-20-other" } });
    const navbar = await mountWithCleanup(NavBar);
    const before = navbar.qingCompanyLogoUrl;
    expect(new URL(before).searchParams.get("unique")).toBe("logo-10-before");
    session.qing_company_logo_versions[1] = "logo-10-after";
    expect(navbar.qingCompanyLogoUrl).not.toBe(before);
    expect(user.writeDate).toBe("2026-09-01 00:00:00");
    user.activeCompany.id = 2;
    const other = new URL(navbar.qingCompanyLogoUrl);
    expect(other.pathname).toBe("/web/image/res.company/2/logo");
    expect(other.searchParams.get("unique")).toBe("logo-20-other");
    delete session.qing_company_logo_versions[2];
    expect(new URL(navbar.qingCompanyLogoUrl).searchParams.has("unique")).toBe(false);
});

test("search bar extension renders with the Odoo 20 template contract", async () => {
    await mountWithSearch(SearchBar, {
        resModel: "qing.search.record",
        searchViewId: false,
    });
    expect(".o_cp_searchview").toHaveCount(1);
    expect(".o_searchview_input").toHaveCount(1);
});

test("list header reads the Odoo 20 offline plugin while rendering export controls", async () => {
    await mountView({
        type: "list",
        resModel: "qing.search.record",
        arch: `<list><field name="name"/></list>`,
    });
    expect(".o_list_view").toHaveCount(1);
    expect(".o_control_panel").toHaveCount(1);
    expect(".o_list_button_add .oi[data-icon='add']").toHaveCount(1);
});

test.tags("desktop");
test("list actions keep selection feedback and show custom actions as text", async () => {
    await mountView({
        type: "list",
        resModel: "qing.search.record",
        arch: `<list><header><button name="action_create_invoice" type="object" string="Create Invoice"/><button name="action_print" type="object" string="Print"/></header><field name="name"/></list>`,
    });
    expect(".o_list_button_add[aria-label='New'] .oi[data-icon='add']").toHaveCount(1);
    await contains(".o_list_renderer .o_list_record_selector input").click();
    expect(".o_selection_container .o_selection_box").toHaveText(/1\s+selected/);
    expect(".o_selection_container").toHaveText(/Create Invoice/);
    expect(".o_selection_container button.o_qing_icon_action[aria-label='Create Invoice']").toHaveCount(0);
    expect(".o_selection_container button.o_qing_icon_action[aria-label='Print'] .oi[data-icon='print']").toHaveCount(1);
});

test("preference API is exposed", () => {
    expect(Boolean(window.qingWebUi)).toBe(true);
    if (!window.qingWebUi) {
        throw new Error("qingWebUi namespace should be registered");
    }
    for (const method of ["getPreferences", "setPreferences", "resetPreferences"]) {
        expect(typeof window.qingWebUi[method]).toBe("function");
        if (typeof window.qingWebUi[method] !== "function") {
            throw new Error(`${method} should exist`);
        }
    }
});

test("theme menuitem is registered in user menu", () => {
    const userMenuRegistry = registry.category("user_menuitems");
    if (!userMenuRegistry.contains("qing_theme_settings")) {
        throw new Error("user menu item should be available");
    }
    expect(userMenuRegistry.contains("qing_theme_settings")).toBe(true);
    const itemFactory = userMenuRegistry.get("qing_theme_settings");
    let opened = false;
    const item = itemFactory({ services: { dialog: { add: () => (opened = true) } } });
    if (item.type !== "item" || item.id !== "qing_theme_settings") {
        throw new Error("theme settings menu item metadata is invalid");
    }
    expect(item.type).toBe("item");
    expect(item.id).toBe("qing_theme_settings");
    if (typeof item.callback !== "function") {
        throw new Error("theme settings callback should be callable");
    }
    expect(typeof item.callback).toBe("function");
    item.callback();
    if (!opened) {
        throw new Error("theme settings callback should invoke dialog.add()");
    }
    expect(opened).toBe(true);
});

test("navbar launcher extension keeps native menu services", () => {
    expect(typeof NavBar.prototype.toggleQingLauncher).toBe("function");
    expect(typeof NavBar.prototype.selectQingLauncherApp).toBe("function");
    if (typeof NavBar.prototype.toggleQingLauncher !== "function") {
        throw new Error("launcher toggle is patched");
    }
    if (typeof NavBar.prototype.selectQingLauncherApp !== "function") {
        throw new Error("launcher selection is patched");
    }
    if (!Object.getOwnPropertyDescriptor(NavBar.prototype, "qingLauncherApps")?.get) {
        throw new Error("launcher apps getter is available");
    }
    expect(Boolean(Object.getOwnPropertyDescriptor(NavBar.prototype, "qingLauncherApps")?.get)).toBe(true);
});

test("preferences schema and enums are normalized", () => {
    const original = window.qingWebUi.getPreferences();
    const prefs = window.qingWebUi.getPreferences();
    expect(prefs.schema).toBe(4);
    if (prefs.schema !== 4) throw new Error("schema should be the current schema version");
    if (!["standard", "compact", "relaxed"].includes(prefs.density)) throw new Error("density is not normalized");
    if (!["expanded", "collapsed"].includes(prefs.sidebarState)) throw new Error("sidebar state is not normalized");
    if (!["expanded", "collapsed"].includes(prefs.sidebarGroupsDefault)) throw new Error("sidebar group default is not normalized");
    if (typeof prefs.reducedMotion !== "boolean") throw new Error("reducedMotion should be boolean");
    if (!["system", "light", "dark"].includes(prefs.themeMode)) throw new Error("theme mode is not normalized");
    if (!["teal", "blue", "purple", "amber", "green"].includes(prefs.accentColor)) throw new Error("accent is not normalized");
    if (!["small", "medium", "large"].includes(prefs.textSize)) throw new Error("text size is not normalized");
    try {
        expect(window.qingWebUi.setPreferences({ sidebarGroupsDefault: "invalid" }).sidebarGroupsDefault).toBe("collapsed");
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

test("chatter position offers automatic placement and migrates the old default", () => {
    const original = window.qingWebUi.getPreferences();
    try {
        expect(window.qingWebUi.getDefaults().chatterPosition).toBe("auto");
        for (const value of ["auto", "right", "bottom"]) {
            expect(window.qingWebUi.setPreferences({ chatterPosition: value }).chatterPosition).toBe(value);
            expect(document.body.dataset.qingChatterPosition).toBe(value);
        }
        expect(window.qingWebUi.setPreferences({ chatterPosition: "left" }).chatterPosition).toBe("auto");
        // Schema 3 "right" behaved like today's automatic rule.
        expect(window.qingWebUi.setPreferences({ schema: 3, chatterPosition: "right" }).chatterPosition).toBe("auto");
        expect(window.qingWebUi.setPreferences({ chatterPosition: "right" }).chatterPosition).toBe("right");
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

test("right chatter widens the side layout to XL only outside dialogs", () => {
    const original = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ chatterPosition: "right" });
        expect(qingForcesSideChatter({}, SIZES.XL)).toBe(true);
        expect(qingForcesSideChatter({}, SIZES.LG)).toBe(false);
        expect(qingForcesSideChatter({}, SIZES.XXL)).toBe(false);
        expect(qingForcesSideChatter({ inDialog: true }, SIZES.XL)).toBe(false);
        window.qingWebUi.setPreferences({ chatterPosition: "auto" });
        expect(qingForcesSideChatter({}, SIZES.XL)).toBe(false);
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

test("preferences can be updated and persisted", () => {
    const values = {
        density: "compact", sidebarState: "collapsed", sidebarGroupsDefault: "expanded", reducedMotion: true,
        themeMode: "dark", accentColor: "purple", textSize: "large",
        autoOpenLauncher: true, swipeActions: true,
    };
    const changed = window.qingWebUi.setPreferences(values);
    expect(Boolean(changed)).toBe(true);
    for (const [key, value] of Object.entries(values)) {
        if (changed[key] !== value) throw new Error(`${key} should update`);
    }
    const readback = window.qingWebUi.getPreferences();
    expect(readback.density).toBe("compact");
    expect(readback.accentColor).toBe("purple");
    expect(readback.textSize).toBe("large");
    if (readback.density !== "compact" || readback.accentColor !== "purple" || readback.textSize !== "large") {
        throw new Error("saved preferences should be readable");
    }
    const defaults = window.qingWebUi.resetPreferences();
    expect(Boolean(defaults)).toBe(true);
    const expected = { sidebarState: "expanded", sidebarGroupsDefault: "collapsed", density: "standard", reducedMotion: false,
        themeMode: "system", accentColor: "teal", textSize: "medium" };
    for (const [key, value] of Object.entries(expected)) {
        if (defaults[key] !== value) throw new Error(`${key} should reset to its default`);
    }
});

test("theme settings open and save menu group and theme preferences", async () => {
    onRpc("res.users.settings", "set_qing_theme_preferences", ({ args }) => args[0]);
    const original = window.qingWebUi.getPreferences();
    let closed = false;
    assignDialogTestEnv();
    try {
        const dialog = await mountWithCleanup(QingThemeSettingsDialog, {
            props: { close: () => (closed = true), title: "Qing Theme Settings" },
        });
        expect(dialog.title).toBe("Qing Theme Settings");
        expect("#o_qing_ui_sidebar_groups_collapsed").toHaveCount(1);
        expect("#o_qing_ui_sidebar_groups_expanded").toHaveCount(1);
        dialog.setSidebarGroupsDefault({ currentTarget: { value: "expanded" } });
        dialog.setThemeMode({ currentTarget: { value: "dark" } });
        await dialog.applyPreferences();
        expect(window.qingWebUi.getPreferences().sidebarGroupsDefault).toBe("expanded");
        expect(window.qingWebUi.getPreferences().themeMode).toBe("dark");
        expect(closed).toBe(true);
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});

// Overflow must reserve the trigger and retain the active app when it fits.
test("top apps fit translated widths and preserve the active app", () => {
    const apps = [1, 2, 3, 4].map((id) => ({ id }));
    const widths = new Map([[1, 80], [2, 140], [3, 90], [4, 100]]);
    expect(fitQingApps(apps, widths, 600, 60, 4)).toEqual([1, 2, 3, 4]);
    expect(fitQingApps(apps, widths, 260, 60, 4)).toEqual([1, 4]);
    expect(fitQingApps(apps, widths, 100, 60, 4)).toEqual([]);
    expect(fitQingApps([], widths, 600, 60, 4)).toEqual([]);
    // A visible limit keeps the active app and fills the rest in personal order.
    expect(fitQingApps(apps, widths, 600, 60, 4, 2)).toEqual([1, 4]);
    expect(fitQingApps(apps, widths, 600, 60, 1, 3)).toEqual([1, 2, 3]);
});


test.tags("desktop");
test("top navigation measures Odoo 20 signal refs after mounting", async () => {
    defineMenus([{ id: 101, name: "Sales" }, { id: 102, name: "Inventory" }]);
    const navbar = await mountWithCleanup(NavBar);
    const rail = navbar.root().querySelector(".o_qing_top_apps");
    rail.style.cssText = "width:1000px;flex:none";
    // The unit bundle does not include the backend theme geometry.
    for (const item of rail.querySelectorAll("[data-measure-app]")) {
        item.style.cssText = "display:inline-flex;width:80px";
    }
    rail.querySelector(".o_qing_top_apps__more").style.width = "60px";
    navbar.measureQingTopNav();
    await animationFrame();
    expect(".o_qing_top_apps > .o_qing_top_apps__item").toHaveCount(navbar.menuService.getApps().length);
    rail.style.width = "1px";
    navbar.measureQingTopNav();
    await animationFrame();
    expect(".o_qing_top_apps > .o_qing_top_apps__item").toHaveCount(0);
    rail.style.width = "1000px";
    navbar.measureQingTopNav();
    await animationFrame();
    expect(".o_qing_top_apps > .o_qing_top_apps__item").toHaveCount(navbar.menuService.getApps().length);
});


// The unit bundle omits backend styles, so provide real grid geometry here.
async function openGridSwitcher(columns = 3) {
    const navbar = await mountWithCleanup(NavBar);
    await contains(".o_qing_top_apps__more").click();
    await animationFrame();
    const panel = document.querySelector(".o_qing_app_switcher");
    const list = panel.querySelector(".o_qing_app_switcher__list");
    list.style.cssText = `display:grid;grid-template-columns:repeat(${columns}, minmax(0, 1fr));width:360px`;
    return {
        navbar,
        panel,
        list,
        search: panel.querySelector("input"),
        items: [...panel.querySelectorAll(".o_qing_app_switcher__item")],
    };
}

test.tags("desktop");
test("app grid arrow navigation follows columns and the incomplete last row", async () => {
    defineMenus([1, 2, 3, 4, 5].map((id) => ({ id, name: `Grid app ${id}` })), { mode: "replace" });
    const { search, items, list } = await openGridSwitcher();
    expect(items).toHaveLength(5);
    search.focus();
    await press("ArrowDown");
    expect(document.activeElement).toBe(items[0]);
    await press("ArrowRight");
    expect(document.activeElement).toBe(items[1]);
    await press("ArrowDown");
    expect(document.activeElement).toBe(items[4]);
    await press("ArrowUp");
    expect(document.activeElement).toBe(items[1]);
    await press("ArrowRight");
    await press("ArrowDown");
    expect(document.activeElement).toBe(items[4]);
    await press("ArrowDown");
    expect(document.activeElement).toBe(search);
    await press("ArrowUp");
    expect(document.activeElement).toBe(items[4]);
    // Resizing to two columns changes vertical steps without rebuilding menus.
    list.style.gridTemplateColumns = "repeat(2, minmax(0, 1fr))";
    items[0].focus();
    await press("ArrowDown");
    expect(document.activeElement).toBe(items[2]);
    await press("ArrowDown");
    expect(document.activeElement).toBe(items[4]);
    await press("ArrowRight");
    expect(document.activeElement).toBe(items[4]);
    items[0].focus();
    await press("ArrowLeft");
    expect(document.activeElement).toBe(items[0]);
    await press("ArrowUp");
    expect(document.activeElement).toBe(search);
});

test.tags("desktop");
test("app grid preserves search caret, Reset tab order and Escape focus", async () => {
    defineMenus([1, 2, 3].map((id) => ({ id, name: `Grid app ${id}` })), { mode: "replace" });
    patchWithCleanup(odoo, { debug: "1" });
    patchWithCleanup(user, { isInternalUser: true });
    const { navbar, panel, search, items } = await openGridSwitcher();
    const reset = panel.querySelector(".o_qing_app_order_help button");
    expect(Boolean(reset)).toBe(true);
    search.focus();
    search.value = "Grid";
    const right = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
    search.dispatchEvent(right);
    expect(right.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(search);
    await press("Tab");
    expect(document.activeElement).toBe(reset);
    expect(navbar.qingLauncherState.isOpen).toBe(true);
    await press("Tab");
    expect(document.activeElement).toBe(items[0]);
    expect(navbar.qingLauncherState.isOpen).toBe(true);
    // Escape closes the foreground switcher and returns to its entry.
    await press("Escape");
    await animationFrame();
    expect(".o_qing_app_switcher").toHaveCount(0);
    expect(document.activeElement).toBe(navbar.root().querySelector(".o_qing_top_apps__more"));
});

test.tags("desktop");
test("app grid consumes Escape before background app shortcuts", async () => {
    defineMenus([{ id: 1, name: "Grid app" }], { mode: "replace" });
    const { navbar, search } = await openGridSwitcher();
    const handled = [];
    const capture = (event) => {
        if (event.key === "Escape") handled.push("capture");
    };
    const bubble = (event) => {
        if (event.key === "Escape") handled.push("bubble");
    };
    window.addEventListener("keydown", capture, true);
    window.addEventListener("keydown", bubble);
    try {
        search.focus();
        const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
        search.dispatchEvent(event);
        await animationFrame();
        expect(event.defaultPrevented).toBe(true);
        expect(handled).toEqual([]);
        expect(navbar.qingLauncherState.isOpen).toBe(false);
        const more = navbar.root().querySelector(".o_qing_top_apps__more");
        expect(document.activeElement).toBe(more);
        // Background shortcuts remain available once the switcher is closed.
        more.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        expect(handled).toEqual(["capture", "bubble"]);
    } finally {
        window.removeEventListener("keydown", capture, true);
        window.removeEventListener("keydown", bubble);
    }
});

test.tags("desktop");
test("a native dialog over the app grid owns Escape and restores grid focus", async () => {
    defineMenus([{ id: 1, name: "Grid app" }], { mode: "replace" });
    assignDialogTestEnv();
    const { navbar, search } = await openGridSwitcher();
    search.focus();
    getService("dialog").add(ConfirmationDialog, {
        body: "Keep the current changes?",
        title: "Confirmation",
    });
    await animationFrame();
    expect(".o_dialog").toHaveCount(1);
    expect(navbar.ui.activeElement.contains(search)).toBe(false);
    // The native dialog still owns Escape if its autofocus has not settled yet.
    search.focus();
    await press("Escape");
    await animationFrame();
    expect(".o_dialog").toHaveCount(0);
    expect(".o_qing_app_switcher").toHaveCount(1);
    expect(navbar.qingLauncherState.isOpen).toBe(true);
    expect(document.activeElement).toBe(search);
    await press("Escape");
    await animationFrame();
    expect(".o_qing_app_switcher").toHaveCount(0);
    expect(document.activeElement).toBe(navbar.root().querySelector(".o_qing_top_apps__more"));
});

test.tags("desktop");
test("app grid search handles one or no results without losing focus", async () => {
    defineMenus([1, 2].map((id) => ({ id, name: `Grid app ${id}` })), { mode: "replace" });
    const { navbar, panel, search } = await openGridSwitcher();
    navbar.qingLauncherState.query = "Grid app 1";
    await animationFrame();
    search.focus();
    const only = panel.querySelector(".o_qing_app_switcher__item");
    expect(".o_qing_app_switcher__item").toHaveCount(1);
    await press("ArrowDown");
    expect(document.activeElement).toBe(only);
    await press("ArrowDown");
    expect(document.activeElement).toBe(search);
    navbar.qingLauncherState.query = "Missing app";
    await animationFrame();
    await press("ArrowDown");
    await press("ArrowUp");
    expect(".o_qing_app_switcher__item").toHaveCount(0);
    expect(document.activeElement).toBe(search);
    expect(navbar.qingLauncherState.isOpen).toBe(true);
});

test.tags("desktop");
test("app grid respects RTL arrows and keeps modifier ordering available", async () => {
    defineMenus([1, 2, 3].map((id) => ({ id, name: `Grid app ${id}` })), { mode: "replace" });
    patchWithCleanup(odoo, { debug: "1" });
    patchWithCleanup(user, { isInternalUser: true });
    const { navbar, panel, items } = await openGridSwitcher();
    panel.style.direction = "rtl";
    items[1].focus();
    await press("ArrowRight");
    expect(document.activeElement).toBe(items[0]);
    await press("ArrowLeft");
    expect(document.activeElement).toBe(items[1]);
    const orders = [];
    patchWithCleanup(navbar, { saveQingAppOrder(ids) { orders.push(ids); } });
    await press(["alt", "shift", "ArrowLeft"]);
    expect(orders).toEqual([[1, 3, 2]]);
    expect(document.activeElement).toBe(items[1]);
});

test.tags("desktop");
test("app grid drag placement uses the tile's horizontal half in both directions", async () => {
    defineMenus([1, 2, 3, 4].map((id) => ({ id, name: `Grid app ${id}` })), { mode: "replace" });
    patchWithCleanup(odoo, { debug: "1" });
    patchWithCleanup(user, { isInternalUser: true });
    const { navbar, panel, items } = await openGridSwitcher();
    const target = items[2];
    const orders = [];
    patchWithCleanup(navbar, { saveQingAppOrder(ids) { orders.push(ids); } });
    for (const direction of ["ltr", "rtl"]) {
        panel.style.direction = direction;
        const rect = target.getBoundingClientRect();
        navbar.qingAppOrder.dragging = 1;
        await navbar.dropQingApp({
            currentTarget: target,
            clientX: direction === "rtl" ? rect.left + 1 : rect.right - 1,
            clientY: rect.top + 1,
            preventDefault() {},
        }, { id: 3 });
        expect(navbar.qingAppOrder.dragging).toBe(null);
    }
    expect(orders).toEqual([[2, 3, 1, 4], [2, 3, 1, 4]]);
});

test.tags("desktop");
test("app grid popup uses its measured width and clears the entire navbar", async () => {
    defineMenus([{ id: 1, name: "Grid app" }], { mode: "replace" });
    const { navbar, panel } = await openGridSwitcher();
    panel.style.cssText = "position:fixed;width:400px;padding:0;border:0;box-sizing:border-box";
    const more = navbar.root().querySelector(".o_qing_top_apps__more");
    patchWithCleanup(more, {
        getBoundingClientRect() {
            return { left: window.innerWidth - 60, bottom: 40, width: 50 };
        },
    });
    patchWithCleanup(navbar.root(), {
        getBoundingClientRect() {
            return { bottom: 80 };
        },
    });
    navbar.measureQingTopNav();
    expect(navbar.qingTopNav.left).toBe(window.innerWidth - panel.getBoundingClientRect().width - 14);
    expect(navbar.qingTopNav.top).toBe(88);
    expect(panel.style.getPropertyValue("--o-qing-app-switcher-max-height")).toBe(
        `${Math.max(0, window.innerHeight - 102)}px`
    );
});

test.tags("desktop");
test("height-only window resize keeps the app grid within the viewport", async () => {
    defineMenus([{ id: 1, name: "Grid app" }], { mode: "replace" });
    const { navbar, panel } = await openGridSwitcher();
    navbar.measureQingTopNav();
    const before = panel.style.getPropertyValue("--o-qing-app-switcher-max-height");
    const width = window.innerWidth;
    await resize({ width, height: window.innerHeight - 160 });
    // Resize observers and navbar patches can schedule the fit on the next frame.
    await animationFrame();
    await animationFrame();
    expect(window.innerWidth).toBe(width);
    expect(panel.style.getPropertyValue("--o-qing-app-switcher-max-height")).not.toBe(before);
    expect(panel.style.getPropertyValue("--o-qing-app-switcher-max-height")).toBe(
        `${Math.max(0, window.innerHeight - navbar.qingTopNav.top - 14)}px`
    );
});

test.tags("desktop");
test("an open app grid stays constrained after crossing the mobile breakpoint", async () => {
    defineMenus([{ id: 1, name: "Grid app" }], { mode: "replace" });
    const { navbar, panel } = await openGridSwitcher();
    await resize({ width: 390, height: 600 });
    await animationFrame();
    await animationFrame();
    expect(".o_qing_top_apps").toHaveCount(0);
    expect(".o_qing_app_switcher").toHaveCount(1);
    expect(navbar.qingTopNav.left).toBe(14);
    expect(navbar.qingTopNav.top).toBe(navbar.root().getBoundingClientRect().bottom + 8);
    expect(panel.style.getPropertyValue("--o-qing-app-switcher-max-height")).toBe(
        `${Math.max(0, window.innerHeight - navbar.qingTopNav.top - 14)}px`
    );
    // Native navbar replacement can drop focus while the switcher stays open.
    document.activeElement?.blur();
    expect(panel.contains(document.activeElement)).toBe(false);
    await press("Escape");
    await animationFrame();
    expect(".o_qing_app_switcher").toHaveCount(0);
});

// Exercise visibility through the actual navbar and its systray plugin scope.
for (const [name, internal, system, admin, debug, visible] of [
    ["ordinary internal user", true, false, false, "", 0],
    ["user access administrator", true, false, true, "", 0],
    ["system administrator", true, true, true, "", 1],
    ["portal user", false, false, false, "", 0],
    ["active developer mode", true, true, true, "1", 0],
    ["active assets mode", true, true, true, "assets", 0],
]) {
    test(`developer shortcut: ${name}`, async () => {
        patchWithCleanup(user, { isInternalUser: internal, isAdmin: admin, isSystem: system });
        patchWithCleanup(odoo, { debug });
        await mountWithCleanup(NavBar);
        expect(".o_menu_systray .o_qing_debug_shortcut").toHaveCount(visible);
        if (visible) {
            expect(".o_qing_debug_shortcut svg.o_qing_debug_icon").toHaveCount(1);
            expect(".o_qing_debug_shortcut img").toHaveCount(0);
            expect(".o_qing_debug_shortcut .o_qing_debug_wings").toHaveCount(0);
            patchWithCleanup(router, {
                pushState(state, options) {
                    expect(state).toEqual({ debug: "1" });
                    expect(options).toEqual({ reload: true });
                    expect.step("activate");
                },
            });
            await contains(".o_qing_debug_shortcut").click();
            expect.verifySteps(["activate"]);
        }
    });
}


for (const [name, internal, system, admin, commands] of [
    ["ordinary internal user", true, false, false, 0],
    ["user access administrator", true, false, true, 0],
    ["portal user", false, false, false, 0],
    ["system administrator", true, true, true, 2],
]) {
    test(`developer command palette: ${name}`, async () => {
        patchWithCleanup(user, { isInternalUser: internal, isAdmin: admin, isSystem: system });
        patchWithCleanup(odoo, { debug: "" });
        await makeTestApp();
        await mountWithCleanup(NavBar);
        getService("command").openPalette({
            providers: [registry.category("command_provider").get("debug")],
            searchValue: "debug",
        });
        await animationFrame();
        expect(".o_command").toHaveCount(commands);
    });
}

class QingDebugMenuPreview extends Component {
    static template = xml`<DebugMenu/>`;
    static components = { DebugMenu };
    setup() {
        useOwnDebugContext({ categories: ["default"] });
    }
}

test("active beetle opens its wings and keeps the native developer dropdown", async () => {
    patchWithCleanup(odoo, { debug: "1" });
    await mountWithCleanup(QingDebugMenuPreview);
    expect(".o_debug_manager .o_qing_debug_button.is-active").toHaveCount(1);
    expect(".o_debug_manager .o_qing_debug_button svg.o_qing_debug_icon").toHaveCount(1);
    expect(".o_debug_manager .o_qing_debug_button img").toHaveCount(0);
    expect(".o_debug_manager .o_qing_debug_wings path").toHaveCount(2);
    expect(".o_debug_manager [data-icon='bug_report']").toHaveCount(0);
    await contains(".o_debug_manager button").click();
    expect(".o-dropdown--menu").toHaveText(/Leave Debug Mode/);
});

// Use the visible control both ways: changing preferences alone misses a lost trigger.
test.tags("desktop");
test("sidebar can expand again through its control after collapsing", async () => {
    onRpc("res.users.settings", "set_qing_theme_preferences", ({ args }) => ({ base: { ...window.qingWebUi.getPreferences(), ...args[0].base } }));
    const preferences = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ sidebarState: "expanded" });
        const navbar = await mountWithCleanup(NavBar);
        navbar.qingActionChanged();
        expect('.o_qing_sidebar__collapse[aria-expanded="true"]').toHaveCount(1);
        await contains(".o_qing_sidebar__collapse").click();
        await animationFrame();
        expect('.o_qing_sidebar__collapse[aria-expanded="false"]').toHaveCount(1);
        expect('.o_qing_sidebar__collapse[aria-label="Expand sidebar"] svg').toHaveCount(1);
        await contains(".o_qing_sidebar__collapse").click();
        await animationFrame();
        expect(window.qingWebUi.getPreferences().sidebarState).toBe("expanded");
        expect('.o_qing_sidebar__collapse[aria-expanded="true"]').toHaveCount(1);
    } finally {
        window.qingWebUi.setPreferences(preferences);
    }
});

test("changing preferences keeps an open form usable", async () => {
    const preferences = window.qingWebUi.getPreferences();
    await mountView({
        type: "form",
        resModel: "qing.search.record",
        resId: 1,
        arch: `<form><sheet><field name="name"/></sheet></form>`,
    });
    try {
        window.qingWebUi.setPreferences({ chatterPosition: "bottom" });
        await animationFrame();
        expect(".o_form_view").toHaveCount(1);
    } finally {
        window.qingWebUi.setPreferences(preferences);
    }
});


test("list action alignment validates preferences and resets to the existing right layout", () => {
    const original = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ listActionsAlignment: "left" });
        expect(window.qingWebUi.getPreferences().listActionsAlignment).toBe("left");
        expect(document.body.dataset.qingListActionsAlignment).toBe("left");
        window.qingWebUi.setPreferences({ listActionsAlignment: "invalid" });
        expect(window.qingWebUi.getPreferences().listActionsAlignment).toBe("right");
        window.qingWebUi.setPreferences({ listActionsAlignment: "left" });
        expect(window.qingWebUi.resetPreferences().listActionsAlignment).toBe("right");
    } finally {
        window.qingWebUi.setPreferences(original);
    }
});


test("personal app order handles removed, new and overflow apps without mutating menus", () => {
    const apps = [1, 2, 3, 4].map((id) => ({ id }));
    expect(orderQingApps(apps, [3, 99, 1]).map((app) => app.id)).toEqual([3, 1, 2, 4]);
    expect(apps.map((app) => app.id)).toEqual([1, 2, 3, 4]);
    expect(moveQingApp([1, 2, 3, 4], 4, 1)).toEqual([4, 1, 2, 3]);
    expect(moveQingApp([1, 2, 3, 4], 1, 4, true)).toEqual([2, 3, 4, 1]);
    expect(moveQingApp([1, 2], 99, 1)).toEqual([1, 2]);
});


test.tags("mobile");
test("mobile record title scrolls only when overflowing and resets after resize or rename", async () => {
    mockMatchMedia({ "prefers-reduced-motion": "no-preference" });
    const returned = [];
    class TitleFixture extends Component {
        static components = { Breadcrumbs };
        static template = xml`
            <div class="o_qing_web_ui">
                <nav class="o_main_navbar" t-att-style="'width:' + this.state.width + 'px; min-width:0'">
                    <div class="o_navbar_breadcrumbs d-flex min-w-0">
                        <Breadcrumbs breadcrumbs="this.breadcrumbs"/>
                    </div>
                </nav>
                <button class="expand-title" t-on-click="() => this.state.width = 600">Expand</button>
                <button class="shrink-title" t-on-click="() => this.state.width = 140">Shrink</button>
                <button class="rename-title" t-on-click="() => this.state.name = 'Acme'">Rename</button>
            </div>`;
        state = proxy({ width: 140, name: "Shanghai Boyuan Trading Company Limited", root: false, collection: false });
        get breadcrumbs() {
            if (this.state.root) return [{ jsId: 1, name: "Contacts and Companies Directory" }];
            return [
                { jsId: 1, name: "Contacts", onSelected: () => returned.push("contacts") },
                { jsId: 2, name: this.state.name, isFormView: !this.state.collection },
            ];
        }
    }
    const fixture = await mountWithCleanup(TitleFixture, { componentEnv: { config: { viewType: "form" } } });
    await animationFrame();
    expect(".o_last_breadcrumb_item").toHaveClass("o_qing_breadcrumb_title_overflow");
    expect(".o_back_button .o_qing_back_icon").toHaveCount(1);
    const label = document.querySelector(".o_qing_breadcrumb_label");
    expect(getComputedStyle(label).visibility).toBe("visible");
    const animation = label.getAnimations()[0];
    expect(animation.playState).toBe("running");
    const frames = animation.effect.getKeyframes();
    expect(frames[0].transform).toBe("translateX(0px)");
    expect(frames[2].transform).not.toBe("translateX(0px)");
    expect(frames[3].transform).toBe(frames[2].transform);
    expect(frames.at(-1).transform).toBe("translateX(0px)");
    fixture.state.width = 140;
    await animationFrame();
    expect(label.getAnimations()[0]).toBe(animation);
    await contains(".expand-title").click();
    await animationFrame();
    expect(".o_last_breadcrumb_item").not.toHaveClass("o_qing_breadcrumb_title_overflow");
    expect(label.getAnimations()).toHaveLength(0);
    await contains(".shrink-title").click();
    await animationFrame();
    expect(".o_last_breadcrumb_item").toHaveClass("o_qing_breadcrumb_title_overflow");
    const preferences = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ reducedMotion: true });
        await animationFrame();
        expect(".o_last_breadcrumb_item").toHaveClass("o_qing_breadcrumb_title_manual");
        expect(label.getAnimations()).toHaveLength(0);
        expect(getComputedStyle(document.querySelector(".o_qing_breadcrumb_viewport")).overflowX).toBe("auto");
    } finally {
        window.qingWebUi.setPreferences(preferences);
    }
    await animationFrame();
    expect(label.getAnimations()).toHaveLength(1);
    mockMatchMedia({ "prefers-reduced-motion": "reduce" });
    await animationFrame();
    expect(".o_last_breadcrumb_item").toHaveClass("o_qing_breadcrumb_title_manual");
    expect(label.getAnimations()).toHaveLength(0);
    mockMatchMedia({ "prefers-reduced-motion": "no-preference" });
    await animationFrame();
    expect(label.getAnimations()).toHaveLength(1);
    await contains(".rename-title").click();
    await animationFrame();
    expect(".o_last_breadcrumb_item").not.toHaveClass("o_qing_breadcrumb_title_overflow");
    await contains(".o_back_button").click();
    expect(returned).toEqual(["contacts"]);
    expect(label.getAnimations()).toHaveLength(0);
    fixture.state.name = "Contacts and Companies Directory";
    fixture.state.collection = true;
    await animationFrame();
    await animationFrame();
    expect(".o_last_breadcrumb_item").not.toHaveClass("o_qing_breadcrumb_title_record");
    expect(label.getAnimations()).toHaveLength(0);
    expect(getComputedStyle(document.querySelector(".o_qing_breadcrumb_viewport")).textOverflow).toBe("ellipsis");
    expect(getComputedStyle(label).display).toBe("inline");
    fixture.state.root = true;
    await animationFrame();
    expect(".o_back_button").toHaveCount(0);
    expect(getComputedStyle(document.querySelector(".o_last_breadcrumb_item > span")).textOverflow).toBe("ellipsis");
});

// Mount the native components to catch inheritance drift and preserve actions.
for (const debug of ["", "1"]) {
    test.tags("desktop");
    test(`compact user menu retains identity and actions (debug=${debug})`, async () => {
        serverState.partnerName = "A long administrator name for the compact menu";
        serverState.debug = debug;
        clearRegistry(registry.category("user_menuitems"));
        patchWithCleanup(session, { db: "tenant_qingerp_200_long_database_name" });
        registry.category("user_menuitems").add("qing_compact_menu_test", () => ({
            type: "item",
            id: "qing_compact_menu_test",
            description: "Open preferences",
            callback: () => expect.step("selected"),
        }));
        const menu = await mountWithCleanup(UserMenu);
        const userName = menu.userName;
        expect(".o_user_menu .oe_topbar_name").toHaveCount(0);
        expect(".o_user_menu").toHaveAttribute("aria-label", userName);
        await contains(".o_user_menu").click();
        expect(".o_qing_user_menu_dropdown .o_qing_systray_identity__name").toHaveText(userName);
        expect(".o_qing_systray_identity__database").toHaveCount(debug ? 1 : 0);
        if (debug) {
            expect(".o_qing_systray_identity__database").toHaveText(session.db);
        }
        await contains('[data-menu="qing_compact_menu_test"]').click();
        expect.verifySteps(["selected"]);
        expect(".o_qing_user_menu_dropdown").toHaveCount(0);
    });
}

test.tags("desktop");
test("compact company menu retains native search and selection reset", async () => {
    serverState.companies = [
        { id: 3, name: "Primary company with a long name", sequence: 1, parent_id: false, child_ids: [] },
        { id: 2, name: "Second company", sequence: 2, parent_id: false, child_ids: [] },
    ];
    cookie.set("cids", "3");
    patchWithCleanup(SwitchCompanyMenu, { toggleDelay: 0 });
    await mountWithCleanup(SwitchCompanyMenu);
    expect(".o_switch_company_menu .oe_topbar_name").toHaveCount(0);
    expect(".o_switch_company_menu svg").toHaveCount(1);
    expect(".o_switch_company_menu").toHaveAttribute("aria-label", user.activeCompany.name);
    await contains(".o_switch_company_menu").click();
    expect(".o_qing_systray_identity__name").toHaveText(user.activeCompany.name);
    await contains('.o_switch_company_menu_dropdown input[role="searchbox"]').edit("Second");
    expect(".o_switch_company_item").toHaveCount(1);
    await contains('.o_switch_company_menu_dropdown input[role="searchbox"]').edit("");
    await contains('[data-company-id="2"] input').click();
    expect(".o_switch_company_menu_buttons").toHaveCount(1);
    await contains(".o_switch_company_menu_buttons .btn-secondary").click();
    expect(".o_switch_company_menu_buttons").toHaveCount(0);
    expect(user.activeCompanies.map((company) => company.id)).toEqual([3]);
});


test("theme cache follows the native user after session uid is consumed", () => {
    patchWithCleanup(session, { db: "qing_theme_cache_test" });
    patchWithCleanup(user, { userId: 101 });
    window.qingWebUi.setPreferences({ themeMode: "dark", accentColor: "blue" });
    user.userId = 102;
    expect(window.qingWebUi.getPreferences().themeMode).toBe("system");
    window.qingWebUi.setPreferences({ themeMode: "light" });
    user.userId = 101;
    expect(window.qingWebUi.getPreferences().themeMode).toBe("dark");
    expect(window.qingWebUi.getPreferences().accentColor).toBe("blue");
});
