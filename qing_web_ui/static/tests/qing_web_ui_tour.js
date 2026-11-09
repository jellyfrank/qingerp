/** @odoo-module */

import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_utils";

registry.category("web_tour.tours").add("qing_web_ui_smoke", {
    steps: () => [
        stepUtils.showAppsMenuItem(),
        {
            content: "Open the Qing application launcher",
            trigger: ".o_qing_launcher_trigger",
            run: "click",
        },
        {
            content: "Launcher drawer is visible",
            trigger: ".o_qing_launcher",
        },
        {
            content: "Search applications",
            trigger: ".o_qing_launcher__search input",
            run: "edit Sales",
        },
        {
            content: "Close launcher with Escape",
            trigger: ".o_qing_launcher",
            run: "press Escape",
        },
        {
            content: "Launcher closes",
            trigger: "body:not(:has(.o_qing_launcher))",
        },
    ],
});
