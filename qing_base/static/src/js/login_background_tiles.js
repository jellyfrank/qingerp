import { Component, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const IMAGE_FIELD = "qing_login_background_image";
const COLOR_FIELD = "qing_login_background_color";
const DEFAULT_COLOR = "#dfe9ee";
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
// First base64 character of each accepted format, as in the native ImageField.
const MAGIC_TYPES = { "/": "jpeg", i: "png", U: "webp" };
let nextId = 0;

/**
 * Picture-card picker for the login background style, in the style of the
 * Outlook theme picker. Each card previews its scene; the custom image card
 * appears once an image is uploaded and can be removed. The field values and
 * server-side validation are unchanged, so saving works as before.
 */
export class LoginBackgroundTiles extends Component {
    static template = "qing_base.LoginBackgroundTiles";
    props = useProps(standardFieldProps);

    setup() {
        this.notification = useService("notification");
        this.radioName = `o_qing_login_tiles_${nextId++}`;
        this.labels = {
            group: _t("Login background"),
            upload: _t("Upload image"),
            replace: _t("Replace image"),
            remove: _t("Remove image"),
            invalid: _t("Choose a JPEG, PNG or WebP image up to 2 MB."),
        };
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    get image() {
        return this.props.record.data[IMAGE_FIELD];
    }

    get imageUrl() {
        const image = this.image;
        if (!image) {
            return "";
        }
        if (image.content) {
            return `data:image/${MAGIC_TYPES[image.content[0]] || "png"};base64,${image.content}`;
        }
        // Saved image without loaded content: the settings record serves it.
        const { resId } = this.props.record;
        return resId ? `/qing/login/preview-image/${resId}` : "/qing/login/background";
    }

    get tiles() {
        const selection = this.props.record.fields[this.props.name].selection;
        return selection
            .filter(([value]) => value !== "image" || this.image)
            .map(([value, label]) => ({ value, label, style: this.tileStyle(value) }));
    }

    tileStyle(value) {
        if (value === "image") {
            return `background-image: url("${this.imageUrl}")`;
        }
        if (value === "solid") {
            const color = this.props.record.data[COLOR_FIELD];
            return `background-color: ${/^#[0-9a-f]{6}$/i.test(color || "") ? color : DEFAULT_COLOR}`;
        }
        return "";
    }

    select(value) {
        this.props.record.update({ [this.props.name]: value });
    }

    pickImage() {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = IMAGE_TYPES.join(",");
        input.addEventListener("change", () => this.onFile(input.files[0]), { once: true });
        input.click();
    }

    async onFile(file) {
        if (!file) {
            return;
        }
        if (!IMAGE_TYPES.includes(file.type) || file.size > MAX_IMAGE_BYTES) {
            this.notification.add(this.labels.invalid, { type: "danger" });
            return;
        }
        const dataUrl = await getDataURLFromFile(file);
        await this.props.record.update({
            [IMAGE_FIELD]: { filename: file.name, content: dataUrl.split(",")[1] },
            [this.props.name]: "image",
        });
    }

    removeImage() {
        const changes = { [IMAGE_FIELD]: false };
        if (this.value === "image") {
            changes[this.props.name] = "default";
        }
        this.props.record.update(changes);
    }
}

export const loginBackgroundTiles = {
    component: LoginBackgroundTiles,
    supportedTypes: ["selection"],
};

registry.category("fields").add("qing_login_background_tiles", loginBackgroundTiles);
