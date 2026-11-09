/** @odoo-module **/

import { user } from "@web/core/user";
import { session } from "@web/session";

const pagePreferences = new Map();
const pendingWrites = new Set();

const PREFERENCE_PREFIX = "qing_web_ui_preferences";
const SCHEMA = 4;

const DEFAULT_PREFERENCES = {
    schema: SCHEMA,
    density: "standard",
    sidebarState: "expanded",
    sidebarGroupsDefault: "collapsed",
    sidebarStyle: "tree",
    listActionsAlignment: "right",
    chatterPosition: "auto",
    reducedMotion: false,
    themeMode: "system",
    accentColor: "teal",
    textSize: "medium",
    autoOpenLauncher: false,
    swipeActions: false,
};

function clampDensity(value) {
    if (value === "compact" || value === "standard" || value === "relaxed") {
        return value;
    }
    return DEFAULT_PREFERENCES.density;
}

function clampSidebar(value) {
    return value === "collapsed" ? value : DEFAULT_PREFERENCES.sidebarState;
}

function clampSidebarGroupsDefault(value) {
    return value === "expanded" ? value : DEFAULT_PREFERENCES.sidebarGroupsDefault;
}

function clampSidebarStyle(value) {
    return ["tree", "islands", "rail"].includes(value) ? value : DEFAULT_PREFERENCES.sidebarStyle;
}

function clampThemeMode(value) {
    return ["system", "light", "dark"].includes(value) ? value : DEFAULT_PREFERENCES.themeMode;
}

function clampAccentColor(value) {
    return ["teal", "blue", "purple", "amber", "green"].includes(value)
        ? value
        : DEFAULT_PREFERENCES.accentColor;
}

// Schema 3 "right" kept the native rule (side only on very wide screens),
// which is what "auto" means now; "right" asks for the side whenever it fits.
function clampChatterPosition(raw) {
    const value = raw.chatterPosition;
    if (value === "right" && (raw.schema || 0) < 4) return "auto";
    return ["auto", "right", "bottom"].includes(value) ? value : DEFAULT_PREFERENCES.chatterPosition;
}

function clampTextSize(value) {
    return ["small", "medium", "large"].includes(value) ? value : DEFAULT_PREFERENCES.textSize;
}

function getDbFromUrl(url) {
    const value =
        new URL(url || window.location.href).searchParams.get("db") ||
        new URL(url || window.location.href).searchParams.get("database");

    return value || null;
}

function getSessionScope() {
    const dbName =
        session.db ||
        session.database ||
        session.db_name ||
        getDbFromUrl() ||
        "default";
    const userId = user.userId || "0";
    return `${dbName}::${userId}`;
}

function getStorageKey() {
    return `${PREFERENCE_PREFIX}__${getSessionScope()}`;
}

function getLegacyStorageKey() {
    return `${PREFERENCE_PREFIX}`;
}

function sanitize(raw) {
    if (!raw || typeof raw !== "object") {
        return { ...DEFAULT_PREFERENCES };
    }

    const reducedMotion =
        typeof raw.reducedMotion === "boolean" ? raw.reducedMotion : DEFAULT_PREFERENCES.reducedMotion;

    return {
        schema: SCHEMA,
        density: clampDensity(raw.density),
        sidebarState: clampSidebar(raw.sidebarState),
        sidebarGroupsDefault: clampSidebarGroupsDefault(raw.sidebarGroupsDefault),
        sidebarStyle: clampSidebarStyle(raw.sidebarStyle),
        listActionsAlignment: raw.listActionsAlignment === "left" ? "left" : "right",
        chatterPosition: clampChatterPosition(raw),
        reducedMotion,
        themeMode: clampThemeMode(raw.themeMode),
        accentColor: clampAccentColor(raw.accentColor),
        textSize: clampTextSize(raw.textSize),
        autoOpenLauncher:
            typeof raw.autoOpenLauncher === "boolean"
                ? raw.autoOpenLauncher
                : DEFAULT_PREFERENCES.autoOpenLauncher,
        swipeActions:
            typeof raw.swipeActions === "boolean" ? raw.swipeActions : DEFAULT_PREFERENCES.swipeActions,
    };
}

function readStorageValue(storage, key) {
    const raw = storage.getItem(key);
    if (!raw) {
        return null;
    }
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") {
            return null;
        }
        if (parsed.schema && parsed.schema > SCHEMA) {
            return null;
        }
        return sanitize(parsed);
    } catch (_error) {
        return null;
    }
}

function parseLegacyPreferences(raw) {
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") {
            return null;
        }
        return sanitize({
            schema: SCHEMA,
            density: parsed.density,
            sidebarState: parsed.sidebarState,
            sidebarGroupsDefault: parsed.sidebarGroupsDefault,
            sidebarStyle: parsed.sidebarStyle,
            listActionsAlignment: parsed.listActionsAlignment,
            reducedMotion: parsed.reducedMotion,
            themeMode: parsed.themeMode,
            accentColor: parsed.accentColor,
            textSize: parsed.textSize,
            autoOpenLauncher: parsed.autoOpenLauncher,
            swipeActions: parsed.swipeActions,
        });
    } catch (_error) {
        return null;
    }
}

function getStorage() {
    const key = getStorageKey();
    if (pendingWrites.has(key)) return pagePreferences.get(key);
    try {
        const scoped = readStorageValue(window.localStorage, getStorageKey());
        if (scoped) {
            return scoped;
        }

        const legacyRaw = window.localStorage.getItem(getLegacyStorageKey());
        if (legacyRaw && !user.userId) {
            const legacy = parseLegacyPreferences(legacyRaw);
            if (legacy) {
                const normalized = sanitize(legacy);
                writeStorage(normalized);
                return normalized;
            }
        }
    } catch (_error) {
        // Account preferences still work when browser storage is blocked.
    }

    return pagePreferences.get(getStorageKey()) || { ...DEFAULT_PREFERENCES };
}

function writeStorage(value) {
    const normalized = sanitize(value);
    const key = getStorageKey();
    pagePreferences.set(key, normalized);
    try {
        window.localStorage.setItem(key, JSON.stringify(normalized));
        pendingWrites.delete(key);
    } catch (_error) {
        pendingWrites.add(key);
        // Ignore storage errors to avoid blocking login and business flows.
    }
}

function withRoot(callback) {
    const candidateNodes = [
        document.body,
        document.documentElement,
        document.querySelector(".o_web_client"),
    ];
    const root = candidateNodes.find((node) => node);
    if (!root) {
        return null;
    }
    callback(root);
    return root;
}

function getDarkFlag() {
    const darkScheme = window.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
    const lightScheme = window.matchMedia?.("(prefers-color-scheme: light)")?.matches;
    if (darkScheme && !lightScheme) {
        return true;
    }
    if (lightScheme && !darkScheme) {
        return false;
    }

    return (
        document.body?.classList.contains("o_dark") ||
        document.documentElement?.classList.contains("o_dark") ||
        document.querySelector(".o_web_client")?.classList.contains("o_dark") ||
        false
    );
}

const ACCENT_COLORS = {
    teal: { accent: "#2b6c6a", weak: "#3c8582" },
    blue: { accent: "#2f6aff", weak: "#6c87ff" },
    purple: { accent: "#7b507a", weak: "#986b96" },
    amber: { accent: "#c27a06", weak: "#db961d" },
    green: { accent: "#2c9660", weak: "#45b87a" },
};

function isDarkMode(preference) {
    if (preference.themeMode === "dark") {
        return true;
    }
    if (preference.themeMode === "light") {
        return false;
    }
    return getDarkFlag();
}

function applyPreferenceClassList(root, preference) {
    root.classList.add("o_qing_web_ui");
    root.classList.remove(
        "o_qing_ui--compact",
        "o_qing_ui--standard",
        "o_qing_ui--relaxed",
        "o_qing_ui--sidebar-collapsed",
        "o_qing_ui--reduced-motion",
        "o_qing_ui--dark",
        "o_qing_ui--text-small",
        "o_qing_ui--text-medium",
        "o_qing_ui--text-large",
    );
    root.classList.add(`o_qing_ui--${preference.density}`);
    if (preference.sidebarState === "collapsed") {
        root.classList.add("o_qing_ui--sidebar-collapsed");
    }
    if (preference.reducedMotion || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
        root.classList.add("o_qing_ui--reduced-motion");
    }
    if (isDarkMode(preference)) {
        root.classList.add("o_qing_ui--dark");
    }
    root.classList.add(`o_qing_ui--text-${preference.textSize}`);
    const accent = ACCENT_COLORS[preference.accentColor] || ACCENT_COLORS.teal;
    root.style.setProperty("--o-qing-accent", accent.accent);
    root.style.setProperty("--o-qing-accent-weak", accent.weak);

    root.dataset.qingThemeDensity = preference.density;
    root.dataset.qingThemeSidebar = preference.sidebarState;
    root.dataset.qingThemeSidebarGroupsDefault = preference.sidebarGroupsDefault;
    root.dataset.qingSidebarStyle = preference.sidebarStyle;
    root.dataset.qingListActionsAlignment = preference.listActionsAlignment;
    root.dataset.qingChatterPosition = preference.chatterPosition;
    root.dataset.qingThemeReducedMotion = String(Boolean(preference.reducedMotion));
    root.dataset.qingThemeMode = preference.themeMode;
    root.dataset.qingThemeAccent = preference.accentColor;
    root.dataset.qingThemeTextSize = preference.textSize;
    root.dataset.qingThemeSchema = String(preference.schema);
}

function applyPreferences(preference) {
    const normalized = sanitize(preference);
    withRoot((root) => {
        applyPreferenceClassList(root, normalized);
    });
    window.dispatchEvent(new CustomEvent("qing-web-ui:preferences-changed", { detail: normalized }));
}

function readPreference() {
    return getStorage();
}

function savePreference(partial) {
    const current = getStorage();
    const updated = sanitize({
        ...current,
        ...(partial || {}),
    });
    writeStorage(updated);
    applyPreferences(updated);
    return updated;
}

function resetPreferences() {
    const defaults = { ...DEFAULT_PREFERENCES };
    writeStorage(defaults);
    applyPreferences(defaults);
    return defaults;
}

function ensureApplied() {
    const current = readPreference();
    applyPreferences(current);
    writeStorage(current);
}

window.qingWebUi = {
    getPreferences: readPreference,
    getDefaults: () => ({ ...DEFAULT_PREFERENCES }),
    setPreferences: savePreference,
    resetPreferences,
};

// The account is authoritative on each login/reload. Old browser-only choices
// remain available until the user explicitly saves; never upload a stale cache.
if (session.qing_theme_preferences?.base) {
    writeStorage(session.qing_theme_preferences.base);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensureApplied, { once: true });
} else {
    ensureApplied();
}

let syncingStorageEvent = false;
window.addEventListener("storage", (event) => {
    if (
        !syncingStorageEvent &&
        event.key &&
        (event.key === getStorageKey() || event.key === getLegacyStorageKey())
    ) {
        // Native storage events are delivered to other documents. HOOT's mock
        // dispatches them synchronously on the same window, so avoid writing
        // back while applying a remote preference update.
        syncingStorageEvent = true;
        pagePreferences.delete(getStorageKey());
        pendingWrites.delete(getStorageKey());
        try {
            applyPreferences(readPreference());
        } finally {
            syncingStorageEvent = false;
        }
    }
});

const reducedMotionMedia = window.matchMedia?.("(prefers-reduced-motion: reduce)");
if (reducedMotionMedia?.addEventListener) {
    reducedMotionMedia.addEventListener("change", ensureApplied);
}

// System mode follows OS changes without turning them into account choices.
const colorSchemeMedia = window.matchMedia?.("(prefers-color-scheme: dark)");
colorSchemeMedia?.addEventListener("change", () => {
    const current = readPreference();
    if (current.themeMode === "system") applyPreferences(current);
});
