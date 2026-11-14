import { expect, test } from "@odoo/hoot";
import { click, waitFor } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { animationFrame } from "@odoo/hoot-mock";
import { clickSave, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";

import {
    getClipboardImage,
    getPastedImageUploadInfo,
} from "@qing_base/js/image_paste_field";

test("finds an image ClipboardItem before other clipboard data", () => {
    const image = new File(["image"], "screenshot.png", { type: "image/png" });
    const clipboardData = {
        files: [],
        items: [
            { kind: "string", type: "text/plain" },
            { kind: "file", type: "image/png", getAsFile: () => image },
        ],
    };

    expect(getClipboardImage(clipboardData)).toBe(image);
});

test("finds an image in files when ClipboardItem is unavailable", () => {
    const document = new File(["document"], "document.pdf", { type: "application/pdf" });
    const image = new File(["image"], "screenshot.png", { type: "image/png" });

    expect(getClipboardImage({ files: [document, image], items: [] })).toBe(image);
});

test("ignores clipboard data without an image", () => {
    const document = new File(["document"], "document.pdf", { type: "application/pdf" });

    expect(getClipboardImage({ files: [document], items: [] })).toBe(undefined);
});

test("builds the upload payload used by the native image field", async () => {
    const image = new File(["image"], "screenshot.png", { type: "image/png" });

    expect(await getPastedImageUploadInfo(image)).toEqual({
        name: "screenshot.png",
        size: 5,
        type: "image/png",
        data: "aW1hZ2U=",
        objectUrl: null,
    });
});


const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQI12P4//8/w38GIAXDIBKE0DHxgljNBAAO9TXL0Y4OHwAAAABJRU5ErkJggg==";

class ImagePastePartner extends models.Model {
    _name = "image.paste.partner";
    name = fields.Char();
    image = fields.Binary();
    _records = [{ id: 1, name: "Image Test", image: false }];
}

defineMailModels();
defineModels([ImagePastePartner]);

function pasteImage(target, data = PNG) {
    const transfer = new DataTransfer();
    const bytes = Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
    transfer.items.add(new File([bytes], "screenshot.png", { type: "image/png" }));
    const event = new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: transfer });
    target.dispatchEvent(event);
    return event;
}

async function mountImageField(extra = "") {
    await mountView({
        type: "form",
        resModel: "image.paste.partner",
        resId: 1,
        arch: `<form><field name="image" widget="image_paste" ${extra}/></form>`,
    });
}

test("pasting into an empty image field updates and saves the image", async () => {
    onRpc("web_save", ({ args }) => {
        expect(args[1].image.content).toBe(PNG);
        expect.step("saved");
    });
    await mountImageField();
    expect(pasteImage(document.querySelector(".o_image_paste_field")).defaultPrevented).toBe(true);
    await waitFor(".o_field_widget[name=image] img[data-src^='data:image/']");
    await clickSave();
    await expect.waitForSteps(["saved"]);
});

test("replacing an image requires confirmation", async () => {
    ImagePastePartner._records[0].image = PNG;
    await mountImageField();
    pasteImage(document.querySelector(".o_image_paste_field"));
    await waitFor(".modal");
    expect(".modal").toHaveText(/This field already contains an image/);
    await click(".modal .btn-secondary");
    await animationFrame();
    expect(".modal").toHaveCount(0);
    pasteImage(document.querySelector(".o_image_paste_field"));
    await waitFor(".modal");
    await click(".modal .btn-primary");
    await waitFor(".o_field_widget[name=image] img[data-src^='data:image/']");
});

test("readonly fields do not consume paste events", async () => {
    await mountImageField('readonly="1"');
    const event = pasteImage(document.querySelector(".o_image_paste_field"));
    await animationFrame();
    expect(event.defaultPrevented).toBe(false);
    expect(".o_image_paste_hint").toHaveCount(0);
    expect(".modal").toHaveCount(0);
});

test("existing images can be opened in the file viewer", async () => {
    ImagePastePartner._records[0].image = PNG;
    await mountImageField('readonly="1"');
    await click(".o_image_paste_preview");
    await waitFor(".o-FileViewer-viewImage");
    expect(".o-FileViewer-viewImage").toHaveAttribute("src", /data:image/);
});
