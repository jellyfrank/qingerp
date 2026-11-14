from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQingNotify(TransactionCase):
    def test_minimal_notification(self):
        action = self.env['res.partner']._qing_notify('Done')
        self.assertEqual(action, {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': 'Done', 'sticky': False, 'type': 'info'},
        })

    def test_options(self):
        action = self.env['res.partner'].browse()._qing_notify(
            'Saved %s', title='Sync', notification_type='success', sticky=1,
            links=[{'label': 'Log', 'url': '/odoo'}], reload=True,
            next_action={'type': 'ir.actions.act_window_close'},
        )
        params = action['params']
        self.assertEqual(params['title'], 'Sync')
        self.assertEqual(params['type'], 'success')
        self.assertIs(params['sticky'], True)
        self.assertEqual(params['links'], [{'label': 'Log', 'url': '/odoo'}])
        # reload wins over an explicit next action
        self.assertEqual(params['next'], {'type': 'ir.actions.client', 'tag': 'soft_reload'})

    def test_next_action_and_invalid_type(self):
        close = {'type': 'ir.actions.act_window_close'}
        action = self.env['res.users']._qing_notify('Bye', next_action=close)
        self.assertEqual(action['params']['next'], close)
        with self.assertRaises(ValueError):
            self.env['res.users']._qing_notify('Oops', notification_type='error')

    def test_not_callable_from_rpc(self):
        # Private (underscore) methods are rejected by the web RPC dispatcher.
        self.assertTrue(self.env['res.partner']._qing_notify.__name__.startswith('_'))
