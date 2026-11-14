import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { checkFileSize } from "@web/core/utils/files";
import { useService } from "@web/core/utils/hooks";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { ImageField, imageField } from "@web/views/fields/image/image_field";

/**
 * Return the first image exposed by a browser paste event.
 *
 * ClipboardItem is preferred because several screenshot tools do not populate
 * clipboardData.files. The files fallback deliberately searches all files so
 * a non-image entry before the screenshot does not prevent a valid paste.
 */
export function getClipboardImage(clipboardData) {
    for (const item of clipboardData?.items || []) {
        if (item.kind === "file" && item.type?.startsWith("image/")) {
            const file = item.getAsFile?.();
            if (file) {
                return file;
            }
        }
    }
    return [...(clipboardData?.files || [])].find((file) => file.type?.startsWith("image/"));
}

/**
 * Build the same payload that web.FileUploader passes to ImageField.
 * This allows pasted images to retain native Odoo processing such as WebP
 * conversion and report-oriented alternate image attachments.
 */
export async function getPastedImageUploadInfo(file) {
    const dataUrl = await getDataURLFromFile(file);
    return {
        name: file.name || "clipboard-image",
        size: file.size,
        type: file.type,
        data: dataUrl.split(",")[1],
        objectUrl: null,
    };
}

export class ImagePasteField extends ImageField {
    static template = "qing_base.ImagePasteField";

    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.fileViewer = useFileViewer();
    }

    openImagePreview() {
        if (!this.props.record.data[this.props.name] || !this.state.isValid) {
            return;
        }
        const fieldName = this.fieldType === "many2one" ? this.props.previewImage : this.props.name;
        const source = this.getUrl(fieldName);
        this.fileViewer.open({
            name: this.imgAlt,
            defaultSource: source,
            downloadUrl: source,
            isImage: true,
            isViewable: true,
        });
    }

    async onPaste(ev) {
        if (this.props.readonly || this.fieldType === "many2one") {
            return;
        }

        const file = getClipboardImage(ev.clipboardData);
        if (!file) {
            return;
        }

        ev.preventDefault();
        ev.stopPropagation();

        if (!checkFileSize(file.size, this.notification)) {
            return;
        }

        if (this.props.record.data[this.props.name] && !(await this._confirmReplacement())) {
            return;
        }

        try {
            await this.onFileUploaded(await getPastedImageUploadInfo(file));
        } catch (error) {
            console.warn("Unable to process pasted image", error);
            this.notification.add(_t("The pasted image could not be processed."), {
                type: "danger",
            });
        }
    }

    _confirmReplacement() {
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Replace image"),
                body: _t("This field already contains an image. Do you want to replace it?"),
                confirmLabel: _t("Replace"),
                cancelLabel: _t("Cancel"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
                dismiss: () => resolve(false),
            });
        });
    }
}

export const imagePasteField = {
    ...imageField,
    component: ImagePasteField,
};

registry.category("fields").add("image_paste", imagePasteField);
