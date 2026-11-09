/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

import { DailyAssistant } from "./daily_assistant";

export class AiSystray extends Component {
    static components = { DailyAssistant, Dropdown };
    static template = "qing_ai.AiSystray";

    setup() {
        this.action = useService("action");
        this.ui = useService("ui");
        this.dropdown = useDropdownState();
    }

    openFullAssistant(conversationId) {
        this.dropdown.close();
        this.action.doAction({
            type: "ir.actions.client",
            name: _t("Daily Assistant"),
            tag: "qing_ai.daily_assistant",
            params: conversationId ? { conversation_id: conversationId } : {},
        });
    }
}

registry.category("systray").add("qing_ai.assistant", {
    Component: AiSystray,
    isDisplayed: () => user.isInternalUser,
}, { sequence: 110 });
