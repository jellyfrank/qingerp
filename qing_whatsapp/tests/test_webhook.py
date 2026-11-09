import hashlib
import hmac
import json

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestWebhook(HttpCase):
    def test_signed_http_and_duplicate(self):
        account = self.env['qing.whatsapp.account'].create({'name': 'HTTP WA', 'phone_uid': '998877',
            'waba_uid': '665544', 'app_secret': 'http-secret'})
        path = '/qing_whatsapp/webhook/' + account.webhook_key
        challenge = self.url_open(path + '?hub.mode=subscribe&hub.verify_token=' + account.verify_token + '&hub.challenge=123')
        self.assertEqual(challenge.status_code, 200)
        self.assertEqual(challenge.text, '123')
        data = {'entry': [{'id': account.waba_uid, 'changes': [{'field': 'messages', 'value': {
            'metadata': {'phone_number_id': account.phone_uid}, 'messages': [
                {'id': 'http1', 'from': '32499000000', 'type': 'text', 'text': {'body': 'Hi'}, 'timestamp': '1789000000'}]}}]}]}
        raw = json.dumps(data).encode()
        headers = {'Content-Type': 'application/json', 'X-Hub-Signature-256': 'sha256=' + hmac.new(b'http-secret', raw, hashlib.sha256).hexdigest()}
        response = self.url_open(path, data=raw, headers=headers)
        self.assertEqual(response.status_code, 200)
        response = self.url_open(path, data=raw, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env['qing.whatsapp.event'].search_count([('account_id', '=', account.id)]), 1)
        response = self.url_open(path, data=raw + b' ', headers=headers)
        self.assertEqual(response.status_code, 403)
