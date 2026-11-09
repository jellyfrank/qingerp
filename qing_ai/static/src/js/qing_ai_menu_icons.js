/** @odoo-module **/

import { registry } from "@web/core/registry";

// Qing Web UI renders all application and sidebar icons from one 24 px line
// icon family. Register Qing AI menus here without making Qing AI depend on the
// optional theme module.
const menuIcons = registry.category("qing_web_ui.menu_icons");

for (const [xmlid, icon] of Object.entries({
    "qing_ai.menu_qing_ai_root": "star",
    "qing_ai.menu_qing_ai_assistant": "chat",
    "qing_ai.menu_qing_ai_agent": "person",
    "qing_ai.menu_qing_ai_skill": "puzzle",
    "qing_ai.menu_qing_ai_tool": "sliders",
})) {
    menuIcons.add(xmlid, icon, { force: true });
}
