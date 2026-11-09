/** @odoo-module **/

import { expect, mockPermission, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineMailModels,
    openDiscuss,
    openMessagingMenu,
    start,
    startServer,
} from "@mail/../tests/mail_test_helpers";
import { browser } from "@web/core/browser/browser";
import { WebClient } from "@web/webclient/webclient";
import { defineModels, getService, models, patchWithCleanup } from "@web/../tests/web_test_helpers";

import "@qing_base/js/browser_push_policy";

class IrConfigParameter extends models.Model {
    _name = "ir.config_parameter";

    get_mommy_web_settings() {
        return {
            title: "QingERP",
            show_documentation: true,
            show_support: true,
            show_shortcuts: true,
            show_odoo_account: true,
        };
    }
}

defineMailModels();
defineModels([IrConfigParameter]);

test("QingERP push: granted permission never attempts device subscription", async () => {
    mockPermission("notifications", "granted");
    patchWithCleanup(browser.Notification, { permission: "granted" });
    patchWithCleanup(WebClient.prototype, {
        async pushManager() {
            expect.step("push-manager");
            throw new Error("Registration failed - push service error");
        },
    });
    await start();
    await animationFrame();
    await WebClient.prototype._subscribePush();
    expect.verifySteps([]);
    expect(".o_notification:contains('Failed to enable push notifications')").toHaveCount(0);
    expect(getService("mail.out_of_focus").canSendNativeNotification).toBe(false);
});

test("QingERP push: messaging menu has no enable prompt or permission request", async () => {
    mockPermission("notifications", "prompt");
    patchWithCleanup(browser.Notification, {
        async requestPermission() {
            expect.step("permission-request");
            return "granted";
        },
    });
    await start();
    await openMessagingMenu();
    await contains(".o-mail-MessagingMenu");
    expect(".o-mail-NotificationItem:contains('Turn on notifications')").toHaveCount(0);
    const permissionService = getService("mail.notification.permission");
    expect(permissionService.permission).toBe("denied");
    await permissionService.requestPermission();
    expect.verifySteps([]);
});

test("QingERP push: Discuss still renders in-app chat messages", async () => {
    const pyEnv = await startServer();
    const channelId = pyEnv["discuss.channel"].create({
        name: "QingERP chat",
        channel_type: "channel",
    });
    pyEnv["mail.message"].create({
        model: "discuss.channel",
        res_id: channelId,
        body: "QingERP in-app message",
        message_type: "comment",
    });
    await start();
    await openDiscuss(channelId);
    await contains(".o-mail-Message-content:contains('QingERP in-app message')");
});
