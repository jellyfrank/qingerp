import { onMounted, proxy } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useBus } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { WebClient } from "@web/webclient/webclient";

const LOCATION_MODELS = new Set(["qing.audit.log", "qing.login.log", "res.device", "res.session"]);

patch(WebClient.prototype, {
    setup() {
        super.setup(...arguments);
        this.geoipAttribution = proxy({ visible: false });
        if (session.qing_dbip_geolocation) {
            const updateAttribution = () => {
                const model = this.actionService.currentController?.action?.res_model;
                this.geoipAttribution.visible = LOCATION_MODELS.has(model);
            };
            useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", updateAttribution);
            onMounted(updateAttribution);
        }
    },
});
