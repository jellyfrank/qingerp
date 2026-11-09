/** @odoo-module **/

// Only unambiguous, generic commands can lose their visible label. A business
// action such as "Create Invoice" or "Confirm Order" must keep its own text.
const COMMON_ACTION_ICONS = new Map([
    ["new", "add"], ["create", "add"], ["新建", "add"], ["创建", "add"],
    ["save", "save"], ["保存", "save"],
    ["discard", "undo"], ["丢弃", "undo"], ["放弃", "undo"],
    ["cancel", "close"], ["取消", "close"],
    ["confirm", "check"], ["确认", "check"],
    ["print", "print"], ["打印", "print"],
    ["preview", "visibility"], ["预览", "visibility"],
    ["send", "send"], ["发送", "send"],
    ["download", "download"], ["下载", "download"],
    ["export", "upload"], ["导出", "upload"],
]);

export function qingHeaderActionIcon(label = "") {
    return COMMON_ACTION_ICONS.get(String(label).trim().toLocaleLowerCase()) || null;
}
