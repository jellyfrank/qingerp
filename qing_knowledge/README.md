# qing_knowledge

Private Markdown knowledge spaces for QingERP, depending on `qing_base` and the matching `mommy_base` foundation.

## Document workspace (20.0.1.0.10)

- A single collapsible, resizable sidebar provides spaces, article hierarchy, recent reading, favorites and personal drafts. Native management actions remain available at the bottom.
- New articles start in a centered dialog with title, permitted-space selection and template cards; parent placement and additional templates remain available.
- Published articles open in reading mode. New drafts open in visual mode; users can choose Markdown source and optional split preview. Mode and sidebar preferences are remembered per account/browser.
- A 900px document column aligns title, metadata, toolbar and body. Insert offers tables, reminders, code and dividers; Attachment uploads controlled images/PDFs at the captured cursor. Body-block insertion inside a table preserves it.
- One inline title leaves the main area for writing. Summary, tags, access, parent, lifecycle, export and immutable revision history live in the details drawer. The outline and focus controls are optional.
- Autosave reports saving, saved and failed states. Failed saves retain local text and offer retry, a Markdown copy and reload. Navigating away attempts to save first. Restoring a revision asks before replacing the current draft.
- Search covers accessible published articles and, separately, only the caller's own accessible drafts. Global/current-space scopes do not broaden record access.
- Images and PDFs use controlled assets. Paste/drop or the upload command inserts an editable Markdown asset reference. PDF links open the native PDF.js viewer in a side panel; download remains available. Asset routes recheck current permissions and use private/no-store responses.
- A space defaults to review before publication. Managers may enable direct publication for publishers/managers; ordinary editors still submit for review. Both paths retain version, content-hash, base-revision, membership and idempotency checks. A draft based on an older published revision cannot overwrite a newer one.

Markdown remains the source of truth. Viewing or switching an untouched supported document preserves the original source. The visual adapter compares semantic Markdown trees, accepting serializer-only formatting changes and empty visual paragraphs; unsupported constructs fall back to source mode. Code whitespace, table content and asset destinations remain significant.

Existing Markdown/ZIP import/export, template creation, article restrictions and hierarchy changes are retained. Tree lazy loading, sibling sorting, cross-space moves, import reports, real-time collaboration and expanded AI/RAG work are outside this revision. The existing 2,000-article tree limit and bounded search remain explicit.

## Build and verification

Use the committed frontend lockfile: `cd frontend && npm ci && npm test && npm run build`. The server does not require Node or a CDN; deploy the committed `static/lib` bundle. Python dependencies remain in the repository requirements. Keep the build's `--supported:template-literal=false` option: the native Odoo minifier can remove whitespace inside nested template literals emitted by esbuild. Verify the actual served minified editor bundle after rebuilding.

Run Odoo with an isolated database and `--test-enable --test-tags /qing_knowledge --stop-after-init`. See `VALIDATION.md` for this revision's evidence and limits.

Upgrade `qing_knowledge` when releasing this version so the publication-policy field and navigation records are loaded, and refresh the web/editor assets. Existing spaces keep the review policy. Production delivery follows the project's scoped SaaS API process; a feature-branch push alone does not update a tenant.


## 高亮提示框与文字格式

在正文工具条点击“高亮提示框”，可以将当前段落变成醒目的提醒。空白段落会插入可直接修改的提示文字；已有提示框中再次点击会移除提示框并保留内容。也可以输入 `/高亮` 后按 Enter，或通过“插入 → 高亮提示框”添加。

提示框保存为标准 Markdown 引用，例如 `> **提醒**：先核对数量，再确认入库。`，可继续在源码中修改，也会随 Markdown 导出保留。加粗、斜体、删除线和行内代码在正文工具条直接使用。

开发注意：编辑器构建保留 `--supported:template-literal=false`；发布前验证 Odoo 实际压缩资源。当前 runtime 的 rjsmin 会改变包含裸空格的正则，提示框标记检测使用字符串方法避免二次压缩改变语义。

## Article trash (20.0.1.0.12)

Moving an article to trash removes it from the normal workspace tree, recent/favorite lists, personal draft results and review queue. The workspace switches to the current space's Trash list instead of reopening the removed article. Space managers can open Trash from the sidebar footer and restore an article; drafts and immutable revisions are retained. Restoration keeps the existing archived-state policy and does not republish the article. Trashed articles are displayed as Trash and have no editing controls until restored. Article-record management remains available under the existing permissions.
