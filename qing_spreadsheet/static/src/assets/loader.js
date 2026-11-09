import { Component, useProps, xml } from "@odoo/owl";
import { LazyComponent } from "@web/core/lazy_component";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

export class WorkbookAction extends Component {
    static components = { LazyComponent };
    static template = xml`<LazyComponent bundle="'spreadsheet.o_spreadsheet'" Component="'qing_spreadsheet.WorkbookEditor'" props="this.props"/>`;
    props = useProps(standardActionServiceProps);
}
registry.category("actions").add("qing_spreadsheet.editor", WorkbookAction);
