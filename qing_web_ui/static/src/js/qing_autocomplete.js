/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

patch(Many2XAutocomplete.prototype, {
    get SearchMoreButtonLabel() {
        // Translate the default action in the theme's namespace while retaining custom labels.
        return this.props.searchMoreLabel ?? _t("Search more...");
    },
});
