import { DiscussChannel } from "@mail/discuss/core/common/discuss_channel_model";
import { fields } from "@mail/model/misc";
import { registerThreadAction } from "@mail/core/common/thread_actions";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(DiscussChannel.prototype, {
    get allowCalls() {
        return !this.qing_conversation_id && super.allowCalls;
    },
    setup() {
        super.setup(...arguments);
        this.qing_conversation_id = fields.Attr(false);
    },
});
registerThreadAction("qing-whatsapp-conversation", {
    condition: ({ channel }) => Boolean(channel?.qing_conversation_id),
    icon: "oi_whatsapp",
    name: _t("Customer, quotations and internal notes"),
    sequence: 12,
    sequenceQuick: 15,
    onSelected: ({ channel, store }) => store.env.services.action.doAction({
        type: "ir.actions.act_window", res_model: "qing.whatsapp.conversation",
        res_id: channel.qing_conversation_id, views: [[false, "form"]], target: "current",
    }),
});
registerThreadAction("qing-whatsapp-template", {
    condition: ({ channel }) => Boolean(channel?.qing_conversation_id),
    icon: "description",
    name: _t("Send WhatsApp template"),
    sequence: 13,
    onSelected: async ({ channel, store }) => {
        const action = await store.env.services.orm.call("qing.whatsapp.conversation", "action_compose", [[channel.qing_conversation_id]]);
        action.context = { ...action.context, default_kind: "template" };
        return store.env.services.action.doAction(action);
    },
});
