import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export class LoginBackgroundPreview extends Component {
    static template = "qing_base.LoginBackgroundPreview";
    props = useProps(standardWidgetProps);

    setup() {
        this.orm = useService("orm");
    }

    async preview() {
        const preview = window.open("about:blank", "_blank");
        if (!preview) {
            return;
        }
        const data = this.props.record.data;
        try {
            const url = await this.orm.call("res.config.settings", "preview_login_background", [
                data.qing_login_background_style,
                data.qing_login_background_color,
                data.qing_login_background_image || false,
            ]);
            preview.location.href = url;
        } catch (error) {
            preview.close();
            throw error;
        }
    }
}
registry.category("view_widgets").add("qing_login_background_preview", { component: LoginBackgroundPreview });
