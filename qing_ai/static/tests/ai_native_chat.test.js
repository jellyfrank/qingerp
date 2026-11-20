/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import {
    click,
    contains,
    defineMailModels,
    insertText,
    start,
    startServer,
    triggerHotkey,
} from "@mail/../tests/mail_test_helpers";
import {
    Command,
    getService,
    onRpc,
    serverState,
} from "@web/../tests/web_test_helpers";

defineMailModels();

async function openAgentChat() {
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Tag Copilot" });
    const channelId = pyEnv["discuss.channel"].create({
        channel_member_ids: [
            Command.create({ partner_id: serverState.partnerId }),
            Command.create({ partner_id: partnerId }),
        ],
        channel_type: "chat",
    });
    const messageId = pyEnv["mail.message"].create({
        author_id: partnerId,
        body: "<p>Review the proposed business action below.</p>",
        message_type: "comment",
        model: "discuss.channel",
        res_id: channelId,
    });
    await start();
    const store = getService("mail.store");
    // Agent chats are opened from Discuss; the navbar button opens the bot chat instead.
    const channel = await store["discuss.channel"].getOrFetch(channelId);
    await channel.openChatWindow({ focus: true });
    await contains(".o-mail-ChatWindow", { text: "Tag Copilot" });
    await contains(".o-mail-ChatWindow .o-mail-Message");
    // The server sends these for AI chats; the mock server does not know the fields.
    channel.qing_ai_agent_id = 7;
    channel.qing_ai_conversation_id = 3;
    return { channel, channelId, message: store["mail.message"].get(messageId) };
}

test("the navbar AI button opens the existing chat with the assistant bot", async () => {
    const pyEnv = await startServer();
    const channelId = pyEnv["discuss.channel"].create({
        channel_member_ids: [
            Command.create({ partner_id: serverState.partnerId }),
            Command.create({ partner_id: serverState.odoobotId }),
        ],
        channel_type: "chat",
    });
    pyEnv["mail.message"].create({
        author_id: serverState.odoobotId,
        body: "<p>Hello, I can help with QingERP.</p>",
        message_type: "comment",
        model: "discuss.channel",
        res_id: channelId,
    });
    onRpc("qing.ai.agent", "assistant_launch_chat", () => {
        throw new Error("The navbar button must not open an agent chat");
    });
    await start();
    await click(".o_qing_ai_trigger");
    await contains(".o-mail-ChatWindow", { text: "OdooBot" });
    await contains(".o-mail-ChatWindow .o-mail-Message", { text: "Hello, I can help with QingERP." });
    await click(".o_qing_ai_trigger");
    await animationFrame();
    expect(".o-mail-ChatWindow").toHaveCount(1);
    const chats = pyEnv["discuss.channel"].search([["channel_type", "=", "chat"]]);
    expect(chats).toEqual([channelId]);
});

test("the navbar AI button creates the bot chat when it does not exist yet", async () => {
    const pyEnv = await startServer();
    await start();
    await click(".o_qing_ai_trigger");
    await contains(".o-mail-ChatWindow", { text: "OdooBot" });
    const chats = pyEnv["discuss.channel"].search_read([["channel_type", "=", "chat"]]);
    expect(chats).toHaveLength(1);
    const members = pyEnv["discuss.channel.member"].search_read([["channel_id", "=", chats[0].id]]);
    expect(members.map((member) => member.partner_id[0]).sort()).toEqual(
        [serverState.partnerId, serverState.odoobotId].sort()
    );
});

test("posting in an AI chat advances the reply and shows the streamed text", async () => {
    let calls = 0;
    onRpc("discuss.channel", "qing_ai_advance", ({ args }) => {
        calls++;
        expect.step(`advance ${args[0][0]}`);
        return { running: calls < 2, progressed: true };
    });
    const { channel, channelId } = await openAgentChat();
    channel.qingAiReceiveStream("Counting the tags");
    await contains(".o_qing_ai_chat_stream", { text: "Counting the tags" });
    await insertText(".o-mail-Composer-input", "How many tags?");
    triggerHotkey("Enter");
    await expect.waitForSteps([`advance ${channelId}`, `advance ${channelId}`]);
    await animationFrame();
    // The loop ends with the reply in the thread, so the partial answer is cleared.
    expect(".o_qing_ai_chat_stream").toHaveCount(0);
    expect(channel.qingAiBusy).toBe(false);
});

test("a reply shows steps, usage and confirms its preview in the chat", async () => {
    onRpc("discuss.channel", "qing_ai_confirm_operation", ({ args }) => {
        expect.step(`confirm ${args[1]}`);
        return { running: true };
    });
    onRpc("discuss.channel", "qing_ai_advance", () => {
        expect.step("advance");
        return { running: false, progressed: true };
    });
    const { message } = await openAgentChat();
    message.qing_ai_meta = {
        failed: false,
        model: "mock-model",
        operations: [{ id: "orm:5", preview: "Create tag: Native Blue", confirm_label: "Confirm changes" }],
        process_steps: [{ kind: "tool", round: 1, status: "ok", summary: "Preparing the tag.", tool_label: "Tags create" }],
        usage_summary: { input_tokens: 120, cached_input_tokens: 100, output_tokens: 8 },
    };
    await contains(".o_qing_ai_chat_operation", { text: "Create tag: Native Blue" });
    // The AI details are added below the body; they must not replace it.
    expect(".o-mail-Message-richBody").toHaveText(/Review the proposed business action/);
    await contains(".o_qing_ai_chat_usage", { text: "mock-model" });
    expect(".o_qing_ai_chat_usage").toHaveText(/Input: 120.*Cache hit: 100.*Output: 8/);
    expect(".o_qing_ai_chat_process summary").toHaveText(/Execution process · 1/);
    await click(".o_qing_ai_chat_confirm");
    await expect.waitForSteps(["confirm orm:5", "advance"]);
    await contains(".o_qing_ai_chat_operation", { count: 0 });
});

async function openChatWithUnreadReply(makeCorrespondent) {
    const pyEnv = await startServer();
    const correspondentId = makeCorrespondent(pyEnv);
    const channelId = pyEnv["discuss.channel"].create({
        channel_member_ids: [
            Command.create({ partner_id: serverState.partnerId }),
            Command.create({ partner_id: correspondentId }),
        ],
        channel_type: "chat",
    });
    pyEnv["mail.message"].create({
        author_id: serverState.partnerId,
        body: "<p>How many tags?</p>",
        message_type: "comment",
        model: "discuss.channel",
        res_id: channelId,
    });
    const replyId = pyEnv["mail.message"].create({
        author_id: correspondentId,
        body: "<p>There are 3 tags.</p>",
        message_type: "comment",
        model: "discuss.channel",
        res_id: channelId,
    });
    const [memberId] = pyEnv["discuss.channel.member"].search([
        ["channel_id", "=", channelId],
        ["partner_id", "=", serverState.partnerId],
    ]);
    pyEnv["discuss.channel.member"].write([memberId], { new_message_separator: replyId });
    await start();
    const channel = await getService("mail.store")["discuss.channel"].getOrFetch(channelId);
    await channel.openChatWindow({ focus: true });
    await contains(".o-mail-ChatWindow .o-mail-Message", { count: 2 });
    await animationFrame();
}

test("a chat with a person keeps the native New line above an unread reply", async () => {
    await openChatWithUnreadReply((pyEnv) => pyEnv["res.partner"].create({ name: "Bob" }));
    await contains(".o-mail-Thread-newMessage");
});

test("the Qing Assistant chat shows no New line right below the user's question", async () => {
    await openChatWithUnreadReply(() => serverState.odoobotId);
    expect(".o-mail-Thread-newMessage").toHaveCount(0);
    expect(".o-mail-ChatWindow .o-mail-Message:last").toHaveText(/There are 3 tags/);
});

test("posting to Qing Assistant lets the chat window advance its reply", async () => {
    const pyEnv = await startServer();
    pyEnv["discuss.channel"].create({
        channel_member_ids: [
            Command.create({ partner_id: serverState.partnerId }),
            Command.create({ partner_id: serverState.odoobotId }),
        ],
        channel_type: "chat",
    });
    onRpc("discuss.channel", "qing_ai_advance", () => {
        expect.step("advance");
        return { running: false, progressed: false };
    });
    await start();
    await click(".o_qing_ai_trigger");
    await contains(".o-mail-ChatWindow", { text: "OdooBot" });
    await insertText(".o-mail-Composer-input", "How many tags?");
    triggerHotkey("Enter");
    await expect.waitForSteps(["advance"]);
});
