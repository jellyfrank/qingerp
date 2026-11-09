import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { resConfigEdition } from "@web/webclient/settings_form_view/widgets/res_config_edition";

patch(resConfigEdition.component.prototype, {
    get qingBaseVersion() {
        return session.qing_base_version || "";
    },
});
