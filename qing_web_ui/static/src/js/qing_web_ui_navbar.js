/** @odoo-module **/

import { Breadcrumbs } from "@web/search/breadcrumbs/breadcrumbs";
import { NavBar } from "@web/webclient/navbar/navbar";
import { WebClient } from "@web/webclient/webclient";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { imageUrl } from "@web/core/utils/urls";
import { onMounted, onPatched, onWillUnmount, proxy, signal, useListener } from "@odoo/owl";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { render } from "@web/owl2/utils";
import { menuIconHref } from "./qing_menu_icons";

// A long rail is hard to scan; the personal app order decides which stay
// visible and the rest remain one click away in the searchable launcher.
export const QING_TOP_APP_LIMIT = 10;

// Include every server-authorized app in the searchable overflow menu.
export function fitQingApps(apps, widths, available, moreWidth, activeId, limit = Infinity) {
    const gap = 4;
    const total = apps.reduce((sum, app) => sum + (widths.get(app.id) || 0) + gap, 0);
    if (apps.length <= limit && total + moreWidth + gap <= available) return apps.map((app) => app.id);
    const active = apps.find((app) => app.id === activeId);
    let remaining = Math.max(0, available - moreWidth - gap);
    const visible = new Set();
    if (active && (widths.get(active.id) || 0) + gap <= remaining) {
        visible.add(active.id);
        remaining -= (widths.get(active.id) || 0) + gap;
    }
    for (const app of apps) {
        if (visible.size >= limit) break;
        if (visible.has(app.id)) continue;
        const width = (widths.get(app.id) || 0) + gap;
        if (width > remaining) break;
        visible.add(app.id);
        remaining -= width;
    }
    return apps.filter((app) => visible.has(app.id)).map((app) => app.id);
}

export function orderQingApps(apps, order) {
    const ranks = new Map(order.map((id, index) => [id, index]));
    return [...apps].sort((a, b) => (ranks.get(a.id) ?? Infinity) - (ranks.get(b.id) ?? Infinity));
}

export function moveQingApp(order, source, target, after = false) {
    if (source === target || !order.includes(source) || !order.includes(target)) return order;
    const result = order.filter((id) => id !== source);
    result.splice(result.indexOf(target) + Number(after), 0, source);
    return result;
}

export function buildQingSidebarRows(
    menus,
    expanded,
    activeActionId,
    railCollapsed = false,
    groupsDefault = "active"
) {
    const rows = [];
    const hasActive = (menu) =>
        (activeActionId && String(menu.actionID) === String(activeActionId)) ||
        (menu.childrenTree || []).some(hasActive);
    const visit = (items, depth = 0) => {
        for (const menu of items) {
            const children = menu.childrenTree || [];
            const isGroup = children.length > 0;
            const defaultOpen = groupsDefault === "expanded" ||
                (groupsDefault === "active" && hasActive(menu));
            const isOpen = isGroup && (railCollapsed ||
                (expanded[menu.id] !== undefined ? expanded[menu.id] : defaultOpen));
            rows.push({ menu, depth, isGroup, isOpen });
            if (isOpen) visit(children, depth + 1);
        }
    };
    visit(menus);
    return rows;
}

// Cards and the icon rail show one flat list per menu group instead of an
// indented tree: each group keeps only its direct leaves, nested groups become
// their own sections (labelled with their parent), and empty groups vanish.
export function buildQingSidebarSections(app, menus) {
    const root = { key: "root", menu: app, title: "", parent: "", path: [], items: [] };
    const sections = [root];
    const visit = (group, path) => {
        const section = {
            key: `menu-${group.id}`,
            menu: group,
            title: group.name,
            parent: path.at(-1) || "",
            path,
            items: [],
        };
        sections.push(section);
        for (const child of group.childrenTree || []) {
            if (child.childrenTree?.length) {
                visit(child, [...path, group.name]);
            } else {
                section.items.push(child);
            }
        }
    };
    for (const menu of menus || []) {
        if (menu.childrenTree?.length) {
            visit(menu, []);
        } else {
            root.items.push(menu);
        }
    }
    return sections.filter((section) => section.items.length);
}

export function findQingActiveSection(sections, actionId) {
    if (!actionId) return null;
    return sections.find((section) =>
        section.items.some((menu) => String(menu.actionID) === String(actionId))
    ) || null;
}

// Measure a fixed title viewport; animate only the overflowing label so the
// native Back button and the other navbar controls remain stationary.
patch(Breadcrumbs.prototype, {
    setup() {
        super.setup(...arguments);
        this.qingBreadcrumbTitle = signal.ref();
        this.qingScheduleTitleFit = () => {
            cancelAnimationFrame(this.qingTitleFrame);
            this.qingTitleFrame = requestAnimationFrame(() => this.fitQingBreadcrumbTitle());
        };
        this.qingTitleObserver = new ResizeObserver(this.qingScheduleTitleFit);
        this.qingTitleMotionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
        useListener(this.qingTitleMotionPreference, "change", this.qingScheduleTitleFit);
        onMounted(this.qingScheduleTitleFit);
        onPatched(this.qingScheduleTitleFit);
        useListener(window, "resize", this.qingScheduleTitleFit);
        useListener(window, "qing-web-ui:preferences-changed", this.qingScheduleTitleFit);
        if (document.fonts) {
            useListener(document.fonts, "loadingdone", this.qingScheduleTitleFit);
        }
        onWillUnmount(() => {
            cancelAnimationFrame(this.qingTitleFrame);
            this.qingTitleObserver.disconnect();
            this.qingTitleAnimation?.cancel();
        });
    },

    fitQingBreadcrumbTitle() {
        const title = this.qingBreadcrumbTitle();
        if (title !== this.qingObservedTitle) {
            this.qingTitleObserver.disconnect();
            if (title) this.qingTitleObserver.observe(title);
            this.qingObservedTitle = title;
        }
        const viewport = title?.querySelector(".o_qing_breadcrumb_viewport");
        const label = viewport?.querySelector(".o_qing_breadcrumb_label");
        const isMobileRecord = this.uiService.isSmall && this.props.breadcrumbs.length > 1 &&
            this.props.breadcrumbs.at(-1)?.isFormView &&
            title?.closest(".o_qing_web_ui .o_main_navbar");
        title?.classList.toggle("o_qing_breadcrumb_title_record", Boolean(isMobileRecord));
        const distance = isMobileRecord && label ? Math.max(0, label.scrollWidth - viewport.clientWidth) : 0;
        const overflow = distance > 1;
        const reducedMotion = this.qingTitleMotionPreference.matches ||
            window.qingWebUi?.getPreferences().reducedMotion;
        title?.classList.toggle("o_qing_breadcrumb_title_overflow", overflow);
        title?.classList.toggle("o_qing_breadcrumb_title_manual", Boolean(overflow && reducedMotion));

        const direction = viewport && getComputedStyle(viewport).direction === "rtl" ? 1 : -1;
        const signature = `${label?.textContent}|${distance}|${direction}|${Boolean(reducedMotion)}`;
        // Unrelated Owl patches must not restart the reading cycle.
        if (label === this.qingAnimatedLabel && signature === this.qingTitleSignature) return;
        this.qingTitleAnimation?.cancel();
        this.qingTitleAnimation = null;
        this.qingAnimatedLabel = label;
        this.qingTitleSignature = signature;
        if (viewport) viewport.scrollLeft = 0;
        if (!overflow || reducedMotion) return;

        const pause = 1400;
        const travel = Math.max(1200, distance / 18 * 1000);
        const duration = 2 * (pause + travel);
        const end = `translateX(${direction * distance}px)`;
        this.qingTitleAnimation = label.animate([
            { transform: "translateX(0)", offset: 0 },
            { transform: "translateX(0)", offset: pause / duration },
            { transform: end, offset: (pause + travel) / duration },
            { transform: end, offset: (2 * pause + travel) / duration },
            { transform: "translateX(0)", offset: 1 },
        ], { duration, iterations: Infinity, easing: "linear" });
    },
});

patch(WebClient.prototype, {
    _loadDefaultApp() {
        // Use the same user order as the top navigation when no action is in the URL.
        const firstApp = orderQingApps(
            this.menuService.getApps(),
            session.qing_app_menu_order || []
        )[0];
        if (firstApp) {
            return this.menuService.selectMenu(firstApp);
        }
    },
});

patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.qingLauncherState = proxy({
            isOpen: false,
            query: "",
        });
        this.qingMenuOrm = useService("orm");
        this.qingMenuNotification = useService("notification");
        this.qingAppOrder = proxy({ ids: session.qing_app_menu_order || [], saving: false, dragging: null, over: null });
        this.qingTopNav = proxy({ ids: [], left: 14, top: 64 });
        this.qingScheduleTopNav = () => {
            cancelAnimationFrame(this.qingNavFrame);
            this.qingNavFrame = requestAnimationFrame(() => this.measureQingTopNav());
        };
        this.qingNavObserver = new ResizeObserver(this.qingScheduleTopNav);
        onMounted(this.qingScheduleTopNav);
        onMounted(() => {
            const banner = document.getElementById("oe_neutralize_banner");
            if (!banner) return;
            // The native banner stays in normal flow; move the fixed sidebar
            // below it, including when translated text wraps after a resize.
            const syncSidebarTop = () => {
                const root = this.root();
                if (root) root.style.setProperty("--o-qing-sidebar-top", `${root.getBoundingClientRect().top}px`);
            };
            this.qingBannerObserver = new ResizeObserver(syncSidebarTop);
            this.qingBannerObserver.observe(banner);
            syncSidebarTop();
        });
        this.qingViewportState = proxy({ isNarrow: window.innerWidth <= 768 });
        this.qingMenuHint = proxy({ text: "", top: 0 });
        this.qingSidebarGroups = proxy({});
        // The rail follows the current page until the user picks another group.
        this.qingRailState = proxy({ key: null, query: "" });
        this.qingSidebarGroupsDefault = window.qingWebUi?.getPreferences().sidebarGroupsDefault || "collapsed";
        this.qingSidebarActionInitialized = false;
        this.qingLastSidebarActionId = this.actionService.currentController?.action?.id || null;
        this.qingLastActiveElement = null;
        this.qingResize = () => {
            this.qingViewportState.isNarrow = window.innerWidth <= 768;
            // A height-only resize does not resize the observed application rail.
            this.qingScheduleTopNav();
        };
        this.qingPreferencesChanged = () => {
            const groupsDefault = window.qingWebUi?.getPreferences().sidebarGroupsDefault || "collapsed";
            if (groupsDefault !== this.qingSidebarGroupsDefault) {
                for (const menuId of Object.keys(this.qingSidebarGroups)) {
                    delete this.qingSidebarGroups[menuId];
                }
                this.qingSidebarGroupsDefault = groupsDefault;
            }
            render(this);
        };
        this.qingActionChanged = () => {
            const actionId = this.actionService.currentController?.action?.id;
            if (actionId && actionId !== this.qingLastSidebarActionId) {
                this.qingLastSidebarActionId = actionId;
                this.qingRailState.key = null;
                this.qingRailState.query = "";
                if (this.qingSidebarActionInitialized) {
                    this.openQingActiveSidebarGroups(actionId);
                }
                this.qingSidebarActionInitialized = true;
            }
            render(this);
        };
        this.env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", this.qingActionChanged);
        window.addEventListener("resize", this.qingResize, { passive: true });
        window.addEventListener("qing-web-ui:preferences-changed", this.qingPreferencesChanged);
        this.qingLauncherKeydown = (event) => {
            if (event.key !== "Escape" || !this.qingSwitcherOwnsEscape()) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            this.closeQingLauncher();
        };
        window.addEventListener("keydown", this.qingLauncherKeydown, true);
        onPatched(() => {
            this.qingScheduleTopNav();
            if (this.qingLauncherState.isOpen && this.qingSwitcherNeedsFocus) {
                document.querySelector(".o_qing_app_switcher__search input")?.focus();
                this.qingSwitcherNeedsFocus = false;
            }
        });
        onWillUnmount(() => {
            cancelAnimationFrame(this.qingNavFrame);
            this.qingNavObserver.disconnect();
            this.qingBannerObserver?.disconnect();
            this.env.bus.removeEventListener("ACTION_MANAGER:UI-UPDATED", this.qingActionChanged);
            window.removeEventListener("keydown", this.qingLauncherKeydown, true);
            window.removeEventListener("resize", this.qingResize);
            window.removeEventListener("qing-web-ui:preferences-changed", this.qingPreferencesChanged);
        });
    },

    get qingCanReorderApps() {
        return Boolean(odoo.debug) && user.isInternalUser && !this.qingAppOrder.saving;
    },

    get qingOrderedApps() {
        return orderQingApps(this.menuService.getApps(), this.qingAppOrder.ids);
    },

    startQingAppDrag(event, app) {
        if (!this.qingCanReorderApps) { event.preventDefault(); return; }
        this.qingAppOrder.dragging = app.id;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(app.id));
    },

    overQingAppDrag(event, app) {
        if (!this.qingCanReorderApps || this.qingAppOrder.dragging === null) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        this.qingAppOrder.over = app.id;
    },

    endQingAppDrag() {
        this.qingAppOrder.dragging = null;
        this.qingAppOrder.over = null;
    },

    async dropQingApp(event, app) {
        if (!this.qingCanReorderApps || this.qingAppOrder.dragging === null) return;
        event.preventDefault();
        const source = this.qingAppOrder.dragging;
        const rect = event.currentTarget.getBoundingClientRect();
        const rtl = getComputedStyle(event.currentTarget).direction === "rtl";
        const after = rtl ? event.clientX < rect.x + rect.width / 2 : event.clientX > rect.x + rect.width / 2;
        const order = this.qingOrderedApps.map((item) => item.id);
        this.endQingAppDrag();
        const next = moveQingApp(order, source, app.id, after);
        if (next.join() !== order.join()) await this.saveQingAppOrder(next);
    },

    openQingDragOverflow(event) {
        if (!this.qingCanReorderApps || this.qingAppOrder.dragging === null) return;
        event.preventDefault();
        this.qingLauncherState.isOpen = true;
        this.qingLauncherState.query = "";
    },

    async reorderQingAppWithKeyboard(event, app) {
        if (!this.qingCanReorderApps || !event.altKey || !event.shiftKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const order = this.qingOrderedApps.map((item) => item.id);
        const index = order.indexOf(app.id);
        const rtl = getComputedStyle(event.currentTarget).direction === "rtl";
        const after = (event.key === "ArrowRight") !== rtl;
        const target = order[index + (after ? 1 : -1)];
        if (target !== undefined) await this.saveQingAppOrder(moveQingApp(order, app.id, target, after));
    },

    async saveQingAppOrder(ids) {
        if (!this.qingCanReorderApps) return;
        const previous = [...this.qingAppOrder.ids];
        this.qingAppOrder.saving = true;
        this.qingAppOrder.ids = ids;
        try {
            const saved = await this.qingMenuOrm.call("res.users.settings", "set_qing_app_menu_order", [ids]);
            this.qingAppOrder.ids = saved;
            session.qing_app_menu_order = saved;
        } catch {
            this.qingAppOrder.ids = previous;
            this.qingMenuNotification.add(_t("Could not save menu order. Please try again."), { type: "warning" });
        } finally {
            this.qingAppOrder.saving = false;
        }
    },

    get qingTopApps() {
        return this.qingOrderedApps.filter((app) => this.qingTopNav.ids.includes(app.id));
    },

    measureQingTopNav() {
        const root = this.root();
        if (!root) return;
        const rail = root.querySelector(".o_qing_top_apps");
        if (this.qingObservedRail !== rail) {
            this.qingNavObserver.disconnect();
            if (rail) this.qingNavObserver.observe(rail);
            this.qingObservedRail = rail;
        }
        const more = rail?.querySelector(".o_qing_top_apps__more");
        if (rail) {
            const widths = new Map([...rail.querySelectorAll("[data-measure-app]")].map((el) =>
                [Number(el.dataset.measureApp), el.getBoundingClientRect().width]));
            const ids = fitQingApps(this.qingOrderedApps, widths, rail.clientWidth,
                more?.getBoundingClientRect().width || 72, this.currentApp?.id, QING_TOP_APP_LIMIT);
            if (ids.join(",") !== this.qingTopNav.ids.join(",")) this.qingTopNav.ids = ids;
        }
        // An open switcher can survive crossing into the native mobile navbar.
        if (this.qingLauncherState.isOpen) {
            const rect = more?.getBoundingClientRect();
            const switcher = root.querySelector(".o_qing_app_switcher");
            const width = switcher?.getBoundingClientRect().width || Math.min(400, window.innerWidth - 28);
            this.qingTopNav.left = Math.max(14, Math.min(rect?.left ?? 14, window.innerWidth - width - 14));
            this.qingTopNav.top = Math.max(rect?.bottom ?? 0, root.getBoundingClientRect().bottom) + 8;
            switcher?.style.setProperty("--o-qing-app-switcher-max-height",
                `${Math.max(0, window.innerHeight - this.qingTopNav.top - 14)}px`);
        }
    },

    async selectQingTopApp(app) {
        // Use the native menu service; it owns routing and unsaved-change handling.
        if (!this._isAvailable(app)) return;
        await this.menuService.selectMenu(app);
    },

    get qingSidebarCollapsed() {
        return window.qingWebUi?.getPreferences().sidebarState === "collapsed";
    },

    get qingSidebarToggleLabel() {
        return this.qingSidebarCollapsed ? _t("Expand sidebar") : _t("Collapse sidebar");
    },

    async toggleQingSidebar() {
        if (this.qingSidebarSaving) return;
        this.hideQingMenuHint();
        this.qingSidebarSaving = true;
        try {
            const saved = await this.qingMenuOrm.call("res.users.settings", "set_qing_theme_preferences", [{
                base: { sidebarState: this.qingSidebarCollapsed ? "expanded" : "collapsed" },
            }]);
            window.qingWebUi?.setPreferences(saved.base);
        } catch (_error) {
            this.qingMenuNotification.add(_t("Could not save theme preferences. Please try again."), { type: "danger" });
        } finally {
            this.qingSidebarSaving = false;
        }
    },

    showQingMenuHint(event, menu) {
        if (!this.qingSidebarCollapsed) return;
        const rect = event.currentTarget.getBoundingClientRect();
        this.qingMenuHint.text = menu.name;
        this.qingMenuHint.top = Math.min(window.innerHeight - 40, Math.max(8, rect.top));
    },

    hideQingMenuHint() {
        this.qingMenuHint.text = "";
    },

    openQingCommandPalette() {
        this.env.services.command.openMainPalette();
    },

    get qingSidebarRows() {
        return buildQingSidebarRows(
            this.currentAppSections,
            this.qingSidebarGroups,
            this.actionService.currentController?.action?.id,
            this.qingSidebarCollapsed,
            window.qingWebUi?.getPreferences().sidebarGroupsDefault || "collapsed"
        );
    },

    get qingSidebarStyle() {
        // Icon-only collapse keeps one shared layout for every style.
        if (this.qingSidebarCollapsed) return "tree";
        return window.qingWebUi?.getPreferences().sidebarStyle || "tree";
    },

    get qingSidebarSections() {
        return buildQingSidebarSections(this.currentApp, this.currentAppSections);
    },

    get qingRailCurrent() {
        const sections = this.qingSidebarSections;
        const actionId = this.actionService.currentController?.action?.id;
        return sections.find((section) => section.key === this.qingRailState.key) ||
            findQingActiveSection(sections, actionId) || sections[0] || null;
    },

    get qingRailResults() {
        const query = this.qingRailState.query.trim().toLocaleLowerCase();
        if (!query) return null;
        const results = [];
        for (const section of this.qingSidebarSections) {
            for (const menu of section.items) {
                if ((menu.name || "").toLocaleLowerCase().includes(query)) {
                    results.push({ menu, path: this.qingSectionPath(section) });
                }
            }
        }
        return results;
    },

    qingSectionLabel(section) {
        return section.title || this.currentApp?.name || "";
    },

    qingSectionPath(section) {
        return [...section.path, this.qingSectionLabel(section)].join(" › ");
    },

    qingSectionHasActive(section) {
        const actionId = this.actionService.currentController?.action?.id;
        return Boolean(actionId) && section.items.some((menu) => String(menu.actionID) === String(actionId));
    },

    selectQingRailSection(section) {
        this.qingRailState.key = section.key;
        this.qingRailState.query = "";
    },

    onQingRailSearch(event) {
        this.qingRailState.query = event.target.value;
    },

    toggleQingSidebarGroup(row) {
        this.qingSidebarGroups[row.menu.id] = !row.isOpen;
        this.hideQingMenuHint();
    },

    openQingActiveSidebarGroups(actionId) {
        const visit = (menu) => {
            const containsActive = String(menu.actionID) === String(actionId) ||
                (menu.childrenTree || []).some(visit);
            if (containsActive && menu.childrenTree?.length) {
                this.qingSidebarGroups[menu.id] = true;
            }
            return containsActive;
        };
        (this.currentAppSections || []).forEach(visit);
    },

    qingMenuIsActive(menu) {
        const actionId = this.actionService.currentController?.action?.id;
        return Boolean(actionId && String(menu.actionID) === String(actionId));
    },

    qingMenuIcon(menu) {
        return menuIconHref(menu);
    },

    get qingLauncherApps() {
        const query = this.qingLauncherState.query.trim().toLocaleLowerCase();
        const apps = this.qingOrderedApps;
        if (!query) {
            return apps;
        }
        return apps
            .filter((app) => app.name.toLocaleLowerCase().includes(query));
    },


    get qingLauncherUserName() {
        return user.name || user.login || "Qing User";
    },

    get qingTechnicalSupportLabel() {
        return _t("Technical Support: Qingdao Ohm Network Technology");
    },

    get qingLauncherCompanyName() {
        return user.activeCompany?.name || user.defaultCompany?.name || "";
    },

    get qingCompanyLogoUrl() {
        const companyId = user.activeCompany?.id || user.defaultCompany?.id;
        if (!companyId || session.qing_company_default_logo_ids?.includes(companyId)) {
            return "";
        }
        // Native company saves reload the session, including this logo revision.
        return imageUrl("res.company", companyId, "logo", {
            unique: session.qing_company_logo_versions?.[companyId],
        });
    },

    get qingLauncherUserAvatar() {
        return imageUrl("res.partner", user.partnerId, "avatar_128", { unique: user.writeDate });
    },

    openQingLauncher() {
        this.qingLastActiveElement = document.activeElement;
        this.qingSwitcherNeedsFocus = true;
        this.qingLauncherState.isOpen = true;
        this.qingLauncherState.query = "";
    },

    qingSwitcherOwnsEscape() {
        if (!this.qingLauncherState.isOpen || this.ui.isBlocked) return false;
        const panel = this.root()?.querySelector(".o_qing_app_switcher");
        // Native dialogs own the active area, including before their autofocus settles.
        // A responsive navbar replacement can temporarily leave DOM focus on body.
        return Boolean(panel && this.ui.activeElement?.contains(panel));
    },

    qingAppSwitcherKeydown(event) {
        if (event.key === "Escape") {
            if (!this.qingSwitcherOwnsEscape()) return;
            event.preventDefault();
            event.stopPropagation();
            this.closeQingLauncher();
            return;
        }
        const panel = event.currentTarget;
        const focused = document.activeElement;
        if (event.key === "Tab") {
            // Keep the optional Reset control in the native tab sequence.
            const controls = [...panel.querySelectorAll("input, button:not(:disabled), a[href]")];
            const index = controls.indexOf(focused);
            const next = index + (event.shiftKey ? -1 : 1);
            if (next < 0 || next >= controls.length) this.closeQingLauncher();
            return;
        }
        if (event.altKey || event.shiftKey || event.ctrlKey || event.metaKey) return;
        const vertical = ["ArrowDown", "ArrowUp"].includes(event.key);
        const horizontal = ["ArrowLeft", "ArrowRight"].includes(event.key);
        if (!vertical && !horizontal) return;
        const search = panel.querySelector(".o_qing_app_switcher__search input");
        const items = [...panel.querySelectorAll(".o_qing_app_switcher__item")];
        if (focused === search) {
            // Left and Right still move the search input's caret.
            if (!vertical || !items.length) return;
            event.preventDefault();
            (event.key === "ArrowDown" ? items[0] : items.at(-1)).focus();
            return;
        }
        const index = items.indexOf(focused);
        if (index < 0) return;
        event.preventDefault();
        if (horizontal) {
            const rtl = getComputedStyle(panel).direction === "rtl";
            const direction = (event.key === "ArrowRight") !== rtl ? 1 : -1;
            items[Math.max(0, Math.min(items.length - 1, index + direction))]?.focus();
            return;
        }
        // Read the actual CSS grid so resizing and filtered results stay aligned.
        const list = panel.querySelector(".o_qing_app_switcher__list");
        const tracks = getComputedStyle(list).gridTemplateColumns;
        const columns = tracks === "none" ? 1 : Math.max(1, tracks.trim().split(/\s+/).length);
        const next = event.key === "ArrowDown" ? index + columns : index - columns;
        const hasNextRow = (Math.floor(index / columns) + 1) * columns < items.length;
        if (next < 0 || (event.key === "ArrowDown" && !hasNextRow)) {
            search?.focus();
        } else {
            items[Math.min(items.length - 1, next)]?.focus();
        }
    },

    closeQingLauncher() {
        if (!this.qingLauncherState.isOpen) return;
        this.qingLauncherState.isOpen = false;
        this.qingLauncherState.query = "";
        const target = this.qingLastActiveElement?.isConnected
            ? this.qingLastActiveElement
            : this.root()?.querySelector(".o_qing_top_apps__more, .o_menu_toggle");
        this.qingLastActiveElement = null;
        queueMicrotask(() => target?.focus?.());
    },

    toggleQingLauncher() {
        if (this.qingLauncherState.isOpen) {
            this.closeQingLauncher();
            return;
        }
        this.openQingLauncher();
    },

    setQingLauncherQuery(event) {
        this.qingLauncherState.query = event.currentTarget.value;
    },

    async selectQingLauncherApp(app) {
        if (!this._isAvailable(app)) return;
        await this.menuService.selectMenu(app);
        this.closeQingLauncher();
    },

});
