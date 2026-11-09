import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    fields,
    models,
    mountView,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";

import "@qing_base/js/binary_preview";

class Ticket extends models.Model {
    _name = "qing.preview.ticket";

    file = fields.Binary();
    filename = fields.Char();

    _records = [
        { id: 1, file: { content: "JVBERi0xLjQK" }, filename: "invoice.pdf" },
        { id: 2, file: { content: "R0lGODlh" }, filename: "photo.GIF" },
        { id: 3, file: { content: "dGVzdA==" }, filename: "archive.zip" },
    ];
}

defineMailModels();
defineModels([Ticket]);

const arch = '<form edit="0"><field name="file" filename="filename"/><field name="filename" invisible="1"/></form>';

test("read-only PDF offers inline preview alongside native download", async () => {
    let openedUrl;
    patchWithCleanup(window, {
        open(url) {
            openedUrl = url;
        },
    });

    await mountView({ resModel: "qing.preview.ticket", resId: 1, type: "form", arch });

    expect(".o_preview_file_button[data-icon='visibility']").toHaveCount(1);
    expect(".o_field_binary .o_form_uri [data-icon='download']").toHaveCount(1);
    await click(".o_preview_file_button");

    const previewUrl = new URL(openedUrl, window.location.origin);
    expect(previewUrl.pathname).toBe("/web/content");
    expect(previewUrl.searchParams.get("model")).toBe("qing.preview.ticket");
    expect(previewUrl.searchParams.get("field")).toBe("file");
    expect(previewUrl.searchParams.get("filename_field")).toBe("filename");
    expect(previewUrl.searchParams.has("download")).toBe(false);
});

test("common image types offer preview", async () => {
    await mountView({ resModel: "qing.preview.ticket", resId: 2, type: "form", arch });
    expect(".o_preview_file_button").toHaveCount(1);
});

test("unsupported files keep download only", async () => {
    await mountView({ resModel: "qing.preview.ticket", resId: 3, type: "form", arch });
    expect(".o_preview_file_button").toHaveCount(0);
    expect(".o_field_binary .o_form_uri").toHaveCount(1);
});

test("unsupported files also have no edit-mode preview button", async () => {
    await mountView({
        resModel: "qing.preview.ticket",
        resId: 3,
        type: "form",
        arch: '<form><field name="file" filename="filename"/><field name="filename" invisible="1"/></form>',
    });
    expect(".o_preview_file_button").toHaveCount(0);
});

test("editable PDF keeps the shared preview action", async () => {
    await mountView({
        resModel: "qing.preview.ticket",
        resId: 1,
        type: "form",
        arch: '<form><field name="file" filename="filename"/><field name="filename" invisible="1"/></form>',
    });
    expect(".o_preview_file_button").toHaveCount(1);
});

test("list binary cells show preview only for supported files", async () => {
    await mountView({
        resModel: "qing.preview.ticket",
        type: "list",
        arch: '<list><field name="file" filename="filename" widget="binary"/><field name="filename" column_invisible="1"/></list>',
    });
    expect(".o_data_row .o_preview_file_button").toHaveCount(2);
    expect(".o_data_row .o_form_uri").toHaveCount(3);
});
