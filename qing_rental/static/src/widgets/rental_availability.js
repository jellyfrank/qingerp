import { Component, t, useProps } from "@odoo/owl";
import { formatFloat } from "@web/views/fields/formatters";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

function availabilityLabel(record) {
    return record.data.availability_state === "available"
        ? _t("Sufficient rental availability")
        : _t("Insufficient rental availability");
}

class RentalAvailabilityPopover extends Component {
    static template = "qing_rental.AvailabilityPopover";
    props = useProps({ record: t.object(), close: t.function() });
    formatQuantity = formatFloat;

    get label() {
        return availabilityLabel(this.props.record);
    }
}

class RentalAvailability extends Component {
    static template = "qing_rental.Availability";
    props = useProps(standardWidgetProps);

    setup() {
        this.popover = usePopover(RentalAvailabilityPopover, { position: "top" });
    }

    get label() {
        return availabilityLabel(this.props.record);
    }

    showPopup(event) {
        this.popover.open(event.currentTarget, { record: this.props.record });
    }
}

registry.category("view_widgets").add("qing_rental_availability", {
    component: RentalAvailability,
    fieldDependencies: [
        { name: "availability_state", type: "selection" },
        { name: "available_qty", type: "float" },
        { name: "required_qty", type: "float" },
    ],
});
