import json
import hmac

from odoo import http
from odoo.http import request
from ..services.cloud_api import valid_signature


class WhatsappWebhook(http.Controller):
    @http.route('/qing_whatsapp/webhook/<string:key>', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, save_session=False)
    def webhook(self, key, **kwargs):
        Account = request.env['qing.whatsapp.account'].sudo()
        domain = [('webhook_key', '=', key), ('active', '=', True)]
        account = Account.search(domain, limit=1)
        if not account:
            return request.make_response('Unknown account', status=404)
        if request.httprequest.method == 'GET':
            token = kwargs.get('hub.verify_token', '')
            if kwargs.get('hub.mode') == 'subscribe' and token and hmac.compare_digest(token, account.verify_token or ''):
                return request.make_response(kwargs.get('hub.challenge', ''), status=200)
            return request.make_response('Forbidden', status=403)
        if request.httprequest.content_length and request.httprequest.content_length > 1024 * 1024:
            return request.make_response('Too large', status=413)
        raw = request.httprequest.get_data()
        if len(raw) > 1024 * 1024:
            return request.make_response('Too large', status=413)
        if not valid_signature(raw, request.httprequest.headers.get('X-Hub-Signature-256'), account.app_secret):
            return request.make_response('Forbidden', status=403)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('entry'), list):
                raise ValueError()
            events = []
            for entry in data['entry']:
                if str(entry.get('id')) != account.waba_uid:
                    continue
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if change.get('field') == 'messages':
                        phone_uid = str(value.get('metadata', {}).get('phone_number_id', ''))
                        target = Account.search([('phone_uid', '=', phone_uid), ('waba_uid', '=', account.waba_uid)], limit=1)
                        if not target or not hmac.compare_digest(target.app_secret or '', account.app_secret or ''):
                            continue
                        for item in value.get('messages', []):
                            events.append((target, 'message', item))
                        for item in value.get('statuses', []):
                            events.append((target, 'status', item))
                    elif value.get('message_template_id'):
                        for target in Account.search([('waba_uid', '=', account.waba_uid)]):
                            if hmac.compare_digest(target.app_secret or '', account.app_secret or ''):
                                events.append((target, 'template', value))
            if any(not isinstance(item, dict) for _, _, item in events):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return request.make_response('Invalid payload', status=400)
        Event = request.env['qing.whatsapp.event']
        for target, kind, item in events:
            Event._receive(target, kind, item)
        return request.make_response('EVENT_RECEIVED', status=200)
