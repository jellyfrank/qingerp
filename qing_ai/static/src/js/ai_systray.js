/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

/** Navbar entry that opens the chat with the assistant bot (小青) in a native chat window. */
export class AiSystray extends Component {
    static template = "qing_ai.AiSystray";

    setup() {
        this.store = useService("mail.store");
        this.opening = false;
    }

    async openChat() {
        const assistant = this.store.odoobot;
        if (this.opening || !assistant) {
            return;
        }
        this.opening = true;
        try {
            let chat = assistant.searchChat();
            if (!chat?.self_member_id?.is_pinned) {
                // Same route as the native "chat with" action; it reuses the existing chat.
                chat = await this.store.joinChat(assistant.id);
            }
            // Native open: inside the Discuss app it shows there, otherwise as a chat window.
            chat?.open({ focus: true });
        } finally {
            this.opening = false;
        }
    }
}

registry.category("systray").add("qing_ai.assistant", {
    Component: AiSystray,
    isDisplayed: () => user.isInternalUser,
}, { sequence: 110 });
