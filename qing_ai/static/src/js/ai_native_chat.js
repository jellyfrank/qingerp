/** @odoo-module **/

import { proxy } from "@odoo/owl";
import { Message } from "@mail/core/common/message";
import { Message as MessageModel } from "@mail/core/common/message_model";
import { Thread as ThreadView } from "@mail/core/common/thread";
import { registerThreadAction } from "@mail/core/common/thread_actions";
import { Thread } from "@mail/core/common/thread_model";
import { DiscussChannel } from "@mail/discuss/core/common/discuss_channel_model";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";

import { DailyAssistant } from "./daily_assistant";

// One call is one provider round; planner runs stop by themselves after a few rounds.
const MAX_ADVANCE_CALLS = 40;
const RETRY_DELAY_MS = 800;

function wait(delay) {
    return new Promise((resolve) => setTimeout(resolve, delay));
}

/**
 * AI chats are private native chats with an agent's Discuss identity, or the user's chat with
 * Qing Assistant once it answers through an agent. The chat window drives each reply on the
 * server one bounded step at a time and shows the partial answer streamed by the provider
 * until the reply message arrives.
 */
patch(DiscussChannel.prototype, {
    setup() {
        super.setup(...arguments);
        /** @type {number|undefined} agent of a Qing AI chat */
        this.qing_ai_agent_id = undefined;
        /** @type {number|undefined} conversation that keeps the AI context of this chat */
        this.qing_ai_conversation_id = undefined;
        this.qingAiBusy = false;
        this.qingAiStream = "";
        this.qingAiStreamAfterId = 0;
    },
    get isQingAiChat() {
        return Boolean(this.qing_ai_agent_id);
    },
    /** The user's private chat with Qing Assistant (the system bot), bound to an agent or not. */
    get isQingAssistantChat() {
        const assistant = this.store.odoobot;
        return (
            this.channel_type === "chat" &&
            Boolean(assistant) &&
            Boolean(this.correspondent?.partner_id?.eq(assistant))
        );
    },
    get allowCalls() {
        // An AI agent cannot take audio or video calls.
        return !this.isQingAiChat && super.allowCalls;
    },
    get qingAiStreamVisible() {
        // Hide the partial answer as soon as a newer message (the reply) is in the thread.
        return Boolean(this.qingAiStream) && (this.thread?.newestMessage?.id ?? 0) <= this.qingAiStreamAfterId;
    },
    qingAiReceiveStream(text) {
        if (!this.qingAiStream) {
            this.qingAiStreamAfterId = this.thread?.newestMessage?.id ?? 0;
        }
        this.qingAiStream = text;
    },
    async qingAiAdvance() {
        if (this.qingAiBusy) {
            return;
        }
        this.qingAiBusy = true;
        const orm = this.store.env.services.orm;
        try {
            for (let call = 0; call < MAX_ADVANCE_CALLS; call++) {
                const result = await orm.call("discuss.channel", "qing_ai_advance", [[this.id]]);
                if (!result.running) {
                    break;
                }
                if (!result.progressed) {
                    await wait(RETRY_DELAY_MS);
                }
            }
        } finally {
            this.qingAiBusy = false;
            this.qingAiStream = "";
        }
    },
    async qingAiConfirm(message, operationId) {
        const orm = this.store.env.services.orm;
        await orm.call("discuss.channel", "qing_ai_confirm_operation", [[this.id], operationId]);
        const meta = message.qing_ai_meta;
        if (meta) {
            message.qing_ai_meta = {
                ...meta,
                operations: meta.operations.filter((operation) => operation.id !== operationId),
            };
        }
        await this.qingAiAdvance();
    },
    async qingAiNewTopic() {
        await this.store.env.services.orm.call("discuss.channel", "qing_ai_new_topic", [[this.id]]);
    },
});

patch(Thread.prototype, {
    async post() {
        const message = await super.post(...arguments);
        // Qing Assistant may bind its chat to an agent while handling this very message.
        if (this.channel?.isQingAiChat || this.channel?.isQingAssistantChat) {
            // Not awaited: the composer is free while the reply is produced.
            this.channel.qingAiAdvance();
        }
        return message;
    },
});

patch(ThreadView.prototype, {
    showNewMessageLine(msg, prevMsg) {
        const channel = this.channel;
        if (
            (channel?.isQingAiChat || channel?.isQingAssistantChat) &&
            !channel.markedAsUnread &&
            prevMsg?.isSelfAuthored
        ) {
            // An answer right below the user's own question needs no "New" marker.
            return false;
        }
        return super.showNewMessageLine(msg, prevMsg);
    },
});

patch(MessageModel.prototype, {
    setup() {
        super.setup(...arguments);
        /** @type {Object|undefined} steps, usage and pending previews of a Qing AI reply */
        this.qing_ai_meta = undefined;
    },
});

patch(Message.prototype, {
    setup() {
        super.setup(...arguments);
        this.qingAiState = proxy({ confirming: null });
    },
    qingAiStepMarker(step) {
        return DailyAssistant.prototype.processStepMarker.call(this, step);
    },
    qingAiStepText(step) {
        return DailyAssistant.prototype.processStepText.call(this, step);
    },
    qingAiUsageText(summary) {
        return DailyAssistant.prototype.usageText.call(this, summary);
    },
    async onQingAiConfirm(operation) {
        if (this.qingAiState.confirming) {
            return;
        }
        this.qingAiState.confirming = operation.id;
        try {
            await this.message.thread.channel.qingAiConfirm(this.message, operation.id);
        } finally {
            this.qingAiState.confirming = null;
        }
    },
});

registerThreadAction("qing-ai-new-topic", {
    condition: ({ channel, owner }) => channel?.isQingAiChat && !owner.isDiscussSidebarChannelActions,
    icon: "add",
    name: _t("New Topic"),
    onSelected: ({ channel }) => channel.qingAiNewTopic(),
    sequence: 8,
    sequenceQuick: 30,
});

registerThreadAction("qing-ai-open-assistant", {
    condition: ({ channel, owner }) => channel?.isQingAiChat && !owner.isDiscussSidebarChannelActions,
    icon: "expand_content",
    name: _t("Open in Qing AI"),
    onSelected: ({ channel }) =>
        channel.store.env.services.action.doAction({
            type: "ir.actions.client",
            name: _t("Daily Assistant"),
            tag: "qing_ai.daily_assistant",
            params: { conversation_id: channel.qing_ai_conversation_id },
        }),
    sequence: 9,
});

export const qingAiChatStreamService = {
    dependencies: ["bus_service", "mail.store"],
    start(env, { bus_service: bus, "mail.store": store }) {
        bus.subscribe("qing_ai/stream", ({ channel_id: channelId, text }) => {
            if (!channelId || typeof text !== "string") {
                return;
            }
            const channel = store["discuss.channel"].get(channelId);
            if (channel?.isQingAiChat || channel?.isQingAssistantChat) {
                channel.qingAiReceiveStream(text);
            }
        });
    },
};

registry.category("services").add("qing_ai.chat_stream", qingAiChatStreamService);
