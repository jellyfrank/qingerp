from lxml import etree

from odoo import api, models


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        # Extend the resolved Apps architecture without adding a database view.
        # This keeps the backend theme independent of website's required fields.
        if view_type == "kanban" and "o_modules_kanban" in arch.get("class", "").split():
            for footer in arch.xpath(".//t[@t-name='card']//footer"):
                if footer.xpath("./button[contains(@class, 'o_qing_app_manage')]"):
                    continue
                for name, label in (
                    ("button_immediate_upgrade", self.env._("Upgrade")),
                    ("button_uninstall_wizard", self.env._("Uninstall")),
                ):
                    button = etree.SubElement(footer, "button", {
                        "name": name,
                        "type": "object",
                        "class": "btn btn-secondary btn-sm o_qing_app_manage",
                        "invisible": "state != 'installed'",
                        "groups": "base.group_system",
                    })
                    button.text = label
                    if name == "button_immediate_upgrade":
                        button.set("class", "btn btn-primary btn-sm o_qing_app_manage")
                        button.set("confirm", self.env._("Upgrade this module?"))
        return arch, view
