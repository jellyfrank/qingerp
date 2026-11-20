import { ResPartner } from "@mail/core/common/res_partner_model";
import { AvatarCard } from "@mail/core/web/avatar_card/avatar_card";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";

patch(ResPartner.prototype, {
    get displayName() {
        const name = super.displayName;
        if (name === "Qing Assistant" && this.isBot && /^zh(?:-|$)/.test(user.lang)) {
            return "小青";
        }
        return name;
    },
});

patch(AvatarCard.prototype, {
    get name() {
        return this.partner?.isBot ? this.partner.displayName : super.name;
    },

    get email() {
        const email = super.email;
        return this.partner?.isBot && email === "odoobot@example.com" ? undefined : email;
    },
});
