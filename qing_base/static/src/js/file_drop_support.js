/** @odoo-module **/

import { FileInput } from "@web/core/file_input/file_input";
import { patch } from "@web/core/utils/patch";
import { FileUploader } from "@web/views/fields/file_handler";

import { onWillUnmount } from "@odoo/owl";

const DROP_ACTIVE_CLASS = "o_qing_file_drop_active";

export function hasDraggedFiles(event) {
    return Boolean(event.dataTransfer?.types?.includes("Files"));
}

export function assignDroppedFiles(input, files, multiUpload) {
    const transfer = new DataTransfer();
    const selectedFiles = multiUpload ? [...files] : [...files].slice(0, 1);
    for (const file of selectedFiles) {
        transfer.items.add(file);
    }
    input.files = transfer.files;
    return selectedFiles.length;
}

function getDropTarget(event) {
    return event.currentTarget.closest(".o_field_widget, .o_file_input") || event.currentTarget;
}

function clearDropTarget(component) {
    component.qingDropTarget?.classList.remove(DROP_ACTIVE_CLASS);
    component.qingDropTarget = null;
}

function showDropTarget(component, event) {
    clearDropTarget(component);
    component.qingDropTarget = getDropTarget(event);
    component.qingDropTarget.classList.add(DROP_ACTIVE_CLASS);
}

function onDragEnter(event) {
    if (!hasDraggedFiles(event)) {
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    showDropTarget(this, event);
}

function onDragOver(event) {
    if (!hasDraggedFiles(event)) {
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
}

function onDragLeave(event) {
    if (!this.qingDropTarget && !hasDraggedFiles(event)) {
        return;
    }
    event.stopPropagation();
    if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) {
        clearDropTarget(this);
    }
}

patch(FileInput.prototype, {
    setup() {
        super.setup(...arguments);
        onWillUnmount(() => clearDropTarget(this));
    },

    onQingDragEnter: onDragEnter,
    onQingDragLeave: onDragLeave,
    onQingDragOver: onDragOver,

    async onQingDrop(event) {
        if (!hasDraggedFiles(event)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const droppedFiles = [...event.dataTransfer.files];
        clearDropTarget(this);
        if (this.state.isDisable || !(await this.props.beforeOpen())) {
            return;
        }
        if (!assignDroppedFiles(
            this.fileInputRef(),
            droppedFiles,
            this.props.multiUpload
        )) {
            return;
        }
        await this.onFileInputChange();
    },
});

patch(FileUploader.prototype, {
    setup() {
        super.setup(...arguments);
        onWillUnmount(() => clearDropTarget(this));
    },

    onQingDragEnter: onDragEnter,
    onQingDragLeave: onDragLeave,
    onQingDragOver: onDragOver,

    async onQingDrop(event) {
        if (!hasDraggedFiles(event)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const droppedFiles = [...event.dataTransfer.files];
        clearDropTarget(this);
        if (this.state.isUploading) {
            return;
        }
        if (this.props.onClick) {
            const canUpload = await this.props.onClick(event);
            if (canUpload !== undefined && !canUpload) {
                return;
            }
        }
        if (!assignDroppedFiles(
            this.fileInputRef(),
            droppedFiles,
            this.props.multiUpload
        )) {
            return;
        }
        await this.onFileChange({ target: this.fileInputRef() });
    },
});
