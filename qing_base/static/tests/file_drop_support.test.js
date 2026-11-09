/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { FileInput } from "@web/core/file_input/file_input";
import { FileUploader } from "@web/views/fields/file_handler";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    mockService,
    mountWithCleanup,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { assignDroppedFiles, hasDraggedFiles } from "@qing_base/js/file_drop_support";

defineMailModels();

test("file drags are distinguished from other drag data", () => {
    expect(hasDraggedFiles({ dataTransfer: { types: ["Files"] } })).toBe(true);
    expect(hasDraggedFiles({ dataTransfer: { types: ["text/plain"] } })).toBe(false);
    expect(hasDraggedFiles({})).toBe(false);
});

test("single-file upload keeps only the first dropped file", () => {
    const input = document.createElement("input");
    input.type = "file";
    const files = [
        new File(["first"], "first.txt", { type: "text/plain" }),
        new File(["second"], "second.txt", { type: "text/plain" }),
    ];

    expect(assignDroppedFiles(input, files, false)).toBe(1);
    expect(input.files).toHaveLength(1);
    expect(input.files[0].name).toBe("first.txt");
});

test("multi-file upload keeps every dropped file", () => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    const files = [
        new File(["first"], "first.txt", { type: "text/plain" }),
        new File(["second"], "second.txt", { type: "text/plain" }),
    ];

    expect(assignDroppedFiles(input, files, true)).toBe(2);
    expect(input.files).toHaveLength(2);
    expect([...input.files].map((file) => file.name)).toEqual(["first.txt", "second.txt"]);
});

test("dropping on FileInput uses the native upload path", async () => {
    patchWithCleanup(odoo, { csrf_token: "dummy" });
    mockService("notification", { add() {} });
    mockService("http", {
        post(route, params) {
            expect(route).toBe("/web/binary/upload_attachment");
            expect(params.ufile).toHaveLength(1);
            expect(params.ufile[0].name).toBe("drop.txt");
            expect.step("posted");
            return JSON.stringify([{ name: params.ufile[0].name }]);
        },
    });
    await mountWithCleanup(FileInput, {
        props: {
            beforeOpen: () => true,
            onUpload(files) {
                expect(files[0].name).toBe("drop.txt");
                expect.step("uploaded");
            },
        },
    });

    const transfer = new DataTransfer();
    transfer.items.add(new File(["drop"], "drop.txt", { type: "text/plain" }));
    const target = document.querySelector(".o_file_input");
    target.dispatchEvent(new DragEvent("dragenter", { bubbles: true, dataTransfer: transfer }));
    await animationFrame();
    expect(target).toHaveClass("o_qing_file_drop_active");

    target.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
    await expect.waitForSteps(["posted", "uploaded"]);
    expect(target).not.toHaveClass("o_qing_file_drop_active");
});

test("dropping on FileUploader uses the native field callback", async () => {
    mockService("notification", { add() {} });
    await mountWithCleanup(FileUploader, {
        props: {
            onUploaded(file) {
                expect(file.name).toBe("field-drop.txt");
                expect.step("uploaded");
            },
        },
    });

    const transfer = new DataTransfer();
    transfer.items.add(new File(["drop"], "field-drop.txt", { type: "text/plain" }));
    const target = document.querySelector(".d-contents");
    target.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));

    await expect.waitForSteps(["uploaded"]);
});
