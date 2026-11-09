import { Crepe } from '@milkdown/crepe';
import { preservesContent } from './roundtrip';
import { activeHeading, markdownOutline, visualOutline } from './outline';
import { sourceInsertionPosition } from './insertion';
import { sourceFormatChange } from './formatting';
import { commandsCtx, editorViewCtx, parserCtx, remarkCtx } from '@milkdown/kit/core';
import { uploadConfig } from '@milkdown/kit/plugin/upload';
import { imageSchema, linkSchema, strongSchema, emphasisSchema, inlineCodeSchema,
    toggleStrongCommand, toggleEmphasisCommand, toggleInlineCodeCommand, wrapInBlockquoteCommand } from '@milkdown/kit/preset/commonmark';
import { strikethroughSchema, toggleStrikethroughCommand } from '@milkdown/kit/preset/gfm';
import { lift } from '@milkdown/kit/prose/commands';
import { Plugin, TextSelection } from '@milkdown/kit/prose/state';
import { $prose, insert as insertMarkdownAtSelection } from '@milkdown/kit/utils';
import { EditorView, basicSetup } from 'codemirror';
import { markdown } from '@codemirror/lang-markdown';
import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';

const ASSET_PATH = /^assets\/[a-f0-9]{64}\.(?:png|jpe?g|gif|webp|pdf)$/;
const IMAGE_PATH = /^assets\/[a-f0-9]{64}\.(?:png|jpe?g|gif|webp)$/;
const CALLOUT_ICON = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg>';
const UPLOAD_ICON = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 16V4m-5 5 5-5 5 5M5 15v5h14v-5"/></svg>';

function assetLabel(name) {
    return String(name || 'File').replace(/[\r\n\u0000-\u001f]+/g, ' ').trim() || 'File';
}

function assetMarkdown(asset) {
    if (!ASSET_PATH.test(asset.path) || (asset.isImage && !IMAGE_PATH.test(asset.path))) {
        throw new Error('Unsupported knowledge asset path.');
    }
    const label = assetLabel(asset.name).replace(/[\\\[\]]/g, '\\$&');
    return `${asset.isImage ? '!' : ''}[${label}](${asset.path})`;
}

function assetUrl(path, options) {
    if (!ASSET_PATH.test(path)) return '';
    const resolved = options.resolveAssetUrl?.(path);
    if (typeof resolved !== 'string' || !resolved.startsWith('/') || resolved.startsWith('//')) return '';
    const url = new URL(resolved, window.location.href);
    return url.origin === window.location.origin ? url.href : '';
}

window.QingKnowledgeEditors = {
    source(root, value, onChange, options = {}) {
        let pendingSelection = null;
        let destroyed = false;
        let outline = markdownOutline(value);
        const notifyOutline = (state) => options.onOutlineChange?.(outline, activeHeading(outline, state.selection.main.head));
        const view = new EditorView({
            doc: value,
            parent: root,
            extensions: [
                basicSetup,
                markdown(),
                EditorView.lineWrapping,
                EditorView.updateListener.of((update) => {
                    if (pendingSelection && update.docChanged) {
                        pendingSelection.position = update.changes.mapPos(pendingSelection.position, 1);
                    }
                    if (update.docChanged) {
                        const source = update.state.doc.toString();
                        outline = markdownOutline(source);
                        onChange(source, update.view.composing);
                    }
                    if (update.docChanged || update.selectionSet) notifyOutline(update.state);
                }),
            ],
        });
        view.dom.addEventListener('compositionend', () => {
            queueMicrotask(() => {
                if (!destroyed) onChange(view.state.doc.toString(), false);
            });
        });
        notifyOutline(view.state);
        return {
            format: (kind) => {
                if (destroyed) return false;
                const { from, to } = view.state.selection.main;
                const change = sourceFormatChange(view.state.doc.toString(), from, to, kind, kind === 'quote' ? options.labels?.calloutText : options.labels?.textGroup);
                if (!change) return false;
                view.dispatch({ changes: { from: change.from, to: change.to, insert: change.insert },
                    selection: { anchor: change.anchor, head: change.head }, scrollIntoView: true });
                view.focus();
                return true;
            },
            getMarkdown: () => view.state.doc.toString(),
            getOutline: () => outline,
            scrollToHeading: (index) => {
                const heading = outline[index];
                if (destroyed || !heading) return false;
                view.dispatch({
                    selection: { anchor: heading.position },
                    effects: EditorView.scrollIntoView(heading.position, { y: 'start', yMargin: 24 }),
                });
                view.focus();
                return true;
            },
            captureSelection: () => (pendingSelection = { position: view.state.selection.main.head }),
            captureSelectionAt: (event) => {
                const position = view.posAtCoords({ x: event.clientX, y: event.clientY });
                if (position !== null) view.dispatch({ selection: { anchor: position } });
                return (pendingSelection = { position: view.state.selection.main.head });
            },
            releaseSelection: () => { pendingSelection = null; },
            insertMarkdown: (markdown, selection = null) => {
                if (destroyed || (selection && selection !== pendingSelection)) return false;
                const position = sourceInsertionPosition(view.state.doc.toString(), selection?.position ?? view.state.selection.main.head);
                const text = `${position ? '\n\n' : ''}${markdown}\n\n`;
                pendingSelection = null;
                view.dispatch({ changes: { from: position, insert: text }, selection: { anchor: position + text.length }, scrollIntoView: true });
                view.focus();
                return true;
            },
            insertAsset: (asset, selection = null) => {
                if (destroyed || (selection && selection !== pendingSelection)) return false;
                const text = assetMarkdown(asset);
                const position = selection?.position ?? view.state.selection.main.head;
                pendingSelection = null;
                view.dispatch({
                    changes: { from: position, insert: text },
                    selection: { anchor: position + text.length },
                    scrollIntoView: true,
                });
                view.focus();
                return true;
            },
            focus: () => view.focus(),
            destroy: () => {
                destroyed = true;
                pendingSelection = null;
                view.destroy();
            },
        };
    },

    async visual(root, value, onChange, options = {}) {
        let contentChanged = false;
        const readMarkdown = () => contentChanged ? crepe.getMarkdown() : value;
        let composing = false;
        let ready = false;
        let destroyed = false;
        let pendingSelection = null;
        let outline = [];
        const formatState = (state) => {
            const result = {};
            for (const [name, schema] of Object.entries({ bold: strongSchema, italic: emphasisSchema, strike: strikethroughSchema, code: inlineCodeSchema })) {
                const type = crepe.editor.action(ctx => schema.type(ctx));
                result[name] = state.selection.empty
                    ? !!type.isInSet(state.storedMarks || state.selection.$from.marks())
                    : state.doc.rangeHasMark(state.selection.from, state.selection.to, type);
            }
            result.quote = Array.from({ length: state.selection.$from.depth }, (_, index) => state.selection.$from.node(index + 1))
                .some(node => node.type.name === 'blockquote');
            return result;
        };
        const notifyOutline = (state) => {
            options.onOutlineChange?.(outline, activeHeading(outline, state.selection.head));
            options.onFormatChange?.(formatState(state));
        };
        const captureSelection = () => crepe.editor.action((ctx) => {
            const view = ctx.get(editorViewCtx);
            pendingSelection = { position: view.state.selection.head };
            return pendingSelection;
        });
        const onStart = () => { composing = true; };
        const onEnd = () => {
            composing = false;
            queueMicrotask(() => {
                if (ready && !destroyed) onChange(readMarkdown(), false);
            });
        };
        const resolveAssetLink = (event) => {
            const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null;
            const path = anchor?.getAttribute('href');
            if (!path || !ASSET_PATH.test(path)) return;
            // Milkdown's link tooltip reads the canonical mark, outside schema.toDOM.
            // Resolve only its navigable DOM href; editing/copying still uses the asset path.
            const url = assetUrl(path, options);
            if (url) anchor.setAttribute('href', url);
            else event.preventDefault();
        };
        const insertCallout = (ctx, removeSlash = false) => {
            const view = ctx.get(editorViewCtx);
            if (removeSlash) {
                const { $from } = view.state.selection;
                if ($from.parent.textBetween(0, $from.parentOffset).startsWith('/')) {
                    view.dispatch(view.state.tr.delete($from.start(), $from.pos));
                }
            }
            if (!view.state.selection.$from.parent.content.size) {
                const from = view.state.selection.from;
                const text = options.labels?.calloutText || 'Note: add important conditions or reminders here.';
                const transaction = view.state.tr.insertText(text);
                transaction.setSelection(TextSelection.create(transaction.doc, from, from + text.length));
                view.dispatch(transaction);
            }
            const changed = ctx.get(commandsCtx).call(wrapInBlockquoteCommand.key);
            view.focus();
            return changed;
        };
        const crepe = new Crepe({
            root,
            defaultValue: value,
            features: { [Crepe.Feature.ImageBlock]: false },
            featureConfigs: {
                [Crepe.Feature.Placeholder]: { text: options.labels?.placeholder },
                [Crepe.Feature.Toolbar]: {
                    boldLabel: options.labels?.bold, italicLabel: options.labels?.italic,
                    strikethroughLabel: options.labels?.strike, codeLabel: options.labels?.inlineCode,
                    linkLabel: options.labels?.link,
                },
                [Crepe.Feature.BlockEdit]: {
                    textGroup: {
                        label: options.labels?.textGroup,
                        text: { label: options.labels?.paragraph },
                        ...Object.fromEntries([1, 2, 3, 4, 5, 6].map(level => [`h${level}`, { label: options.labels?.[`h${level}`] }])),
                        quote: { label: options.labels?.quote, icon: CALLOUT_ICON },
                        divider: { label: options.labels?.divider },
                    },
                    listGroup: {
                        label: options.labels?.listGroup,
                        bulletList: { label: options.labels?.bulletList },
                        orderedList: { label: options.labels?.orderedList },
                        taskList: { label: options.labels?.taskList },
                    },
                    advancedGroup: {
                        label: options.labels?.advancedGroup,
                        codeBlock: { label: options.labels?.codeBlock },
                        table: { label: options.labels?.table },
                        image: null,
                        math: null,
                    },
                    buildMenu: (builder) => {
                        const quote = builder.getGroup('text').group.items.find(item => item.key === 'quote');
                        if (quote) quote.onRun = ctx => insertCallout(ctx, true);
                        if (!options.onUploadRequest) return;
                        builder.addGroup('knowledge-assets', options.labels?.attachments || 'Attachments')
                            .addItem('knowledge-upload', {
                                label: options.uploadLabel || 'Upload a file',
                                icon: UPLOAD_ICON,
                                onRun: (ctx) => {
                                    const view = ctx.get(editorViewCtx);
                                    const { $from } = view.state.selection;
                                    const text = $from.parent.textBetween(0, $from.parentOffset);
                                    // Remove only the slash query, never an existing paragraph.
                                    if (text.startsWith('/')) {
                                        view.dispatch(view.state.tr.delete($from.start(), $from.pos));
                                    }
                                    options.onUploadRequest(captureSelection());
                                },
                            });
                    },
                },
            },
        });
        crepe.editor.config((ctx) => {
            // Assets are uploaded through the draft RPC. Never create ephemeral blob URLs.
            ctx.update(uploadConfig.key, (config) => ({ ...config, uploader: async () => [] }));
            ctx.update(imageSchema.key, (original) => (context) => ({
                ...original(context),
                toDOM: (node) => {
                    const src = IMAGE_PATH.test(node.attrs.src) ? assetUrl(node.attrs.src, options) : '';
                    return ['img', {
                        ...(src ? { src } : {}),
                        alt: node.attrs.alt || '',
                        title: node.attrs.title || '',
                        'data-knowledge-asset': src ? node.attrs.src : '',
                    }];
                },
                parseDOM: [{
                    tag: 'img',
                    getAttrs: (dom) => ({
                        src: dom.getAttribute('data-knowledge-asset') || dom.getAttribute('src') || '',
                        alt: dom.getAttribute('alt') || '',
                        title: dom.getAttribute('title') || '',
                    }),
                }],
                parseMarkdown: {
                    match: (node) => node.type === 'image',
                    runner: (state, node, type) => state.addNode(type, {
                        src: node.url || '',
                        alt: node.alt || '',
                        // CommonMark's omitted title is null; the pinned schema requires a string.
                        title: node.title || '',
                    }),
                },
            }));
            ctx.update(linkSchema.key, (original) => (context) => {
                const schema = original(context);
                return {
                    ...schema,
                    toDOM: (mark) => {
                        // Preserve Milkdown's protocol sanitizer for ordinary links.
                        const dom = schema.toDOM(mark);
                        if (ASSET_PATH.test(mark.attrs.href)) {
                            dom[1].href = assetUrl(mark.attrs.href, options);
                            dom[1]['data-knowledge-asset'] = mark.attrs.href;
                            if (mark.attrs.href.endsWith('.pdf')) {
                                dom[1].class = 'o_qing_knowledge_file_link';
                                dom[1]['data-knowledge-caption'] = options.filePreviewLabel || 'PDF · Click to preview';
                            }
                        }
                        return dom;
                    },
                    parseDOM: [{
                        tag: 'a[href]',
                        getAttrs: (dom) => ({
                            href: dom.getAttribute('data-knowledge-asset') || dom.getAttribute('href'),
                            title: dom.getAttribute('title'),
                        }),
                    }],
                };
            });
        });
        crepe.editor.use($prose(() => new Plugin({
            view: () => ({
                update: (view, previousState) => {
                    if (!ready || destroyed) return;
                    const changed = !previousState.doc.eq(view.state.doc);
                    if (changed) outline = visualOutline(view.state.doc);
                    if (changed || !previousState.selection.eq(view.state.selection) || previousState.storedMarks !== view.state.storedMarks) notifyOutline(view.state);
                },
            }),
            state: {
                init: () => null,
                apply: (transaction) => {
                    if (ready && transaction.docChanged) contentChanged = true;
                    if (pendingSelection && transaction.docChanged) {
                        pendingSelection.position = transaction.mapping.map(pendingSelection.position, 1);
                    }
                    return null;
                },
            },
        })));
        crepe.on((listener) => listener.markdownUpdated((_ctx, source) => {
            if (ready && !destroyed) onChange(contentChanged ? source : value, composing);
        }));
        await crepe.create();
        const unsupportedImage = crepe.editor.action((ctx) => {
            let unsupported = false;
            ctx.get(editorViewCtx).state.doc.descendants((node) => {
                if (node.type.name === 'image' && (!IMAGE_PATH.test(node.attrs.src) || !assetUrl(node.attrs.src, options))) {
                    unsupported = true;
                }
            });
            return unsupported;
        });
        const safeRoundtrip = crepe.editor.action(ctx => preservesContent(ctx.get(remarkCtx), value, crepe.getMarkdown()));
        if (unsupportedImage || !safeRoundtrip) {
            await crepe.destroy();
            return null;
        }
        ready = true;
        crepe.editor.action(ctx => {
            const { state } = ctx.get(editorViewCtx);
            outline = visualOutline(state.doc);
            notifyOutline(state);
        });
        root.addEventListener('compositionstart', onStart);
        root.addEventListener('compositionend', onEnd);
        root.addEventListener('pointerdown', resolveAssetLink, true);
        root.addEventListener('click', resolveAssetLink, true);
        return {
            format: (kind) => {
                if (destroyed) return false;
                return crepe.editor.action(ctx => {
                    const view = ctx.get(editorViewCtx);
                    const commands = { bold: toggleStrongCommand, italic: toggleEmphasisCommand,
                        strike: toggleStrikethroughCommand, code: toggleInlineCodeCommand, quote: wrapInBlockquoteCommand };
                    if (!commands[kind]) return false;
                    const active = formatState(view.state);
                    let changed;
                    if (kind === 'quote' && active.quote) {
                        changed = lift(view.state, view.dispatch);
                    } else if (kind === 'quote') {
                        changed = insertCallout(ctx);
                    } else if (kind === 'code' && view.state.selection.empty) {
                        const type = inlineCodeSchema.type(ctx);
                        view.dispatch(active.code ? view.state.tr.removeStoredMark(type) : view.state.tr.addStoredMark(type.create()));
                        changed = true;
                    } else {
                        changed = ctx.get(commandsCtx).call(commands[kind].key);
                    }
                    view.focus();
                    return changed;
                });
            },
            getMarkdown: readMarkdown,
            getOutline: () => outline,
            scrollToHeading: (index) => {
                const heading = outline[index];
                if (destroyed || !heading) return false;
                return crepe.editor.action(ctx => {
                    const view = ctx.get(editorViewCtx);
                    const cursor = TextSelection.near(view.state.doc.resolve(heading.position));
                    view.dispatch(view.state.tr.setSelection(cursor).scrollIntoView());
                    view.focus();
                    view.nodeDOM(heading.nodePosition)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    return true;
                });
            },
            captureSelection,
            captureSelectionAt: (event) => crepe.editor.action(ctx => {
                const view = ctx.get(editorViewCtx);
                const hit = view.posAtCoords({ left: event.clientX, top: event.clientY });
                if (hit) view.dispatch(view.state.tr.setSelection(TextSelection.near(view.state.doc.resolve(hit.pos))));
                return captureSelection();
            }),
            releaseSelection: () => { pendingSelection = null; },
            insertMarkdown: (markdown, selection = null) => {
                if (destroyed || (selection && selection !== pendingSelection)) return false;
                return crepe.editor.action(ctx => {
                    const view = ctx.get(editorViewCtx);
                    const position = selection?.position ?? view.state.selection.head;
                    const resolved = view.state.doc.resolve(Math.max(0, Math.min(position, view.state.doc.content.size)));
                    let tableDepth = 0;
                    for (let depth = resolved.depth; depth > 0; depth--) {
                        if (resolved.node(depth).type.name === 'table') { tableDepth = depth; break; }
                    }
                    pendingSelection = null;
                    if (tableDepth) {
                        // A body block must not split the current table or create a nested table.
                        const document = ctx.get(parserCtx)(markdown);
                        if (!document) return false;
                        const after = resolved.after(tableDepth);
                        const transaction = view.state.tr.insert(after, document.content);
                        transaction.setSelection(TextSelection.near(transaction.doc.resolve(after + document.content.size), -1));
                        view.dispatch(transaction.scrollIntoView());
                    } else {
                        view.dispatch(view.state.tr.setSelection(TextSelection.near(resolved)));
                        insertMarkdownAtSelection(markdown)(ctx);
                    }
                    view.focus();
                    return true;
                });
            },
            insertAsset: (asset, selection = null) => {
                if (destroyed || (selection && selection !== pendingSelection)) return false;
                assetMarkdown(asset);
                return crepe.editor.action((ctx) => {
                    const view = ctx.get(editorViewCtx);
                    const { state } = view;
                    const position = selection?.position ?? state.selection.head;
                    const resolved = state.doc.resolve(Math.max(0, Math.min(position, state.doc.content.size)));
                    const cursor = TextSelection.findFrom(resolved, 1, true) || TextSelection.findFrom(resolved, -1, true);
                    if (!cursor) return false;
                    const node = asset.isImage
                        ? imageSchema.type(ctx).create({ src: asset.path, alt: assetLabel(asset.name) })
                        : state.schema.text(assetLabel(asset.name), [linkSchema.type(ctx).create({ href: asset.path })]);
                    pendingSelection = null;
                    view.dispatch(state.tr.setSelection(cursor).replaceSelectionWith(node, false).scrollIntoView());
                    view.focus();
                    return true;
                });
            },
            focus: () => root.querySelector('[contenteditable="true"]')?.focus(),
            destroy: async () => {
                destroyed = true;
                ready = false;
                pendingSelection = null;
                root.removeEventListener('compositionstart', onStart);
                root.removeEventListener('compositionend', onEnd);
                root.removeEventListener('pointerdown', resolveAssetLink, true);
                root.removeEventListener('click', resolveAssetLink, true);
                await crepe.destroy();
            },
        };
    },
};
