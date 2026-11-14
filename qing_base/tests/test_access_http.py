import json
from unittest.mock import patch

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAccessHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(cls.env, login='qing_access_http_user', groups='base.group_user')

    def test_access_error_response_does_not_expose_rule_details(self):
        self.authenticate(self.operator.login, self.operator.login)
        # The test user lives in the test transaction; do not write an event
        # through the independent production diagnostic cursor for that user.
        with patch('odoo.addons.qing_base.tools.access_events.enqueue'):
            response = self.url_open('/web/dataset/call_kw/qing.access.event/search_read', data=json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'call',
                'params': {'model': 'qing.access.event', 'method': 'search_read', 'args': [],
                           'kwargs': {'domain': [], 'fields': ['event_type']}},
            }), headers={'Content-Type': 'application/json'})
        data = response.json()['error']['data']
        self.assertEqual(data['name'], 'odoo.exceptions.AccessError')
        self.assertIn('access is restricted', data['message'])
        self.assertEqual(data['context']['qing_access']['model'], 'qing.access.event')
        self.assertEqual(data['context']['qing_access']['operation'], 'read')
        serialized = json.dumps(data)
        self.assertNotIn('following groups', serialized)
        self.assertNotIn('base.group_system', serialized)
        self.assertNotIn('Traceback', data['debug'])
