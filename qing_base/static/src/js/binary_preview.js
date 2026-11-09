/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { url } from "@web/core/utils/urls";
import { BinaryField } from "@web/views/fields/binary/binary_field";

const PREVIEW_MIMETYPES = {
    gif: "image/gif",
    jpeg: "image/jpeg",
    jpg: "image/jpeg",
    pdf: "application/pdf",
    png: "image/png",
    webp: "image/webp",
};
const PREVIEWABLE_EXTENSION = /\.(?:pdf|png|jpe?g|gif|webp)$/i;

patch(BinaryField.prototype, {
    get qingCanPreview() {
        const { record, name } = this.props;
        const value = record.data[name];
        if (!value || (!record.resId && !(typeof value === "object" && value.content))) {
            return false;
        }
        return PREVIEWABLE_EXTENSION.test(this.fileName || "");
    },

    onPreviewClick() {
        const { record, name, fileNameField } = this.props;
        const value = record.data[name];
        if (value && typeof value === "object" && value.content) {
            // A newly uploaded file may exist only in the unsaved record.
            const extension = (this.fileName || value.filename || "").split(".").pop().toLowerCase();
            const mimetype = PREVIEW_MIMETYPES[extension] || "application/octet-stream";
            const bytes = Uint8Array.from(atob(value.content), (char) => char.charCodeAt(0));
            const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mimetype }));
            window.open(objectUrl, "_blank", "noopener");
            window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
        } else if (record.resId) {
            // The native route checks access to the record and its field.
            window.open(
                url("/web/content", {
                    model: record.resModel,
                    id: record.resId,
                    field: name,
                    filename_field: fileNameField || "",
                }),
                "_blank",
                "noopener"
            );
        }
    },
});
