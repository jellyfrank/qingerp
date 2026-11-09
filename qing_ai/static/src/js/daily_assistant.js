/** @odoo-module **/

import { Component, markup, onMounted, onPatched, onWillDestroy, onWillStart, proxy, signal, useProps } from "@odoo/owl";
import { Wysiwyg } from "@html_editor/wysiwyg";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDate } from "@web/core/l10n/dates";
import { editorToMarkdown } from "./composer_content";

export class DailyAssistant extends Component {
    static template = "qing_ai.DailyAssistant";
    static components = { Wysiwyg };
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.busService = useService("bus_service");
        this.compact = Boolean(this.props.compact);
        this.messagesRef = signal.ref();
        this.scrollPending = true;
        this.isAtBottom = true;
        this.runningRunId = null;
        this.state = proxy({
            conversations: [],
            conversation: null,
            view: this.compact ? "list" : "chat",
            search: "",
            draft: "",
            image: null,
            files: [],
            imageLoading: false,
            task: "ask",
            provider: "default",
            targetLanguage: "English",
            busy: false,
            sending: false,
            pendingMessage: null,
            streamText: "",
            error: "",
        });
        // Partial answers pushed over the bus while the request is still running.
        this.onStream = (payload) => this.applyStream(payload);
        this.busService.subscribe("qing_ai/stream", this.onStream);
        onWillDestroy(() => this.busService.unsubscribe("qing_ai/stream", this.onStream));
        this.editorConfig = {
            content: "",
            baseContainers: ["DIV", "P"],
            placeholder: _t("Type a message…"),
            resources: {
                on_will_paste_handlers: (_selection, event) => this.onFilePaste(event),
                should_bypass_paste_image_files_predicates: () => true,
            },
            onChange: () => this.onEditorChange(),
            onEditorReady: () => {
                this.editor.editable.addEventListener("keydown", (event) => this.onDraftKeydown(event));
                this.editor.editable.addEventListener("drop", (event) => this.onFileDrop(event));
            },
        };
        onWillStart(() => this.load());
        onMounted(() => this.scrollToBottom());
        onPatched(() => {
            if (this.scrollPending) {
                this.scrollPending = false;
                this.scrollToBottom();
            }
        });
    }

    async load() {
        try {
            this.state.conversations = await this.orm.call("qing.ai.conversation", "assistant_list", []);
            if (this.compact) {
                return;
            }
            const requestedId = Number(this.props.action?.params?.conversation_id);
            const id = requestedId || this.state.conversations[0]?.id;
            if (id) {
                await this.open(id);
            } else {
                await this.newConversation();
            }
        } catch (error) {
            this.showError(error);
        }
    }

    showError(error) {
        this.state.error = error.data?.message || _t("The AI request failed. Please try again.");
    }

    applyStream(payload) {
        if (!payload || !this.state.sending || this.state.conversation?.id !== payload.conversation_id) {
            return;
        }
        this.state.streamText = payload.text || "";
        this.scrollPending = this.isAtBottom;
    }

    applyConversation(conversation) {
        this.state.streamText = "";
        const existing = this.state.conversations.find(item => item.id === conversation.id);
        this.state.conversation = conversation;
        this.state.view = "chat";
        this.scrollPending = true;
        if (this.state.task === "business" && !conversation.business_enabled) {
            this.state.task = "ask";
        }
        this.state.provider = conversation.provider || "default";
        this.state.targetLanguage = conversation.target_language || "English";
        this.state.error = "";
        this.state.sending = Boolean(conversation.active_run);
        this.state.conversations = [
            {
                id: conversation.id,
                name: conversation.name,
                last_message_at: existing?.last_message_at || "",
            },
            ...this.state.conversations.filter(item => item.id !== conversation.id),
        ].slice(0, 50);
    }

    get filteredConversations() {
        const query = this.state.search.trim().toLocaleLowerCase();
        return query
            ? this.state.conversations.filter(item => item.name.toLocaleLowerCase().includes(query))
            : this.state.conversations;
    }

    get optionsSummary() {
        const tasks = {
            ask: _t("Ask"),
            summarize: _t("Summarize"),
            translate: _t("Translate"),
            business: _t("Business"),
        };
        const provider = this.state.conversation?.providers?.find(
            item => item.value === this.state.provider
        );
        const agent = this.state.conversation?.agent_name;
        return [agent, tasks[this.state.task], provider?.label || _t("Default Provider")]
            .filter(Boolean).join(" · ");
    }

    messageHtml(message) {
        return markup(message.content_html);
    }

    processStepMarker(step) {
        if (step.status === "ok" || step.status === "done") return "✓";
        if (step.status === "error" || step.status === "blocked") return "!";
        return "";
    }

    processStepText(step) {
        if (step.summary) return step.summary;
        if (step.status === "running") {
            return _t("Analyzing the request and selecting an allowed action");
        }
        if (step.status === "retry") {
            return _t("Adjusting the plan and trying again");
        }
        const tool = step.tool_label || _t("Business action");
        if (step.status === "ok" || step.status === "done") {
            return _t("Completed") + ": " + tool;
        }
        if (step.status === "blocked") {
            return _t("Skipped a repeated action") + ": " + tool;
        }
        return _t("Could not complete") + ": " + tool;
    }

    usageText(summary) {
        const values = [];
        const add = (label, value) => {
            if (typeof value === "number" && Number.isFinite(value)) {
                values.push(label + ": " + new Intl.NumberFormat().format(value));
            }
        };
        add(_t("Input"), summary?.input_tokens);
        add(_t("Cache hit"), summary?.cached_input_tokens);
        add(_t("Output"), summary?.output_tokens);
        add(_t("Reasoning"), summary?.reasoning_tokens);
        add(_t("Total tokens"), summary?.total_tokens);
        return values.join(" · ");
    }

    formatConversationDate(value) {
        return value ? formatDate(deserializeDateTime(value)) : "";
    }

    scrollToBottom() {
        const messages = this.messagesRef();
        if (messages) {
            messages.scrollTop = messages.scrollHeight;
            this.isAtBottom = true;
        }
    }

    onMessagesScroll(event) {
        const messages = event.currentTarget;
        this.isAtBottom = messages.scrollHeight - messages.clientHeight - messages.scrollTop < 24;
    }

    onMessageImageLoad() {
        if (this.isAtBottom) {
            this.scrollToBottom();
        }
    }

    deleteConversation(id) {
        if (this.state.busy) return;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete conversation"),
            body: _t("Delete this conversation, its messages and attachments permanently?"),
            confirmLabel: _t("Delete"),
            confirmClass: "btn-danger",
            confirm: () => this.confirmDeleteConversation(id),
        });
    }

    async confirmDeleteConversation(id) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            await this.orm.call("qing.ai.conversation", "assistant_delete", [[id]]);
            this.state.conversations = this.state.conversations.filter(item => item.id !== id);
            if (this.state.conversation?.id === id) {
                this.state.conversation = null;
                this.state.draft = "";
                this.state.image = null;
                this.state.files = [];
                this.clearEditor();
                if (this.compact) {
                    this.state.view = "list";
                }
            }
            this.state.error = "";
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async backToList() {
        if (this.state.busy) return;
        this.state.view = "list";
        this.state.error = "";
        await this.load();
    }

    openFullAssistant() {
        this.props.onOpenFull?.(this.state.conversation?.id);
    }

    async open(id) {
        if (this.state.busy) return;
        this.state.busy = true;
        let runId = null;
        try {
            const conversation = await this.orm.call("qing.ai.conversation", "assistant_open", [[id]]);
            this.applyConversation(conversation);
            runId = conversation.active_run?.id;
            this.state.draft = "";
            this.state.image = null;
            this.state.files = [];
            this.clearEditor();
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
        if (runId) {
            this.driveRun(runId, id);
        }
    }

    async newConversation(agentId = null) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const conversation = await this.orm.call("qing.ai.conversation", "assistant_new", agentId ? [agentId] : []);
            this.applyConversation(conversation);
            this.state.task = "ask";
            this.state.draft = "";
            this.state.image = null;
            this.state.files = [];
            this.clearEditor();
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async openAgentInDiscuss() {
        const agentId = this.state.conversation?.agent_id;
        if (!agentId || this.state.busy) return;
        this.state.busy = true;
        try {
            const action = await this.orm.call("qing.ai.agent", "action_open_discuss", [[agentId]]);
            await this.action.doAction(action);
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async selectAgent(event) {
        const agentId = Number(event.target.value);
        if (!agentId || this.state.busy || !this.state.conversation) return;
        if (this.state.conversation.messages?.length) {
            await this.newConversation(agentId);
            return;
        }
        this.state.busy = true;
        try {
            const conversation = await this.orm.call("qing.ai.conversation", "assistant_select_agent", [
                [this.state.conversation.id], agentId,
            ]);
            this.applyConversation(conversation);
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async send(event) {
        event?.preventDefault();
        const text = this.editor ? editorToMarkdown(this.editor.editable) : this.state.draft.trim();
        if ((!text && !this.state.image && !this.state.files.length) || !this.state.conversation || this.state.busy || this.state.sending || this.state.imageLoading) return;
        if (text.length > 4000) {
            this.state.error = _t("The AI message is too long.");
            return;
        }
        const args = [
            [this.state.conversation.id], text || (this.state.files.length ? _t("Analyze the attached files.") : _t("Describe this image.")), this.state.task,
            this.state.provider, this.state.targetLanguage,
            this.state.image && {
                name: this.state.image.name,
                data: this.state.image.data,
            },
            this.state.files.map(({ name, data }) => ({ name, data })),
            true,
            true,
        ];
        this.state.busy = true;
        this.state.sending = true;
        this.state.pendingMessage = {
            content: args[1],
            image_url: this.state.image?.url || "",
            files: this.state.files.map(({ name }) => ({ name })),
        };
        this.scrollPending = true;
        this.state.error = "";
        this.state.draft = "";
        this.state.image = null;
        this.state.files = [];
        this.clearEditor();
        let runId = null;
        const conversationId = this.state.conversation.id;
        try {
            const conversation = await this.orm.call("qing.ai.conversation", "assistant_send", args);
            this.applyConversation(conversation);
            runId = conversation.active_run?.id;
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.pendingMessage = null;
            this.state.sending = Boolean(runId);
            this.state.busy = false;
        }
        if (runId) {
            this.driveRun(runId, conversationId);
        }
    }

    async driveRun(runId, conversationId) {
        if (!runId || this.runningRunId === runId) return;
        this.runningRunId = runId;
        try {
            for (let step = 0; step < 12; step++) {
                if (this.state.conversation?.id !== conversationId) break;
                const conversation = await this.orm.call(
                    "qing.ai.conversation", "assistant_continue_run",
                    [[conversationId], runId, true],
                );
                if (this.state.conversation?.id !== conversationId) break;
                this.applyConversation(conversation);
                if (!conversation.active_run || conversation.active_run.id !== runId) break;
            }
        } catch (error) {
            if (this.state.conversation?.id === conversationId) {
                this.showError(error);
            }
        } finally {
            if (this.runningRunId === runId) {
                this.runningRunId = null;
            }
            if (this.state.conversation?.id === conversationId) {
                this.state.sending = Boolean(this.state.conversation?.active_run);
            }
        }
    }

    onDraftKeydown(event) {
        if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) {
            return;
        }
        if (event.altKey) {
            event.preventDefault();
            this.editor.shared.dom.insert(document.createElement("br"));
            this.editor.shared.history.commit();
            return;
        }
        if (event.shiftKey || event.ctrlKey || event.metaKey) {
            return;
        }
        event.preventDefault();
        this.send();
    }

    onEditorReady(editor) {
        this.editor = editor;
    }

    onEditorChange() {
        this.state.draft = editorToMarkdown(this.editor.editable).slice(0, 4001);
    }

    clearEditor() {
        if (!this.editor?.editable) return;
        this.editor.editable.replaceChildren(document.createElement("div"));
        this.editor.shared.history.reset();
    }

    async loadImage(file) {
        if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
            this.state.error = _t("Paste a PNG, JPEG or WebP image.");
            return;
        }
        if (file.size > 4 * 1024 * 1024) {
            this.state.error = _t("The image must be under 4 MB.");
            return;
        }
        this.state.imageLoading = true;
        try {
            const url = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(reader.error);
                reader.readAsDataURL(file);
            });
            const data = String(url).split(",", 2)[1];
            this.state.image = { name: file.name || "pasted-image", data, url };
            this.state.error = "";
        } catch {
            this.state.error = _t("The image could not be read.");
        } finally {
            this.state.imageLoading = false;
        }
    }

    onFilePaste(event) {
        if (this.state.busy && !this.state.sending) return;
        const files = [...(event.clipboardData?.files || [])];
        if (files.length) this.loadFiles(files);
    }

    onFileDrop(event) {
        if (this.state.busy && !this.state.sending) return;
        const files = [...(event.dataTransfer?.files || [])];
        if (!files.length) return;
        event.preventDefault();
        this.loadFiles(files);
    }

    onFilesSelected(event) {
        this.loadFiles([...(event.target.files || [])]);
        event.target.value = "";
    }

    async loadFiles(files) {
        for (const file of files) {
            if (file.type.startsWith("image/")) {
                if (this.state.image) {
                    this.state.error = _t("Only one image can be attached.");
                    continue;
                }
                await this.loadImage(file);
                continue;
            }
            if (!/\.(txt|md|csv|pdf)$/i.test(file.name)) {
                this.state.error = _t("Attach a PNG, JPEG, WebP, PDF, TXT, MD or CSV file.");
                continue;
            }
            if (file.size > 4 * 1024 * 1024 || this.state.files.length >= 3) {
                this.state.error = _t("Attach up to three documents, each under 4 MB.");
                continue;
            }
            this.state.imageLoading = true;
            try {
                const url = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = () => reject(reader.error);
                    reader.readAsDataURL(file);
                });
                this.state.files = [...this.state.files, {
                    name: file.name, data: String(url).split(",", 2)[1],
                }];
                this.state.error = "";
            } catch {
                this.state.error = _t("The file could not be read.");
            } finally {
                this.state.imageLoading = false;
            }
        }
    }

    async confirmOperation(operationId) {
        if (this.state.busy || !this.state.conversation) return;
        this.state.busy = true;
        const conversationId = this.state.conversation.id;
        let runId = null;
        try {
            const conversation = await this.orm.call("qing.ai.conversation", "assistant_confirm_operation", [
                [conversationId], operationId,
            ]);
            this.applyConversation(conversation);
            // A confirmed step of a multi-step request resumes planning for the remaining steps.
            runId = conversation.active_run?.id;
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
        if (runId) {
            this.driveRun(runId, conversationId);
        }
    }
}

registry.category("actions").add("qing_ai.daily_assistant", DailyAssistant);
