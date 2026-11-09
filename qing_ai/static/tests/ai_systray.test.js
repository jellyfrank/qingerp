/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { insertText } from "@html_editor/../tests/_helpers/user_actions";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { patch } from "@web/core/utils/patch";
import { contains, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";

import { AiSystray } from "@qing_ai/js/ai_systray";
import { DailyAssistant } from "@qing_ai/js/daily_assistant";
import { editorToMarkdown } from "@qing_ai/js/composer_content";

defineMailModels();

test("AI systray opens a conversation and sends a reply without creating another session", async () => {
    let sends = 0;
    let editor;
    patch(DailyAssistant.prototype, {
        onEditorReady(value) {
            super.onEditorReady(value);
            editor = value;
        },
    });
    const original = {
        id: 7,
        name: "Quarterly plan",
        provider: "default",
        target_language: "English",
        providers: [{ value: "default", label: "Default Provider" }],
        context_model: "",
        context_res_id: 0,
        context_fields: [],
        context_unavailable: false,
        messages: [{ id: 1, role: "user", content: "Previous question", model: "" }],
    };
    onRpc("qing.ai.conversation", "assistant_list", () => [
        { id: 7, name: "Quarterly plan", last_message_at: "2026-09-24 08:00:00" },
    ]);
    onRpc("qing.ai.conversation", "assistant_new", () => {
        throw new Error("Opening the systray must not create a conversation");
    });
    onRpc("qing.ai.conversation", "assistant_open", ({ args }) => {
        expect(args[0]).toEqual([7]);
        return original;
    });
    onRpc("qing.ai.conversation", "assistant_send", ({ args }) => {
        sends++;
        expect(args[0]).toEqual([7]);
        expect(args[1]).toBe("What changed?");
        return {
            ...original,
            messages: [
                ...original.messages,
                { id: 2, role: "user", content: "What changed?", model: "" },
                {
                    id: 3, role: "assistant", content: "**The plan changed.**",
                    content_html: "<p><strong>The plan changed.</strong></p>", model: "test-model",
                    process_steps: [{
                        kind: "tool", status: "ok", tool_label: "Search quotations",
                        summary: "Checked the matching quotations.",
                    }],
                    usage_summary: {
                        input_tokens: 120, output_tokens: 30,
                        reasoning_tokens: 8, total_tokens: 158,
                    },
                },
            ],
        };
    });

    await mountWithCleanup(AiSystray);
    await contains(".o_qing_ai_trigger").click();
    expect(".o_qing_ai_session_row").toHaveCount(1);
    await contains(".o_qing_ai_session_row").click();
    expect(".o_qing_ai_messages").toHaveText(/Previous question/);
    expect(".o_qing_ai_options:not([open])").toHaveCount(1);
    await contains(".o_qing_ai_options summary").click();
    expect(".o_qing_ai_options[open]").toHaveCount(1);
    await contains(".o_qing_ai_options summary").click();
    await contains(".o_qing_ai_editable").click();
    await insertText(editor, "What changed?");
    await contains(".o_qing_ai_editable").press("Enter");
    expect(sends).toBe(1);
    expect(editorToMarkdown(editor.editable)).toBe("");
    expect(".o_qing_ai_markdown strong").toHaveText("The plan changed.");
    expect(".o_qing_ai_process summary").toHaveText(/Execution process · 1/);
    await contains(".o_qing_ai_process summary").click();
    expect(".o_qing_ai_process_steps").toHaveText(/Checked the matching quotations/);
    expect(".o_qing_ai_response_meta").toHaveText(
        /test-model.*Token usage.*Input: 120.*Output: 30.*Reasoning: 8.*Total tokens: 158/
    );
    await contains("button[aria-label='Back to AI conversations']").click();
    expect(".o_qing_ai_session_row").toHaveCount(1);
});

test("conversation owner can delete a chat from the full assistant", async () => {
    const conversation = {
        id: 17, name: "Private chat", provider: "default", target_language: "English",
        providers: [{ value: "default", label: "Default Provider" }],
        context_fields: [], messages: [{ id: 21, role: "user", content: "Confidential", model: "" }],
    };
    let deletedId;
    onRpc("qing.ai.conversation", "assistant_list", () => [
        { id: 17, name: "Private chat", last_message_at: "" },
    ]);
    onRpc("qing.ai.conversation", "assistant_open", () => conversation);
    onRpc("qing.ai.conversation", "assistant_delete", ({ args }) => {
        deletedId = args[0][0];
        return true;
    });

    await mountWithCleanup(DailyAssistant);
    expect(".o_qing_ai_messages").toHaveText(/Confidential/);
    await contains(".o_qing_ai_delete_session").click();
    expect(deletedId).toBe(undefined);
    await contains(".modal-footer .btn-danger").click();
    expect(deletedId).toBe(17);
    expect(".o_qing_ai_session_item").toHaveCount(0);
    expect(".o_qing_ai_messages").toHaveText(/Select a conversation/);
});

test("sending unlocks the composer and shows thinking dots without losing the next draft", async () => {
    const response = Promise.withResolvers();
    let payload;
    let sends = 0;
    const conversation = {
        id: 7, name: "Image question", provider: "default", target_language: "English",
        providers: [{ value: "default", label: "Default Provider" }],
        context_fields: [], messages: [],
    };
    onRpc("qing.ai.conversation", "assistant_list", () => []);
    onRpc("qing.ai.conversation", "assistant_new", () => conversation);
    onRpc("qing.ai.conversation", "assistant_send", ({ args }) => {
        sends++;
        payload = args;
        return response.promise;
    });
    const assistant = await mountWithCleanup(DailyAssistant);
    assistant.state.image = {
        name: "image.png", data: "cGljdHVyZQ==", url: "data:image/png;base64,cGljdHVyZQ==",
    };
    assistant.state.files = [{ name: "notes.txt", data: "bm90ZXM=" }];
    await contains(".o_qing_ai_editable").click();
    await insertText(assistant.editor, "Can you read this image?");
    expect(".o_qing_ai_image_preview").toHaveCount(1);
    expect(".o_qing_ai_file_preview").toHaveCount(1);
    await contains(".o_qing_ai_editable").press("Enter");
    expect(payload).toEqual([
        [7], "Can you read this image?", "ask", "default", "English",
        { name: "image.png", data: "cGljdHVyZQ==" },
        [{ name: "notes.txt", data: "bm90ZXM=" }],
        true,
    ]);
    expect(assistant.state.busy).toBe(true);
    expect(assistant.state.sending).toBe(true);
    expect(assistant.state.draft).toBe("");
    expect(editorToMarkdown(assistant.editor.editable)).toBe("");
    expect(".o_qing_ai_image_preview, .o_qing_ai_file_preview").toHaveCount(0);
    expect(".o_qing_ai_editor--disabled").toHaveCount(0);
    expect(assistant.editor.editable.contentEditable).toBe("true");
    expect(".o_qing_ai_user").toHaveText(/Can you read this image/);
    expect(".o_qing_ai_thinking_dots span").toHaveCount(3);
    expect(".o_qing_ai_thinking").toHaveAttribute("role", "status");
    expect("button[type='submit']").toHaveAttribute("disabled");
    await insertText(assistant.editor, "My next question");
    expect(assistant.state.draft).toBe("My next question");
    assistant.state.files = [{ name: "next.txt", data: "bmV4dA==" }];
    await animationFrame();
    expect(".o_qing_ai_file_preview").toHaveCount(1);
    await contains(".o_qing_ai_editable").press("Enter");
    expect(sends).toBe(1);
    response.resolve({
        ...conversation,
        messages: [{ id: 1, role: "error", content: "Image input is not enabled." }],
    });
    await animationFrame();
    expect(".o_qing_ai_messages").toHaveText(/Image input is not enabled/);
    expect(assistant.state.busy).toBe(false);
    expect(assistant.state.sending).toBe(false);
    expect(".o_qing_ai_thinking").toHaveCount(0);
    expect(assistant.state.draft).toBe("My next question");
    expect(editorToMarkdown(assistant.editor.editable)).toBe("My next question");
    expect(".o_qing_ai_image_preview").toHaveCount(0);
    expect(".o_qing_ai_file_preview").toHaveCount(1);
    expect("button[type='submit']").not.toHaveAttribute("disabled");
});

test("Alt+Enter inserts a line break without sending", () => {
    let inserted;
    let sends = 0;
    let prevented = false;
    const assistant = {
        editor: { shared: { dom: { insert: (value) => { inserted = value; } }, history: { commit: () => {} } } },
        send: () => sends++,
    };
    DailyAssistant.prototype.onDraftKeydown.call(assistant, {
        key: "Enter",
        altKey: true,
        preventDefault: () => { prevented = true; },
    });
    expect(inserted.tagName).toBe("BR");
    expect(prevented).toBe(true);
    expect(sends).toBe(0);
});

test("pasting files forwards them to the attachment loader", () => {
    const file = new File(["picture"], "pasted.png", { type: "image/png" });
    let attached;
    const assistant = {
        state: { busy: false },
        loadFiles: (value) => { attached = value; },
    };
    DailyAssistant.prototype.onFilePaste.call(assistant, { clipboardData: { files: [file] } });
    expect(attached).toEqual([file]);
});

test("rich editor content retains common formatting in the model prompt", () => {
    const root = document.createElement("div");
    root.innerHTML = "<p><strong>Plan</strong></p><ul><li>First</li><li>Second</li></ul>";
    expect(editorToMarkdown(root)).toBe("**Plan**\n- First\n- Second");
});
