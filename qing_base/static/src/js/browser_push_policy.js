/** @odoo-module **/

import "@mail/webclient/web/webclient";
import { notificationPermissionService } from "@mail/core/common/notification_permission_service";
import { OutOfFocusService } from "@mail/core/common/out_of_focus_service";
import { patch } from "@web/core/utils/patch";
import { WebClient } from "@web/webclient/webclient";

// Preserve the service worker for PWA/offline support, and the mail bus for chat.
patch(WebClient.prototype, {
    get _canSendNativeNotification() {
        return false;
    },
    async _subscribePush() {},
});

patch(notificationPermissionService, {
    start() {
        return {
            permission: "denied",
            async requestPermission() {},
        };
    },
});

patch(OutOfFocusService.prototype, {
    get canSendNativeNotification() {
        return false;
    },
    async hasServiceWorkInstalledAndPushSubscriptionActive() {
        return false;
    },
});
