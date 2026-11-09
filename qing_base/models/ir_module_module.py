"""Complete Qing catalog metadata after the full registry is available."""
from pathlib import Path

from odoo import api, models
from odoo.modules.module import get_module_path


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    def _register_hook(self):
        super()._register_hook()
        self._qing_complete_catalog_classification()

    @api.model
    def _qing_complete_catalog_classification(self):
        """Repair only empty classifications of Qing addons in this product bundle.

        CLI upgrades discover addons before base_import_module defines module_type.
        The full-registry hook runs after that optional field becomes available.
        """
        if 'module_type' not in self._fields or self.env.cr.readonly:
            return self.browse()
        bundle = Path(__file__).resolve().parents[2]
        domain = [('name', '=like', r'qing\_%'), ('module_type', '=', False)]
        candidates = self.search(domain)
        owned = self.browse()
        for module in candidates:
            addon_path = get_module_path(module.name, display_warning=False)
            if addon_path and Path(addon_path).resolve().parent == bundle:
                owned |= module
        if owned:
            owned.write({'module_type': 'official'})
        return owned
