/** @odoo-module **/
import { menuIcons } from "./qing_menu_icons";

// Optional mommy_wework adapter. No business-addon import or dependency.
const icons = {
    menu_wework_robot_root: "robot",
    menu_wework_robot: "robot",
    menu_wework_robot_template: "template",
    menu_wework_robot_message: "message",
    menu_wework_robot_schedule: "calendar",
    menu_wework_robot_schedule_log: "activityLog",
    menu_wework_intelligent_bot: "smartRobot",
    menu_wework_intelligent_bot_message: "smartMessage",
    menu_mommy_message_template: "template",
    menu_mommy_wework_message: "message",
    menu_mommy_wework_messages: "message",
    menu_wework_article: "article",
    menu_wework_media: "media",
    menu_wework_app: "apps",
    menu_wework_menu: "folders",
    menu_wework_bench: "dashboard",
    menu_wework_departments: "organization",
    menu_wework_employees: "person",
    menu_company: "organization",
    menu_wework: "settings",
    menu_wework_config: "settings",
    menu_wework_contact_sync_job: "refresh",
    menu_sync_departments: "refresh",
    menu_sync_employees: "refresh",
};
for (const [name, icon] of Object.entries(icons)) {
    menuIcons.add(`mommy_wework.${name}`, icon);
}
