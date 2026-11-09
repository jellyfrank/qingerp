from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQingModuleCatalog(TransactionCase):
    def test_full_registry_repairs_only_owned_empty_classifications(self):
        Module = self.env['ir.module.module']
        if 'module_type' not in Module._fields:
            self.skipTest('Install base_import_module to exercise its catalog classification.')
        own = Module.search([('name', '=', 'qing_base')])
        third_party = Module.search([('name', '=', 'base_setup')])
        original_states = (own | third_party).mapped('state')
        own.module_type = 'industries'
        Module._register_hook()
        self.assertEqual(own.module_type, 'industries')
        own.module_type = False
        third_party.module_type = False
        orphan = Module.create({'name': 'qing_missing_catalog_test', 'module_type': False})
        Module._register_hook()
        self.assertEqual(own.module_type, 'official')
        self.assertFalse(third_party.module_type)
        self.assertFalse(orphan.module_type)
        self.assertEqual((own | third_party).mapped('state'), original_states)
        self.assertFalse(Module._qing_complete_catalog_classification())
