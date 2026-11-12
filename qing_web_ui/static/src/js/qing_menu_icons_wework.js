/** @odoo-module **/
import { menuIcons } from "./qing_menu_icons";

// Optional WeCom adapter for qing_wework and the Solutions mommy_wework.
// No business-addon import or dependency.
const icons = {
    menu_wework_robot_root: "robot",
    menu_wework_robot: "robot",
    menu_wework_robot_template: "robotTemplate",
    menu_wework_robot_message: "robotSend",
    menu_wework_robot_schedule: "robotSchedule",
    menu_wework_robot_schedule_log: "robotScheduleLog",
    menu_wework_intelligent_bot: "aiBot",
    menu_wework_intelligent_bot_message: "aiChat",
    menu_mommy_message_template: "messageTemplate",
    menu_mommy_wework_message: "bell",
    menu_mommy_wework_messages: "bell",
    menu_wework_article: "article",
    menu_wework_media: "media",
    menu_wework_app: "apps",
    menu_wework_menu: "folders",
    menu_wework_bench: "dashboard",
    menu_wework_departments: "organization",
    menu_wework_employees: "person",
    menu_company: "city",
    menu_wework: "settings",
    menu_wework_config: "settings",
    menu_wework_contact_sync_job: "refresh",
    menu_sync_departments: "refresh",
    menu_sync_employees: "refresh",
};
for (const [name, icon] of Object.entries(icons)) {
    menuIcons.add(`mommy_wework.${name}`, icon);
    // qing_wework renamed the three menus whose ids carried the old brand.
    menuIcons.add(`qing_wework.${name.replace("_mommy_", "_qing_")}`, icon);
}
