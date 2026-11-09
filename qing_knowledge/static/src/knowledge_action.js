import { Component, markup, onPatched, onWillDestroy, onWillStart, proxy, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { loadBundle } from "@web/core/assets";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { useSetupAction } from "@web/search/action_hook";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { hasDraggedFiles } from "@qing_base/js/file_drop_support";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { browser } from "@web/core/browser/browser";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { highlightText } from "@web/core/utils/html";

const ARTICLE_PAGE_SIZE = 200;
const MAX_TREE_ARTICLES = 2000;
const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 480;
const SPACE_COLORS = ["#3d6fd1", "#2f9e7a", "#c27a2c", "#a35bd1", "#d0556b", "#2a8fa8"];
const HOME_RECENT_LIMIT = 6;
const HOME_WORK_LIMIT = 5;
const CONTROLLED_PDF = /^\/qing\/knowledge\/(?:draft_asset|asset)\/[0-9a-f-]{36}\/assets\/[0-9a-f]{64}\.pdf$/;

export class KnowledgeWorkspace extends Component {
    static template = "qing_knowledge.Workspace";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.sidebarStorageKey = `qing_knowledge.sidebar.${user.userId}`;
        let sidebar = {};
        try {
            sidebar = JSON.parse(browser.localStorage.getItem(this.sidebarStorageKey) || "{}") || {};
        } catch {
            // Storage can be unavailable in a restricted browser session.
        }
        this.state = proxy({
            spaces: [], articles: [], pending: [], favorites: [], recent: [], members: [], spaceId: null, articleId: null,
            article: null, draft: null, revisions: [], diff: "", query: "", newTitle: "", newTemplate: "blank",
            newParentId: null, moveParentId: null, restrictionMode: "inherit", restrictedMemberIds: [], mode: "read",
            dirty: false, saving: false, error: "", saveError: "", preview: "", status: "",
            editorMode: sidebar.editorMode === "source" ? "source" : "visual", editorReady: false, publishOperationUUID: null,
            creatingArticle: false, creatingBusy: false, newSpaceId: null, newSpaces: [], newParents: [], newError: "",
            insertOpen: false, ownerName: "", creatingSpace: false, newSpaceName: "", treeCollapsed: false,
            expandedArticleIds: [], treeTruncated: false, dragArticleId: null, dropTarget: "", spaceRole: "",
            sidebarCollapsed: sidebar.collapsed === true || window.matchMedia("(max-width: 700px)").matches,
            sidebarWidth: Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, Number(sidebar.width) || 240)),
            showPreview: sidebar.showPreview === true,
            showOutline: sidebar.showOutline === true, detailsOpen: false, searchOpen: false, searchScope: "all",
            searchResults: [], draftResults: [], myDrafts: [], trash: [], navigation: "home", pdfUrl: "", pdfName: "",
            addMenuOpen: false, searchActive: 0, searchTypes: { published: true, drafts: true }, spaceCounts: {}, articleSpaces: {},
            publishing: false, editorOutline: [], activeOutlineId: null,
            resizingSidebar: false, uploadingAsset: false, formats: {},
        });
        this.preferredEditorMode = this.state.editorMode;
        this.workspaceRoot = signal.ref();
        this.searchInput = signal.ref();
        this.newTitleInput = signal.ref();
        this.importFileInput = signal.ref();
        this.assetFileInput = signal.ref();
        this.editorRoot = signal.ref();
        this.editor = null;
        this.editorDraftId = null;
        this.editorMounting = false;
        this.autosaveTimer = null;
        this.maxAutosaveTimer = null;
        this.previewTimer = null;
        this.navigationVersion = 0;
        this.listVersion = 0;
        this.treeSpaceId = null;
        this.composing = false;
        this.importOperation = null;
        this.destroyed = false;
        useSetupAction({
            beforeLeave: () => {
                this.flushEditor();
                return !this.state.dirty || window.confirm(_t("This draft has unsaved changes. Leave anyway?"));
            },
            beforeUnload: (event) => {
                this.flushEditor();
                if (this.state.dirty) {
                    event.preventDefault();
                    event.returnValue = "";
                }
            },
        });
        onWillStart(() => this.load());
        this.handleKeyboard = (event) => {
            const root = this.workspaceRoot();
            if (!root?.isConnected || !root.getClientRects().length) return;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
                event.preventDefault();
                event.stopImmediatePropagation();
                this.openSearch();
            } else if (event.key === 'Escape') {
                this.closeAddMenu();
                this.closeSearch();
                this.state.detailsOpen = false;
                this.state.pdfUrl = '';
                this.closeNewArticle();
                this.closeInsert();
            }
        };
        document.addEventListener('keydown', this.handleKeyboard, true);
        onPatched(() => {
            const outlineContext = `${this.state.articleId}:${this.state.draft?.uuid}:${this.state.mode}:${this.state.editorMode}`;
            if (outlineContext !== this.outlineContext) {
                this.outlineContext = outlineContext;
                this.state.activeOutlineId = null;
            }
            this.syncEditor();
            const modal = this.state.creatingArticle ? "create" : this.state.insertOpen ? "insert" : this.state.pdfUrl ? "pdf" : this.state.detailsOpen ? "details" : this.state.searchOpen ? "search" : "";
            if (modal !== this.previousModal) {
                if (modal) {
                    if (!this.previousModal) this.modalOpener = document.activeElement;
                    this.workspaceRoot()?.querySelector('[role="dialog"]')?.querySelector('input,button')?.focus();
                } else if (this.previousModal !== "insert") {
                    this.modalOpener?.focus();
                }
                this.previousModal = modal;
            }
            const layer = this.workspaceRoot()?.querySelector('.o_qing_knowledge_layer [role="dialog"]');
            if (this.focusInsert && layer) {
                layer.querySelector('button')?.focus();
                this.focusInsert = false;
            }
            if (this.focusEditorAfterModal && !this.hasModal && this.state.editorReady) {
                this.editor?.focus();
                this.focusEditorAfterModal = false;
            }
            if (this.focusSearch && this.searchInput()) {
                this.searchInput().focus();
                this.focusSearch = false;
            }
            if (this.scrollActiveHit) {
                this.workspaceRoot()?.querySelector('.o_qing_knowledge_hit.active')?.scrollIntoView({ block: 'nearest' });
                this.scrollActiveHit = false;
            }
            if (this.focusAddMenu) {
                this.workspaceRoot()?.querySelector('.o_qing_knowledge_menu [role="menuitem"]')?.focus();
                this.focusAddMenu = false;
            }
            if (this.focusNewSpace) {
                this.workspaceRoot()?.querySelector('#qing_new_space_name')?.focus();
                this.focusNewSpace = false;
            }
            if (this.focusNewTitle && this.newTitleInput()) {
                this.newTitleInput().focus();
                this.focusNewTitle = false;
            }
        });
        onWillDestroy(() => {
            this.destroyed = true;
            document.removeEventListener("keydown", this.handleKeyboard, true);
            clearTimeout(this.searchTimer);
            clearTimeout(this.autosaveTimer);
            clearTimeout(this.maxAutosaveTimer);
            clearTimeout(this.previewTimer);
            this.editor?.destroy();
        });
    }

    get safeHtml() {
        const html = this.state.mode !== "read" ? this.state.preview : (this.state.article?.html || "");
        // Owl compares Markup by identity. Preserve the rendered nodes (and focus)
        // when only outline selection, sidebar or toolbar state changes.
        if (this.htmlCache?.source === html) return this.htmlCache.value;
        const document = new DOMParser().parseFromString(html, 'text/html');
        document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((heading, index) => {
            heading.id = `qing-knowledge-heading-${index}`;
        });
        for (const anchor of document.querySelectorAll('a[href]')) {
            let url;
            try { url = new URL(anchor.getAttribute('href'), window.location.origin); } catch { continue; }
            if (url.origin !== window.location.origin || !CONTROLLED_PDF.test(url.pathname)) continue;
            anchor.classList.add('o_qing_knowledge_file_link');
            anchor.setAttribute('data-knowledge-caption', _t('PDF · Click to preview'));
            anchor.setAttribute('aria-label', _t('Preview PDF: %s', anchor.textContent));
        }
        const value = markup(document.body.innerHTML);
        this.htmlCache = { source: html, value };
        return value;
    }

    get favoriteActionLabel() {
        return this.state.article?.is_favorite ? _t("Remove from favorites") : _t("Add to favorites");
    }

    get greeting() {
        const hour = new Date().getHours();
        if (hour < 12) return _t("Good morning, %s", user.name);
        if (hour < 18) return _t("Good afternoon, %s", user.name);
        return _t("Good evening, %s", user.name);
    }

    spaceColor(spaceId) {
        return SPACE_COLORS[Math.abs(Number(spaceId) || 0) % SPACE_COLORS.length];
    }

    spaceInitial(name) {
        return Array.from((name || "").trim())[0]?.toUpperCase() || "·";
    }

    spaceCountLabel(spaceId) {
        const count = this.state.spaceCounts[spaceId];
        return count ? _t("%s articles", count) : _t("No articles yet");
    }

    relativeTime(value) {
        if (!value) return "";
        try {
            return deserializeDateTime(value).toRelative() || "";
        } catch {
            return "";
        }
    }

    highlight(text) {
        return highlightText(this.state.query.trim(), text || "", "o_qing_knowledge_mark");
    }

    bookmarkItem(row) {
        const articleId = row.article_id[0];
        const space = this.state.articleSpaces[articleId] || {};
        const when = this.relativeTime(row.last_opened_at);
        return {
            id: row.id, kind: "article", article_id: articleId, title: row.article_id[1],
            space_id: space.id, space_name: space.name || "", when,
            meta: [space.name, when].filter(Boolean).join(" · "),
        };
    }

    draftItem(draft) {
        const submitted = draft.state === "submitted";
        return {
            id: draft.id, key: `draft:${draft.id}`, kind: "draft", article_id: draft.article_id,
            title: draft.title, meta: [draft.space_name, this.relativeTime(draft.updated_at)].filter(Boolean).join(" · "),
            label: submitted ? _t("Submitted for review") : _t("Editing"), tone: submitted ? "accent" : "neutral",
        };
    }

    reviewItem(draft) {
        return {
            id: draft.id, key: `review:${draft.id}`, kind: "review", article_id: draft.article_id?.[0],
            title: draft.title, meta: this.state.spaces.find(space => space.id === this.state.spaceId)?.name || "",
            label: _t("Awaiting review"), tone: "blue",
        };
    }

    get homeRecent() {
        return this.state.recent.slice(0, HOME_RECENT_LIMIT).map((row) => this.bookmarkItem(row));
    }

    get homeWork() {
        return [
            ...this.state.pending.slice(0, HOME_WORK_LIMIT).map((draft) => this.reviewItem(draft)),
            ...this.state.myDrafts.map((draft) => this.draftItem(draft)),
        ].slice(0, HOME_WORK_LIMIT);
    }

    get breadcrumbs() {
        if (this.state.navigation !== "article" || !this.state.article) {
            return [_t("Knowledge"), this.listTitle];
        }
        const byId = new Map(this.state.articles.map((article) => [article.id, article]));
        const current = byId.get(this.state.articleId);
        const ancestors = (current?.ancestorIds || []).map((id) => byId.get(id)?.name).filter(Boolean);
        const title = this.state.mode === "read" ? this.state.article.title : (this.state.draft?.title || this.state.article.title);
        return [this.currentSpaceName, ...ancestors, title || _t("Untitled")];
    }

    openWorkItem(item) {
        if (item.kind === "review") return this.openSubmission(item.id);
        if (item.kind === "draft") return this.openOwnDraft(item.article_id);
        return this.openArticle(item.article_id);
    }

    openListItem(item) {
        return this.openWorkItem(item);
    }

    toggleAddMenu() {
        this.state.addMenuOpen = !this.state.addMenuOpen;
        if (this.state.addMenuOpen) this.focusAddMenu = true;
    }

    closeAddMenu() {
        this.state.addMenuOpen = false;
    }

    runAddMenu(kind) {
        this.closeAddMenu();
        if (kind === "article") return this.prepareNewArticle(null);
        if (kind === "import") return this.chooseImportFile();
        this.state.sidebarCollapsed = false;
        this.state.creatingSpace = true;
        this.state.creatingArticle = false;
        this.focusNewSpace = true;
    }

    onAddMenuKeydown(event) {
        const items = [...event.currentTarget.querySelectorAll('[role="menuitem"]')];
        const index = items.indexOf(document.activeElement);
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            this.closeAddMenu();
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const step = event.key === "ArrowDown" ? 1 : -1;
            items[(index + step + items.length) % items.length]?.focus();
        }
    }

    startFromTemplate(template) {
        if (!this.canCreateArticle) return;
        this.prepareNewArticle(null);
        this.state.newTemplate = template;
    }

    get favoriteLabel() {
        return this.state.article?.is_favorite ? _t("★ Saved") : _t("☆ Favorite");
    }

    get editorToggleLabel() {
        return this.state.editorMode === "source" ? _t("Visual editing") : _t("Markdown source");
    }

    get sidebarToggleLabel() {
        return this.state.sidebarCollapsed ? _t("Expand knowledge sidebar") : _t("Collapse knowledge sidebar");
    }

    get assetUploadLabel() {
        return this.state.uploadingAsset ? _t("Uploading…") : _t("Upload a file");
    }

    get previewVisible() {
        return this.state.mode === "review" || (this.state.mode === "edit" && (this.state.draft?.state === "submitted" || (this.state.editorMode === "source" && this.state.showPreview)));
    }

    persistSidebar() {
        try {
            browser.localStorage.setItem(this.sidebarStorageKey, JSON.stringify({
                collapsed: this.state.sidebarCollapsed, width: this.state.sidebarWidth,
                showPreview: this.state.showPreview, editorMode: this.preferredEditorMode,
                showOutline: this.state.showOutline,
            }));
        } catch {
            // Resizing still works when browser storage is disabled.
        }
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
        this.persistSidebar();
    }

    togglePreview() {
        this.state.showPreview = !this.state.showPreview;
        this.persistSidebar();
        if (this.state.showPreview && this.state.mode === "edit" && this.state.draft) {
            this.run(() => this.preview());
        }
    }

    setSidebarWidth(width) {
        const available = this.workspaceRoot()?.clientWidth || 960;
        const maximum = Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, available - 480));
        this.state.sidebarWidth = Math.max(SIDEBAR_MIN_WIDTH, Math.min(maximum, width));
    }

    startSidebarResize(event) {
        if (event.button !== 0) return;
        event.preventDefault();
        this.sidebarResize = { x: event.clientX, width: this.state.sidebarWidth };
        this.state.resizingSidebar = true;
        event.currentTarget.setPointerCapture(event.pointerId);
    }

    resizeSidebar(event) {
        if (!this.sidebarResize) return;
        this.setSidebarWidth(this.sidebarResize.width + event.clientX - this.sidebarResize.x);
    }

    endSidebarResize(event) {
        if (!this.sidebarResize) return;
        this.sidebarResize = null;
        this.state.resizingSidebar = false;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
        }
        this.persistSidebar();
    }

    resizeSidebarByKeyboard(event) {
        const widths = {
            ArrowLeft: this.state.sidebarWidth - 20, ArrowRight: this.state.sidebarWidth + 20,
            Home: SIDEBAR_MIN_WIDTH, End: SIDEBAR_MAX_WIDTH,
        };
        if (!(event.key in widths)) return;
        event.preventDefault();
        this.setSidebarWidth(widths[event.key]);
        this.persistSidebar();
    }

    chooseImportFile() {
        if (this.state.spaceId && this.canCreateArticle) this.importFileInput()?.click();
    }

    requestAssetUpload(selection) {
        if (!this.editor || this.state.uploadingAsset || this.state.draft?.state !== "editing") return;
        this.assetUploadTarget = {
            draftUuid: this.state.draft.uuid, editor: this.editor,
            selection: selection || this.editor.captureSelection(),
        };
        this.assetFileInput()?.click();
    }

    cancelAssetUpload() {
        const target = this.assetUploadTarget;
        target?.editor.releaseSelection();
        this.assetUploadTarget = null;
    }

    get visibleArticles() {
        if (this.state.query.trim()) return this.state.articles;
        const expanded = new Set(this.state.expandedArticleIds);
        return this.state.articles.filter((article) => article.ancestorIds.every((id) => expanded.has(id)));
    }

    get spaceRole() {
        return this.state.spaceRole;
    }

    get canManageSpace() {
        return this.spaceRole === "manager";
    }

    get canCreateArticle() {
        return ["editor", "publisher", "manager"].includes(this.spaceRole);
    }

    get moveParentCandidates() {
        const moving = this.state.articles.find((article) => article.id === this.state.articleId);
        if (!moving) return this.state.articles;
        return this.state.articles.filter((article) => article.id !== moving.id
            && !article.ancestorIds?.includes(moving.id));
    }

    async run(action) {
        try {
            this.state.error = "";
            return await action();
        } catch (error) {
            this.state.error = error.data?.message || error.message || _t("Operation failed.");
            this.notification.add(this.state.error, { type: "danger" });
        }
    }

    async load() {
        await this.run(async () => {
            this.state.spaces = await this.orm.searchRead("qing.knowledge.space", [], ["name", "uuid", "publication_policy"]);
            if (!this.state.spaceId && this.state.spaces.length) {
                this.state.spaceId = this.state.spaces[0].id;
            }
            await this.loadArticles();
            await this.loadMyDrafts();
            await this.loadSpaceCounts();
        });
    }

    async loadSpaceCounts() {
        const rows = await this.orm.formattedReadGroup(
            "qing.knowledge.article", [["lifecycle", "!=", "trashed"]], ["space_id"], ["__count"]
        );
        if (this.destroyed) return;
        const counts = {};
        for (const row of rows) {
            const spaceId = Array.isArray(row.space_id) ? row.space_id[0] : row.space_id;
            if (spaceId) counts[spaceId] = row.__count || 0;
        }
        this.state.spaceCounts = counts;
    }

    async loadArticleSpaces(articleIds) {
        const missing = [...new Set(articleIds)].filter((id) => id && !(id in this.state.articleSpaces));
        if (!missing.length) return;
        const rows = await this.orm.searchRead("qing.knowledge.article", [["id", "in", missing]], ["space_id"]);
        if (this.destroyed) return;
        const spaces = { ...this.state.articleSpaces };
        for (const row of rows) {
            spaces[row.id] = { id: row.space_id?.[0] || false, name: row.space_id?.[1] || "" };
        }
        this.state.articleSpaces = spaces;
    }

    async loadArticles() {
        const listVersion = ++this.listVersion;
        if (!this.state.spaceId) {
            this.state.articles = [];
            this.state.pending = [];
            this.state.members = [];
            this.state.favorites = [];
            this.state.recent = [];
            this.state.spaceRole = "";
            return;
        }
        const spaceId = this.state.spaceId;
        const domain = [["space_id", "=", spaceId], ["lifecycle", "!=", "trashed"]];
        const rows = [];
        let page = [];
        do {
            page = await this.orm.searchRead(
                "qing.knowledge.article", domain, ["name", "uuid", "lifecycle", "space_id", "parent_id"],
                { limit: ARTICLE_PAGE_SIZE, offset: rows.length, order: "sequence, id" }
            );
            if (listVersion !== this.listVersion || spaceId !== this.state.spaceId) return;
            rows.push(...page);
        } while (page.length === ARTICLE_PAGE_SIZE && rows.length < MAX_TREE_ARTICLES);
        let moreRows = false;
        if (rows.length >= MAX_TREE_ARTICLES && page.length === ARTICLE_PAGE_SIZE) {
            moreRows = Boolean((await this.orm.searchRead("qing.knowledge.article", domain, ["id"],
                { limit: 1, offset: rows.length, order: "sequence, id" })).length);
        }
        if (listVersion !== this.listVersion || spaceId !== this.state.spaceId) return;
        this.state.treeTruncated = moreRows;
        const articles = this.flattenArticles(rows);
        if (this.treeSpaceId !== this.state.spaceId) {
            this.state.expandedArticleIds = articles.filter((article) => article.hasChildren).map((article) => article.id);
            this.treeSpaceId = this.state.spaceId;
        }
        this.state.articles = articles;
        const pending = await this.orm.searchRead(
            "qing.knowledge.draft", [["state", "=", "submitted"], ["article_id.space_id", "=", spaceId], ["article_id.lifecycle", "!=", "trashed"]],
            ["title", "uuid", "article_id", "draft_version", "content_hash"], { limit: 50 }
        );
        if (listVersion !== this.listVersion || spaceId !== this.state.spaceId) return;
        this.state.pending = pending;
        const [ownMembership] = await this.orm.searchRead(
            "qing.knowledge.member", [["space_id", "=", spaceId], ["user_id", "=", user.userId], ["active", "=", true]],
            ["role"], { limit: 1 }
        );
        if (listVersion !== this.listVersion || spaceId !== this.state.spaceId) return;
        this.state.spaceRole = ownMembership?.role || "";
        const members = await this.orm.searchRead(
            "qing.knowledge.member", [["space_id", "=", spaceId], ["active", "=", true]],
            ["user_id", "role"], { limit: 200 }
        );
        if (listVersion !== this.listVersion || spaceId !== this.state.spaceId) return;
        this.state.members = members;
        await this.loadBookmarks();
    }

    flattenArticles(rows) {
        const byParent = new Map();
        for (const row of rows) {
            const parent = row.parent_id?.[0];
            const key = parent || null;
            if (!byParent.has(key)) byParent.set(key, []);
            byParent.get(key).push(row);
        }
        const result = [];
        const seen = new Set();
        const visit = (parent, depth, ancestorIds) => {
            for (const row of byParent.get(parent) || []) {
                if (seen.has(row.id)) continue;
                seen.add(row.id);
                result.push({ ...row, depth, ancestorIds, hasChildren: Boolean(byParent.get(row.id)?.length) });
                visit(row.id, depth + 1, [...ancestorIds, row.id]);
            }
        };
        visit(null, 0, []);
        return result;
    }

    async loadBookmarks() {
        const spaceId = this.state.spaceId;
        const domain = [["article_id.lifecycle", "!=", "trashed"]];
        const bookmarks = await this.orm.searchRead(
            "qing.knowledge.bookmark", domain,
            ["article_id", "is_favorite", "last_opened_at"], { limit: 50, order: "last_opened_at desc, id desc" }
        );
        if (spaceId !== this.state.spaceId) return;
        this.state.favorites = bookmarks.filter((row) => row.is_favorite).slice(0, 20);
        this.state.recent = bookmarks.filter((row) => row.last_opened_at).slice(0, 10);
        await this.loadArticleSpaces(bookmarks.map((row) => row.article_id?.[0]));
    }

    async selectSpace(spaceId) {
        if (this.state.spaceId === spaceId) {
            this.state.treeCollapsed = false;
            return;
        }
        if (!(await this.saveBeforeNavigation())) return;
        this.state.spaceRole = "";
        clearTimeout(this.autosaveTimer);
        clearTimeout(this.maxAutosaveTimer);
        clearTimeout(this.previewTimer);
        this.navigationVersion++;
        await this.destroyEditor();
        this.state.spaceId = spaceId;
        this.state.navigation = "article";
        this.state.treeCollapsed = false;
        this.state.creatingArticle = false;
        this.state.newParentId = null;
        this.state.query = "";
        this.state.articleId = null;
        this.state.article = null;
        this.state.draft = null;
        this.state.revisions = [];
        this.state.mode = "read";
        this.state.dirty = false;
        await this.run(() => this.loadArticles());
    }

    toggleSpace(spaceId) {
        if (spaceId !== this.state.spaceId) return this.selectSpace(spaceId);
        this.state.treeCollapsed = !this.state.treeCollapsed;
    }

    toggleCreateSpace() {
        this.state.creatingSpace = !this.state.creatingSpace;
        this.state.creatingArticle = false;
    }

    toggleArticle(articleId) {
        const expanded = new Set(this.state.expandedArticleIds);
        if (expanded.has(articleId)) expanded.delete(articleId);
        else expanded.add(articleId);
        this.state.expandedArticleIds = [...expanded];
    }

    articleOptionLabel(article) {
        return `${"— ".repeat(article.depth)}${article.name}`;
    }

    async prepareNewArticle(parentId = null) {
        if (!this.canCreateArticle || this.state.creatingBusy) return;
        this.newArticleOpener = document.activeElement;
        this.state.newTitle = "";
        this.state.newError = "";
        this.state.newTemplate = "sop";
        this.state.newSpaceId = this.state.spaceId;
        this.state.newParentId = parentId;
        this.state.newParents = this.state.articles;
        this.state.newSpaces = this.state.spaces.filter(space => space.id === this.state.spaceId);
        this.focusNewTitle = true;
        this.state.creatingArticle = true;
        this.state.creatingSpace = false;
        await this.run(async () => {
            const roles = await this.orm.searchRead("qing.knowledge.member", [
                ["user_id", "=", user.userId], ["active", "=", true],
                ["role", "in", ["editor", "publisher", "manager"]],
            ], ["space_id"]);
            if (!this.state.creatingArticle) return;
            const ids = new Set(roles.map(member => member.space_id[0]));
            this.state.newSpaces = this.state.spaces.filter(space => ids.has(space.id));
        });
    }

    closeNewArticle() {
        if (this.state.creatingBusy) return;
        this.state.creatingArticle = false;
        this.newArticleOpener?.focus();
    }

    async chooseNewSpace(event) {
        const spaceId = Number(event.target.value);
        this.state.newSpaceId = spaceId;
        this.state.newParentId = null;
        this.state.newParents = [];
        await this.run(async () => {
            const articles = await this.orm.searchRead("qing.knowledge.article", [["space_id", "=", spaceId], ["lifecycle", "!=", "trashed"]],
                ["name", "parent_id"], { limit: MAX_TREE_ARTICLES });
            if (this.state.creatingArticle && this.state.newSpaceId === spaceId) {
                this.state.newParents = articles.map(article => ({ ...article, depth: 0 }));
            }
        });
    }

    formatText(kind) {
        if (!this.editor || this.composing || this.state.publishing || this.state.draft?.state !== "editing") return;
        this.editor.format(kind);
    }

    openInsert() {
        if (!this.editor || !this.state.editorReady || this.state.draft?.state !== "editing") return;
        this.insertTarget = { editor: this.editor, draftUuid: this.state.draft.uuid, selection: this.editor.captureSelection() };
        this.state.insertOpen = true;
        this.focusInsert = true;
    }

    closeInsert() {
        if (!this.state.insertOpen) return;
        this.state.insertOpen = false;
        this.focusEditorAfterModal = true;
        this.insertTarget?.editor.releaseSelection();
        this.insertTarget?.editor.focus();
        this.insertTarget = null;
    }

    insertContent(kind) {
        const target = this.insertTarget;
        if (!target || target.editor !== this.editor || target.draftUuid !== this.state.draft?.uuid) return this.closeInsert();
        if (kind === "asset") {
            this.state.insertOpen = false;
            this.insertTarget = null;
            this.requestAssetUpload(target.selection);
            return;
        }
        const blocks = {
            table: `| ${_t("Item")} | ${_t("Description")} |\n| --- | --- |\n| ${_t("New item")} | ${_t("Enter details here.")} |`,
            callout: `> ${_t("Note: add important conditions or reminders here.")}`,
            code: "```text\n\n```",
            divider: "---",
        };
        if (!blocks[kind]) return;
        if (!target.editor.insertMarkdown(blocks[kind], target.selection)) return;
        this.focusEditorAfterModal = true;
        this.state.insertOpen = false;
        this.insertTarget = null;
    }

    get hasModal() {
        return this.state.creatingArticle || this.state.insertOpen || this.state.detailsOpen || this.state.searchOpen || !!this.state.pdfUrl;
    }

    get metadataAuthor() {
        return (this.state.mode === "edit" ? user.name : this.state.article?.author) || this.state.ownerName || "";
    }

    formatRevisionDate(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "";
    }

    get detailTags() {
        const tags = this.state.mode === "edit" && this.state.draft?.state === "editing"
            ? (this.state.draft.tag_names || "").split(",").map(tag => tag.trim()).filter(Boolean)
            : this.state.article?.tags || [];
        return [...new Set(tags)];
    }

    get detailVisibility() {
        return this.state.article?.access_mode === "restricted" ? _t("Selected space members") : _t("Space members");
    }

    async createSpace() {
        this.flushEditor();
        const name = this.state.newSpaceName.trim();
        if (!name) return;
        if (this.state.dirty) {
            this.notification.add(_t("Save or close the current draft before creating a space."), { type: "warning" });
            return;
        }
        await this.run(async () => {
            const [spaceId] = await this.orm.create("qing.knowledge.space", [{ name }]);
            this.state.spaces = await this.orm.searchRead("qing.knowledge.space", [], ["name", "uuid", "publication_policy"]);
            this.state.newSpaceName = "";
            this.state.creatingSpace = false;
            await this.selectSpace(spaceId);
        });
    }

    chooseNewParent(event) {
        this.state.newParentId = Number(event.target.value) || null;
    }

    chooseMoveParent(event) {
        this.state.moveParentId = Number(event.target.value) || null;
    }

    chooseRestrictedMembers(event) {
        this.state.restrictedMemberIds = Array.from(
            event.target.selectedOptions, (option) => Number(option.value)
        );
    }

    async openArticle(articleId, autoEdit = true) {
        if (!(await this.saveBeforeNavigation())) return;
        clearTimeout(this.autosaveTimer);
        clearTimeout(this.maxAutosaveTimer);
        clearTimeout(this.previewTimer);
        const navigation = ++this.navigationVersion;
        this.state.saveError = "";
        if (window.matchMedia("(max-width: 700px)").matches) this.state.sidebarCollapsed = true;
        this.state.navigation = "article";
        this.state.searchOpen = false;
        this.state.query = "";
        this.state.detailsOpen = false;
        this.state.pdfUrl = "";
        this.state.articleId = articleId;
        this.state.article = null;
        this.state.draft = null;
        this.state.revisions = [];
        this.state.mode = "read";
        this.state.dirty = false;
        this.state.editorReady = false;
        this.state.preview = "";
        this.state.status = "";
        await this.destroyEditor();
        await this.run(async () => {
            const article = await this.orm.call("qing.knowledge.article", "get_article", [[articleId]]);
            if (navigation !== this.navigationVersion) return;
            this.state.articleId = articleId;
            this.state.article = article;
            const [metadata] = await this.orm.read("qing.knowledge.article", [articleId], ["owner_id", "access_mode"]);
            if (navigation !== this.navigationVersion) return;
            this.state.ownerName = metadata.owner_id?.[1] || "";
            this.state.article.access_mode = metadata.access_mode;
            if (article.space_id !== this.state.spaceId) {
                this.state.spaceId = article.space_id;
                await this.loadArticles();
                if (navigation !== this.navigationVersion) return;
            }
            this.state.moveParentId = this.state.article.parent_id || null;
            this.state.restrictionMode = this.state.article.access_mode || "inherit";
            this.state.restrictedMemberIds = this.state.article.restricted_member_ids || [];
            this.state.revisions = await this.orm.call("qing.knowledge.article", "get_revisions", [[articleId]]);
            if (navigation !== this.navigationVersion) return;
            this.state.diff = "";
            await this.loadBookmarks();
            if (navigation !== this.navigationVersion) return;
            this.state.draft = null;
            this.state.publishOperationUUID = null;
            this.state.mode = "read";
            this.state.dirty = false;
            if (autoEdit && this.state.article.lifecycle === "unpublished" && this.state.article.can_edit) {
                await this.edit();
            }
        });
    }

    async createArticle() {
        if (this.state.creatingBusy) return;
        this.flushEditor();
        const title = this.state.newTitle.trim(), spaceId = this.state.newSpaceId, parentId = this.state.newParentId;
        if (!title || !spaceId) {
            this.state.newError = _t("Enter an article title.");
            this.newTitleInput()?.focus();
            return;
        }
        this.state.creatingBusy = true;
        this.state.newError = "";
        try {
            if (!(await this.saveBeforeNavigation())) {
                this.state.newError = this.state.saveError || _t("Save the current draft before creating another article.");
                return;
            }
            const result = await this.orm.call("qing.knowledge.space", "create_article", [
                [spaceId], title, parentId || false, this.state.newTemplate,
            ]);
            this.state.creatingArticle = false;
            this.state.newTitle = "";
            this.state.query = "";
            if (spaceId !== this.state.spaceId) await this.selectSpace(spaceId);
            else await this.loadArticles();
            if (parentId && !this.state.expandedArticleIds.includes(parentId)) {
                this.state.expandedArticleIds = [...this.state.expandedArticleIds, parentId];
            }
            const row = this.state.articles.find(item => item.uuid === result.article_uuid)
                || (await this.orm.searchRead("qing.knowledge.article", [["uuid", "=", result.article_uuid]], ["id"], { limit: 1 }))[0];
            if (row) await this.openArticle(row.id);
        } catch (error) {
            this.state.newError = error.data?.message || error.message || _t("Operation failed.");
        } finally {
            this.state.creatingBusy = false;
        }
    }

    async edit() {
        if (!this.state.articleId) return;
        const navigation = this.navigationVersion;
        const articleId = this.state.articleId;
        await this.run(async () => {
            const info = await this.orm.call("qing.knowledge.article", "open_draft", [[articleId]]);
            if (navigation !== this.navigationVersion || articleId !== this.state.articleId) return;
            const [draft] = await this.orm.searchRead(
                "qing.knowledge.draft", [["uuid", "=", info.draft_uuid]],
                ["title", "summary", "markdown", "tag_names", "draft_version", "state", "content_hash", "uuid"]
            );
            if (navigation !== this.navigationVersion || articleId !== this.state.articleId) return;
            draft.base_revision_uuid = info.base_revision_uuid;
            draft.content_hash = info.content_hash;
            this.state.draft = draft;
            this.state.navigation = "article";
            this.state.status = draft.state === "submitted" ? _t("Submitted for review") : _t("Draft saved");
            this.state.mode = "edit";
            this.state.dirty = false;
            this.state.editorMode = this.preferredEditorMode;
            this.state.editorReady = false;
            await loadBundle("qing_knowledge.editor");
            if (navigation !== this.navigationVersion || this.state.draft?.id !== draft.id || this.state.mode !== "edit") return;
            this.state.editorReady = true;
            await this.preview();
        });
    }

    async toggleFavorite() {
        if (!this.state.article?.published) return;
        await this.run(async () => {
            this.state.article.is_favorite = await this.orm.call(
                "qing.knowledge.article", "toggle_favorite", [[this.state.articleId]]
            );
            await this.loadBookmarks();
        });
    }

    async comparePrevious() {
        if (this.state.revisions.length < 2) return;
        await this.run(async () => {
            this.state.diff = await this.orm.call("qing.knowledge.article", "compare_revisions", [
                [this.state.articleId], this.state.revisions[1].uuid, this.state.revisions[0].uuid,
            ]);
        });
    }

    async restoreRevision(revisionId) {
        if (!(await this.saveBeforeNavigation())) return;
        this.dialog.add(ConfirmationDialog, {
            body: _t('Replace your current draft with this revision? The published version will stay unchanged.'),
            confirm: () => this.run(async () => {
                await this.orm.call("qing.knowledge.revision", "restore_as_draft", [[revisionId]]);
                await this.edit();
            }),
        });
    }

    async setLifecycle(lifecycle) {
        const articleId = this.state.articleId;
        if (!articleId || !(await this.saveBeforeNavigation())) return;
        await this.run(async () => {
            await this.orm.call("qing.knowledge.article", "set_lifecycle", [[articleId], lifecycle]);
            await this.loadArticles();
            await this.loadMyDrafts();
            if (this.state.articleId !== articleId) return;
            if (lifecycle === "trashed") await this.navigateList("trash");
            else await this.openArticle(articleId, false);
        });
    }

    async moveArticle() {
        if (!this.state.article?.can_manage || !(await this.saveBeforeNavigation())) return;
        await this.run(async () => {
            await this.orm.call("qing.knowledge.article", "move_to", [
                [this.state.articleId], this.state.moveParentId || false,
            ]);
            await this.loadArticles();
            await this.openArticle(this.state.articleId, false);
        });
    }

    canDropArticle(parentId) {
        if (!this.canManageSpace || this.state.query.trim() || !this.state.dragArticleId) return false;
        const moving = this.state.articles.find((article) => article.id === this.state.dragArticleId);
        if (!moving || (moving.parent_id?.[0] || null) === parentId) return false;
        if (!parentId) return true;
        const target = this.state.articles.find((article) => article.id === parentId);
        return Boolean(target && target.id !== moving.id && !target.ancestorIds.includes(moving.id));
    }

    startArticleDrag(event, articleId) {
        if (!this.canManageSpace || this.state.query.trim()) {
            event.preventDefault();
            return;
        }
        this.state.dragArticleId = articleId;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", `qing-knowledge:${articleId}`);
    }

    endArticleDrag() {
        this.state.dragArticleId = null;
        this.state.dropTarget = "";
    }

    dragOverParent(event, parentId) {
        event.stopPropagation();
        if (!this.canDropArticle(parentId)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        this.state.dropTarget = parentId ? `article:${parentId}` : `space:${this.state.spaceId}`;
    }

    leaveDropTarget(event) {
        if (!event.currentTarget.contains(event.relatedTarget)) this.state.dropTarget = "";
    }

    async dropOnParent(event, parentId) {
        event.stopPropagation();
        if (!this.canDropArticle(parentId)) return;
        event.preventDefault();
        const articleId = this.state.dragArticleId;
        this.endArticleDrag();
        await this.run(async () => {
            await this.orm.call("qing.knowledge.article", "move_to", [[articleId], parentId || false]);
            await this.loadArticles();
            if (parentId && !this.state.expandedArticleIds.includes(parentId)) {
                this.state.expandedArticleIds = [...this.state.expandedArticleIds, parentId];
            }
            if (this.state.articleId === articleId && this.state.article) {
                this.state.article.parent_id = parentId || false;
                this.state.moveParentId = parentId || null;
            }
        });
    }

    async saveRestriction() {
        if (!this.state.article?.can_manage) return;
        await this.run(async () => {
            await this.orm.call("qing.knowledge.article", "set_restriction", [
                [this.state.articleId], this.state.restrictedMemberIds,
                this.state.restrictionMode === "restricted",
            ]);
            await this.loadArticles();
            await this.openArticle(this.state.articleId, false);
        });
    }

    async destroyEditor() {
        this.cancelAssetUpload();
        const editor = this.editor;
        this.editor = null;
        this.editorDraftId = null;
        if (this.state.editorOutline.length) this.state.editorOutline = [];
        await editor?.destroy();
    }

    async syncEditor() {
        const root = this.editorRoot();
        if (!root || !this.state.editorReady || !this.state.draft || this.state.draft.state !== "editing" || this.state.mode !== "edit") {
            await this.destroyEditor();
            return;
        }
        const draftUuid = this.state.draft.uuid;
        const mode = this.state.editorMode;
        const key = `${draftUuid}:${mode}`;
        if (this.editorDraftId === key || this.editorMounting) return;
        this.editorMounting = true;
        const isCurrent = () => !this.destroyed && this.state.editorReady && this.editorRoot() === root
            && this.state.draft?.uuid === draftUuid && this.state.mode === "edit"
            && this.state.draft.state === "editing" && this.state.editorMode === mode;
        let retry = false;
        try {
            await this.destroyEditor();
            if (!isCurrent()) { retry = true; return; }
            const value = this.state.draft.markdown || "";
            const onChange = (source, composing = false) => {
                if (!isCurrent()) return;
                if (source !== this.state.draft.markdown || this.composing) {
                    this.changeField("markdown", { target: { value: source } }, composing);
                } else {
                    this.composing = composing;
                }
            };
            const options = {
                filePreviewLabel: _t('PDF · Click to preview'),
                onOutlineChange: (headings, activeId) => {
                    if (!isCurrent()) return;
                    this.state.editorOutline = headings.map(heading => ({ ...heading, title: heading.title || _t('Untitled heading') }));
                    this.state.activeOutlineId = activeId;
                },
                onUploadRequest: (selection) => { if (isCurrent()) this.requestAssetUpload(selection); },
                onFormatChange: (formats) => { if (isCurrent()) this.state.formats = formats; },
                labels: {
                    placeholder: _t('Write here, or type / to insert'), calloutText: _t('Note: add important conditions or reminders here.'),
                    bold: _t('Bold'), italic: _t('Italic'), strike: _t('Strikethrough'), inlineCode: _t('Inline code'),
                    link: _t('Link'), attachments: _t('Attachments'),
                    textGroup: _t('Text'), paragraph: _t('Paragraph'),
                    h1: _t('Heading 1'), h2: _t('Heading 2'), h3: _t('Heading 3'),
                    h4: _t('Heading 4'), h5: _t('Heading 5'), h6: _t('Heading 6'),
                    quote: _t('Highlight callout'), divider: _t('Divider'), listGroup: _t('Lists'),
                    bulletList: _t('Bullet list'), orderedList: _t('Numbered list'), taskList: _t('Checklist'),
                    advancedGroup: _t('More blocks'), codeBlock: _t('Code block'), table: _t('Table'),
                },
                uploadLabel: _t("Upload a file"),
                uploadDescription: _t("Insert an image or a PDF"),
                resolveAssetUrl: (path) => `/qing/knowledge/draft_asset/${draftUuid}/${path}`,
            };
            const editor = mode === "visual"
                ? await window.QingKnowledgeEditors.visual(root, value, onChange, options)
                : window.QingKnowledgeEditors.source(root, value, onChange, options);
            if (!isCurrent()) {
                await editor?.destroy();
                retry = true;
                return;
            }
            if (!editor && mode === "visual") {
                this.state.editorMode = "source";
                this.run(() => this.preview());
                retry = true;
                this.notification.add(_t("This Markdown cannot safely round-trip through visual editing. Source mode is preserved."), { type: "warning" });
                return;
            }
            this.editor = editor;
            this.editorDraftId = key;
        } catch (error) {
            if (isCurrent()) {
                this.state.error = error.message || _t("Editor failed to load.");
                this.state.editorMode = "source";
                retry = mode === "visual";
            }
        } finally {
            this.editorMounting = false;
            if (retry && !this.destroyed) this.syncEditor();
        }
    }

    async toggleEditor() {
        if (!this.state.draft || this.state.draft.state !== "editing" || this.state.uploadingAsset || this.editorMounting) return;
        this.flushEditor();
        this.state.editorMode = this.state.editorMode === "source" ? "visual" : "source";
        this.preferredEditorMode = this.state.editorMode;
        this.persistSidebar();
    }

    flushEditor() {
        if (!this.editor || this.composing || this.state.mode !== "edit" || this.state.draft?.state !== "editing") return;
        const source = this.editor.getMarkdown();
        if (source !== this.state.draft.markdown) this.changeField("markdown", { target: { value: source } });
    }

    changeField(field, event, composing = false) {
        if (!this.state.draft) return;
        this.state.draft[field] = event.target.value;
        this.state.dirty = true;
        this.state.publishOperationUUID = null;
        this.state.status = _t("Unsaved changes");
        this.composing = composing;
        if (composing) return;
        if (field === "markdown") {
            clearTimeout(this.previewTimer);
            this.previewTimer = setTimeout(() => {
                if (!this.destroyed && this.state.mode === "edit") this.run(() => this.preview());
            }, 450);
        }
        clearTimeout(this.autosaveTimer);
        this.autosaveTimer = setTimeout(() => {
            if (!this.destroyed && this.state.dirty) this.save();
        }, 2000);
        if (!this.maxAutosaveTimer) {
            this.maxAutosaveTimer = setTimeout(() => {
                this.maxAutosaveTimer = null;
                if (!this.destroyed && this.state.dirty) this.save();
            }, 15000);
        }
    }

    async save() {
        if (this.savePromise) return this.savePromise;
        this.flushEditor();
        if (!this.state.draft || !this.state.dirty || this.state.saving || this.composing) return;
        this.savePromise = this.saveCurrentDraft();
        try {
            await this.savePromise;
        } finally {
            this.savePromise = null;
        }
    }

    async saveCurrentDraft() {
        clearTimeout(this.autosaveTimer);
        clearTimeout(this.maxAutosaveTimer);
        this.maxAutosaveTimer = null;
        const draft = this.state.draft;
        const navigation = this.navigationVersion;
        const isCurrent = () => !this.destroyed && this.navigationVersion === navigation && this.state.draft?.uuid === draft.uuid;
        const snapshot = { title: draft.title, summary: draft.summary, markdown: draft.markdown, tag_names: draft.tag_names };
        this.state.saving = true;
        this.state.status = _t('Saving draft…');
        try {
            const result = await this.orm.call('qing.knowledge.draft', 'save_draft', [
                [draft.id], draft.draft_version, snapshot.title, snapshot.summary, snapshot.markdown,
                (snapshot.tag_names || '').split(',').map(tag => tag.trim()).filter(Boolean),
            ]);
            if (!isCurrent()) return;
            draft.draft_version = result.draft_version;
            draft.content_hash = result.content_hash;
            this.state.dirty = draft.title !== snapshot.title || draft.summary !== snapshot.summary
                || draft.markdown !== snapshot.markdown || draft.tag_names !== snapshot.tag_names;
            this.state.saveError = '';
            this.state.status = this.state.dirty ? _t('Unsaved changes') : _t('Draft saved');
        } catch (error) {
            if (isCurrent()) {
                this.state.saveError = error.data?.message || error.message || _t('Save failed.');
                this.state.status = _t('Save failed. Your local changes are preserved.');
                this.notification.add(this.state.saveError, { type: 'danger' });
            }
        } finally {
            this.state.saving = false;
        }
        if (!isCurrent() || this.state.saveError) return;
        await this.run(async () => {
            await this.preview();
            await this.loadMyDrafts();
        });
        if (this.state.dirty) this.autosaveTimer = setTimeout(() => this.save(), 2000);
    }

    async preview() {
        if (!this.state.draft || (this.state.mode === "edit" && !this.previewVisible)) return;
        const draftId = this.state.draft.id;
        const source = this.state.draft.markdown;
        const html = await this.orm.call("qing.knowledge.draft", "preview_markdown", [
            [draftId], source,
        ]);
        if (this.state.draft?.id === draftId && this.state.draft.markdown === source && this.state.mode !== "read") {
            this.state.preview = html;
        }
    }

    async uploadAsset(event) {
        const file = event.target.files?.[0];
        const target = this.assetUploadTarget;
        this.assetUploadTarget = null;
        event.target.value = "";
        if (!file || !target || this.state.uploadingAsset || this.state.draft?.state !== "editing"
            || this.state.draft.uuid !== target.draftUuid || this.editor !== target.editor || this.state.mode !== "edit") {
            target?.editor.releaseSelection();
            return;
        }
        this.state.uploadingAsset = true;
        try {
            await this.run(async () => {
                if (file.size > 20 * 1024 * 1024) throw new Error(_t("Asset exceeds 20 MiB."));
                const encoded = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
                    reader.onerror = () => reject(new Error(_t("The selected file could not be read.")));
                    reader.readAsDataURL(file);
                });
                const result = await this.orm.call("qing.knowledge.asset", "upload_to_draft", [
                    target.draftUuid, file.name, encoded,
                ]);
                if (this.destroyed || this.state.draft?.uuid !== target.draftUuid
                    || this.state.mode !== "edit" || this.editor !== target.editor) return;
                const inserted = target.editor.insertAsset({
                    path: result.path, name: file.name, isImage: /\.(png|jpe?g|gif|webp)$/i.test(result.path),
                }, target.selection);
                if (!inserted) return;
                const source = target.editor.getMarkdown();
                if (this.state.draft.markdown !== source) {
                    this.changeField("markdown", { target: { value: source } });
                }
                this.notification.add(_t("Asset added to the draft."), { type: "success" });
            });
        } finally {
            target.editor.releaseSelection();
            this.state.uploadingAsset = false;
        }
    }

    async submit() {
        this.flushEditor();
        const draft = this.state.draft;
        const navigation = this.navigationVersion;
        const isCurrent = () => !this.destroyed && this.navigationVersion === navigation
            && this.state.draft?.uuid === draft?.uuid && this.state.mode === "edit" && draft?.state === "editing";
        if (!isCurrent() || this.state.uploadingAsset) return;
        // Wait for an in-flight autosave, then save any edits made during that request.
        await this.save();
        if (!isCurrent()) return;
        this.flushEditor();
        if (this.state.dirty && !this.state.error && !this.state.saveError) await this.save();
        if (!isCurrent()) return;
        this.flushEditor();
        if (this.state.dirty || this.composing) return;
        await this.run(async () => {
            const result = await this.orm.call("qing.knowledge.draft", "submit_draft", [[draft.id], draft.draft_version]);
            if (!isCurrent()) return;
            draft.draft_version = result.draft_version;
            draft.content_hash = result.content_hash;
            draft.state = "submitted";
            this.state.status = _t("Submitted for review");
            await this.preview();
            await this.loadArticles();
            await this.loadMyDrafts();
        });
    }

    async withdrawSubmission() {
        const draft = this.state.draft;
        if (!draft || draft.state !== "submitted" || this.state.mode !== "edit") return;
        await this.run(async () => {
            const version = await this.orm.call("qing.knowledge.draft", "withdraw_draft", [
                [draft.id], draft.draft_version,
            ]);
            if (this.state.draft?.id !== draft.id) return;
            draft.draft_version = version;
            draft.state = "editing";
            draft.content_hash = false;
            this.state.mode = "edit";
            this.state.status = _t("Editing resumed");
            await this.loadArticles();
            await loadBundle("qing_knowledge.editor");
            this.state.editorReady = true;
            await this.preview();
        });
    }

    async publish() {
        if (!this.state.draft || this.state.draft.state !== "submitted") return;
        await this.run(async () => {
            const draft = this.state.draft;
            this.state.publishOperationUUID ||= crypto.randomUUID();
            await this.orm.call("qing.knowledge.draft", "publish_draft", [
                [draft.id], draft.draft_version, this.state.article?.revision_uuid || false,
                draft.content_hash, this.state.publishOperationUUID,
            ]);
            this.state.publishOperationUUID = null;
            await this.loadArticles();
            await this.openArticle(this.state.articleId);
        });
    }

    async openSubmission(draftId) {
        await this.run(async () => {
            const [draft] = await this.orm.searchRead("qing.knowledge.draft", [["id", "=", draftId]],
                ["title", "summary", "markdown", "tag_names", "draft_version", "state", "content_hash", "uuid", "article_id"]);
            if (!draft) return;
            await this.openArticle(draft.article_id[0], false);
            if (this.state.articleId !== draft.article_id[0] || !this.state.article) return;
            this.state.navigation = "article";
            this.state.status = _t("Submitted for review");
            this.state.draft = draft;
            this.state.mode = "review";
            this.state.preview = await this.orm.call("qing.knowledge.draft", "preview_markdown", [[draft.id], draft.markdown]);
            this.state.diff = await this.orm.call("qing.knowledge.article", "compare_submission", [
                [this.state.articleId], draft.id,
            ]);
        });
    }

    async search(event) {
        this.state.query = event.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.run(() => this.updateSearch()), 250);
    }

    async importFile(event) {
        const file = event.target.files?.[0];
        if (!file || !this.state.spaceId) return;
        const key = `${this.state.spaceId}:${file.name}:${file.size}:${file.lastModified}`;
        if (this.importOperation?.key !== key) {
            this.importOperation = { key, uuid: crypto.randomUUID() };
        }
        const operationUUID = this.importOperation.uuid;
        await this.run(async () => {
            if (file.name.toLowerCase().endsWith(".md")) {
                const source = await file.text();
                await this.orm.call("qing.knowledge.space", "import_markdown", [
                    [this.state.spaceId], file.name.replace(/\.md$/i, ""), source, operationUUID,
                ]);
            } else if (file.name.toLowerCase().endsWith(".zip")) {
                if (file.size > 50 * 1024 * 1024) throw new Error(_t("ZIP exceeds 50 MiB."));
                const encoded = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
                    reader.onerror = reject;
                    reader.readAsDataURL(file);
                });
                await this.orm.call("qing.knowledge.space", "import_package", [
                    [this.state.spaceId], encoded, operationUUID,
                ]);
            } else {
                throw new Error(_t("Choose a Markdown or ZIP file."));
            }
            await this.loadArticles();
            this.importOperation = null;
            this.notification.add(_t("Imported as personal drafts. Review before publishing."), { type: "success" });
        });
        event.target.value = "";
    }

    _download(content, mime, filename) {
        const url = URL.createObjectURL(new Blob([content], { type: mime }));
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    exportMarkdown() {
        if (!this.state.article?.published) return;
        this._download(this.state.article.markdown, "text/markdown;charset=utf-8", `${this.state.article.uuid}.md`);
    }

    async exportPackage() {
        if (!this.state.article?.published) return;
        await this.run(async () => {
            const encoded = await this.orm.call("qing.knowledge.space", "export_package", [
                [this.state.spaceId], [this.state.article.uuid],
            ]);
            const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
            this._download(bytes, "application/zip", `${this.state.article.uuid}.zip`);
        });
    }

    get currentSpaceName() {
        return this.state.article?.space_name || this.state.spaces.find(space => space.id === this.state.spaceId)?.name || _t('Knowledge');
    }

    get canPublishDirectly() {
        return this.state.article?.can_publish && this.state.article.publication_policy === 'direct';
    }

    get publicationLabel() {
        return this.canPublishDirectly ? _t('Publish') : _t('Submit for review');
    }

    get listTitle() {
        return {
            home: _t('Home'), recent: _t('Recent articles'), favorites: _t('Favorites'), drafts: _t('My drafts'),
            trash: _t('Trash'), review: _t('Review queue'),
        }[this.state.navigation] || _t('Home');
    }

    get listItems() {
        const navigation = this.state.navigation;
        if (navigation === 'trash') {
            return this.state.trash.map(item => ({ ...item, kind: 'article', meta: item.space_name }));
        }
        if (navigation === 'drafts') return this.state.myDrafts.map(draft => this.draftItem(draft));
        if (navigation === 'review') return this.state.pending.map(draft => this.reviewItem(draft));
        return (navigation === 'favorites' ? this.state.favorites : this.state.recent).map(row => this.bookmarkItem(row));
    }

    get outlineItems() {
        if (this.state.mode === 'edit' && this.state.draft?.state === 'editing') return this.state.editorOutline;
        const html = this.state.mode === 'read' ? this.state.article?.html : this.state.preview;
        const document = new DOMParser().parseFromString(html || '', 'text/html');
        return [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((heading, index) => ({
            id: index, title: heading.textContent.trim() || _t('Untitled heading'), depth: Number(heading.tagName.slice(1)),
        }));
    }

    get pdfViewerUrl() {
        return this.state.pdfUrl ? `/web/static/lib/pdfjs/web/viewer.html?file=${encodeURIComponent(this.state.pdfUrl)}#page=1` : '';
    }

    get saveConflict() {
        return /DRAFT_CONFLICT|REVISION_CONFLICT/.test(this.state.saveError || this.state.error);
    }

    async saveBeforeNavigation() {
        this.flushEditor();
        if (this.state.dirty && !this.composing) {
            await this.save();
            this.flushEditor();
            if (this.state.dirty && !this.state.error && !this.state.saveError) await this.save();
        }
        return !this.state.dirty || window.confirm(_t('This draft could not be saved. Download a copy before discarding your changes. Leave anyway?'));
    }

    async navigateList(navigation) {
        if (!(await this.saveBeforeNavigation())) return;
        await this.destroyEditor();
        this.closeAddMenu();
        this.state.navigation = navigation;
        this.state.draft = null;
        this.state.article = null;
        this.state.articleId = null;
        this.state.dirty = false;
        this.state.mode = 'read';
        this.state.detailsOpen = false;
        this.state.pdfUrl = '';
        await this.run(async () => {
            await this.loadBookmarks();
            await this.loadMyDrafts();
            if (navigation === 'trash') await this.loadTrash();
            if (navigation === 'home') await this.loadSpaceCounts();
        });
    }

    async loadTrash() {
        const spaceId = this.state.spaceId;
        if (!spaceId || !this.canManageSpace) {
            this.state.trash = [];
            return;
        }
        const rows = await this.orm.searchRead('qing.knowledge.article',
            [['space_id', '=', spaceId], ['lifecycle', '=', 'trashed']],
            ['name', 'space_id'], { limit: MAX_TREE_ARTICLES, order: 'write_date desc, id desc' });
        if (spaceId !== this.state.spaceId || this.destroyed) return;
        this.state.trash = rows.map(article => ({
            id: article.id, article_id: article.id, title: article.name,
            space_name: article.space_id[1], state: 'trashed',
        }));
    }

    async loadMyDrafts() {
        const drafts = await this.orm.call('qing.knowledge.draft', 'list_my_drafts', ['', false, 100]);
        if (!this.destroyed) this.state.myDrafts = drafts;
    }

    async openOwnDraft(articleId) {
        await this.openArticle(articleId, false);
        if (this.state.articleId === articleId && this.state.article?.can_edit && this.state.mode === 'read') await this.edit();
    }

    async showPublished() {
        if (!(await this.saveBeforeNavigation())) return;
        await this.destroyEditor();
        this.state.mode = 'read';
        this.state.dirty = false;
    }

    async chooseEditorMode(mode) {
        if (this.state.uploadingAsset || this.state.publishing) return;
        if (this.state.mode === 'read') await this.edit();
        if (this.state.draft?.state !== 'editing') return;
        this.flushEditor();
        this.state.editorMode = mode;
        this.preferredEditorMode = mode;
        this.persistSidebar();
    }

    toggleOutline() {
        this.state.showOutline = !this.state.showOutline;
        this.persistSidebar();
    }

    scrollToHeading(index) {
        if (this.state.mode === 'edit' && this.state.draft?.state === 'editing') {
            if (this.editor?.scrollToHeading(index)) {
                this.state.activeOutlineId = index;
                return;
            }
        } else {
            const heading = this.workspaceRoot()?.querySelector(`#qing-knowledge-heading-${index}`);
            if (heading) {
                heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
                heading.setAttribute('tabindex', '-1');
                heading.focus({ preventScroll: true });
                this.state.activeOutlineId = index;
                return;
            }
        }
        this.notification.add(_t('This heading is not available yet. Wait for the editor to load and try again.'), { type: 'warning' });
    }

    openSearch() {
        this.focusSearch = true;
        this.state.searchOpen = true;
        this.run(() => this.updateSearch());
    }

    handleSearchKey(event) {
        if (event.key !== 'Tab') return;
        const dialog = event.currentTarget;
        const fields = [...dialog.querySelectorAll('button,input,select,textarea,summary,a[href]')].filter(field => !field.disabled && field.getClientRects().length);
        const first = fields[0], last = fields.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }

    closeSearch() {
        this.state.searchOpen = false;
        this.state.query = '';
        this.state.searchActive = 0;
    }

    get searchGroups() {
        let index = 0;
        const groups = [];
        if (this.state.searchTypes.published && this.state.searchResults.length) {
            groups.push({
                key: 'published', label: _t('Published articles'),
                items: this.state.searchResults.map(item => ({
                    key: `article:${item.uuid}`, kind: 'article', id: item.id, index: index++,
                    title: item.title, summary: item.summary, meta: item.space_name,
                })),
            });
        }
        if (this.state.searchTypes.drafts && this.state.draftResults.length) {
            groups.push({
                key: 'drafts', label: _t('My drafts'),
                items: this.state.draftResults.map(draft => ({ ...this.draftItem(draft), index: index++, summary: draft.summary })),
            });
        }
        return groups;
    }

    get searchItems() {
        return this.searchGroups.flatMap(group => group.items);
    }

    get createFromSearchLabel() {
        return _t('Create “%s” as a new article', this.state.query.trim());
    }

    setSearchScope(scope) {
        if (this.state.searchScope === scope) return;
        this.state.searchScope = scope;
        this.run(() => this.updateSearch());
    }

    toggleSearchType(type) {
        this.state.searchTypes = { ...this.state.searchTypes, [type]: !this.state.searchTypes[type] };
        this.state.searchActive = 0;
    }

    onSearchInputKeydown(event) {
        const items = this.searchItems;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!items.length) return;
            const step = event.key === 'ArrowDown' ? 1 : -1;
            this.state.searchActive = (this.state.searchActive + step + items.length) % items.length;
            this.scrollActiveHit = true;
        } else if (event.key === 'Enter' && !event.isComposing) {
            const item = items[this.state.searchActive];
            if (!item) return;
            event.preventDefault();
            this.openSearchItem(item);
        }
    }

    openSearchItem(item) {
        if (item.kind === 'draft') return this.openOwnDraft(item.article_id);
        return this.openArticle(item.id);
    }

    createFromSearch() {
        const title = this.state.query.trim();
        if (!title || !this.canCreateArticle) return;
        this.closeSearch();
        this.prepareNewArticle(null);
        this.state.newTitle = title;
        this.state.newTemplate = 'blank';
    }

    async updateSearch() {
        const query = this.state.query;
        const scope = this.state.searchScope;
        const spaceId = this.state.spaceId;
        const space = this.state.spaces.find(item => item.id === spaceId);
        const uuid = scope === 'space' ? space?.uuid : false;
        const [published, drafts] = await Promise.all([
            this.orm.call('qing.knowledge.article', 'search_articles', [query, uuid, 40, 0]),
            this.orm.call('qing.knowledge.draft', 'list_my_drafts', [query, uuid, 40]),
        ]);
        if (!this.state.searchOpen || query !== this.state.query || scope !== this.state.searchScope || spaceId !== this.state.spaceId) return;
        this.state.searchResults = published;
        this.state.draftResults = drafts;
        this.state.searchActive = 0;
    }

    changeSearchScope(event) {
        this.state.searchScope = event.target.value;
        this.run(() => this.updateSearch());
    }

    manageSpaces() {
        return this.action.doAction('qing_knowledge.action_knowledge_spaces');
    }

    manageArticles() {
        return this.action.doAction('qing_knowledge.action_knowledge_articles');
    }

    confirmPublication() {
        this.dialog.add(ConfirmationDialog, {
            title: this.state.draft?.state === 'submitted' || this.canPublishDirectly ? _t('Publish article') : _t('Submit for review'),
            body: this.state.draft?.state === 'submitted' || this.canPublishDirectly
                ? _t('Publish a new version for authorized space members? Your later edits will remain personal drafts.')
                : _t('Submit this saved draft to the space publishers for review?'),
            confirmLabel: this.state.draft?.state === 'submitted' || this.canPublishDirectly ? _t('Publish') : _t('Submit for review'),
            confirm: async () => {
                this.state.publishing = true;
                try {
                    return await (this.state.draft?.state === 'submitted' ? this.publish()
                        : this.canPublishDirectly ? this.publishDirectly() : this.submit());
                } finally { this.state.publishing = false; }
            },
        });
    }

    async publishDirectly() {
        if (!this.canPublishDirectly || this.state.draft?.state !== 'editing' || this.state.uploadingAsset || this.composing) return;
        this.flushEditor();
        this.state.publishing = true;
        try {
            await this.save();
            this.flushEditor();
            if (this.state.dirty && !this.state.error && !this.state.saveError) await this.save();
            if (this.state.dirty || this.state.error || this.state.saveError || this.composing) return;
            const draft = this.state.draft;
            this.state.publishOperationUUID ||= crypto.randomUUID();
            await this.run(async () => {
                await this.orm.call('qing.knowledge.draft', 'publish_direct', [
                    [draft.id], draft.draft_version, draft.base_revision_uuid || false,
                    draft.content_hash, this.state.publishOperationUUID,
                ]);
                this.state.publishOperationUUID = null;
                await this.loadArticles();
                await this.loadMyDrafts();
                await this.openArticle(this.state.articleId, false);
            });
        } finally {
            this.state.publishing = false;
        }
    }

    downloadDraftCopy() {
        this.flushEditor();
        if (!this.state.draft) return;
        this._download(this.state.draft.markdown || '', 'text/markdown;charset=utf-8',
            `${(this.state.draft.title || 'draft').replace(/[\/:*?"<>|]/g, '_')}.md`);
    }

    async reloadDraft() {
        if (this.state.dirty && !window.confirm(_t('Reloading replaces your local edits. Download a copy first. Continue?'))) return;
        this.state.dirty = false;
        this.state.error = '';
        this.state.saveError = '';
        const id = this.state.articleId;
        await this.openArticle(id, false);
        await this.edit();
    }

    onContentClick(event) {
        const anchor = event.target.closest?.('a[href]');
        if (!anchor || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
        let url;
        try { url = new URL(anchor.getAttribute('href'), window.location.origin); } catch { return; }
        if (url.origin !== window.location.origin || !CONTROLLED_PDF.test(url.pathname)) return;
        event.preventDefault();
        this.state.pdfName = anchor.textContent;
        this.state.pdfUrl = url.pathname;
    }

    onEditorDragOver(event) {
        if (!hasDraggedFiles(event) || this.state.draft?.state !== 'editing') return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = 'copy';
    }

    onEditorDrop(event) {
        if (!hasDraggedFiles(event) || this.state.draft?.state !== 'editing') return;
        event.preventDefault();
        event.stopPropagation();
        this.uploadEditorFiles([...event.dataTransfer.files], this.editor?.captureSelectionAt?.(event));
    }

    onEditorPaste(event) {
        const files = [...(event.clipboardData?.files || [])];
        if (!files.length || this.state.draft?.state !== 'editing') return;
        event.preventDefault();
        event.stopPropagation();
        this.uploadEditorFiles(files, this.editor?.captureSelection());
    }

    uploadEditorFiles(files, selection) {
        if (files.length !== 1) {
            this.notification.add(_t('Insert one image or PDF at a time.'), { type: 'warning' });
            this.editor?.releaseSelection();
            return;
        }
        if (!this.editor || this.state.uploadingAsset || this.state.publishing) return;
        this.assetUploadTarget = { draftUuid: this.state.draft.uuid, editor: this.editor, selection };
        return this.uploadAsset({ target: { files, value: '' } });
    }

}

registry.category("actions").add("qing_knowledge.workspace", KnowledgeWorkspace);
