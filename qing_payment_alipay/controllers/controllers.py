import logging

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from odoo.addons.payment.controllers.payment_status import PaymentStatus


_logger = logging.getLogger(__name__)


class Alipay(PaymentStatus):
    @http.route('/payment/alipay/refund/notify', type='http', auth='none', methods=['POST'], csrf=False)
    def alipay_refund_notify(self, **kwargs):
        raw_body = request.httprequest.get_data()
        data = request.httprequest.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('refundRequestId'), str):
            return request.make_response('failed', status=400)
        Transaction = request.env['payment.transaction'].sudo()
        domain = [('provider_code', '=', 'alipay'), ('operation', '=', 'refund'),
                  ('antom_refund_request_id', '=', data['refundRequestId'])]
        tx = Transaction.search(domain, limit=2)
        if len(tx) != 1:
            return request.make_response('failed', status=400)
        try:
            client = tx._antom_refund_client()
            valid = client.comm.verify_signature(
                'POST', '/payment/alipay/refund/notify', request.httprequest.headers,
                raw_body, timestamp_header='request-time')
            if not valid:
                return request.make_response('failed', status=400)
            tx._antom_record_refund(data, 'notify')
        except (ValueError, TypeError, UserError):
            return request.make_response('failed', status=400)
        return request.make_json_response({'result': {
            'resultCode': 'SUCCESS', 'resultStatus': 'S', 'resultMessage': 'success',
        }})

    @http.route('/payment/alipay/process', type='http', auth='none', methods=['POST', 'GET'], csrf=False)
    def alipay_validate(self, **kwargs):
        # Return flow belongs to the monitored session, not unsigned URL parameters.
        tx = self._get_monitored_transaction()
        if tx and tx.provider_code == 'alipay':
            try:
                tx._alipay_query_and_record()
            except (ValidationError, UserError):
                _logger.warning('Alipay return inquiry could not be validated for %s.', tx.reference)
        return request.redirect('/payment/status')

    @http.route('/payment/alipay/notify', type='http', auth='none', methods=['POST'], csrf=False)
    def alipay_notify(self, **kwargs):
        is_json = request.httprequest.mimetype == 'application/json'
        data = request.httprequest.get_json(silent=True) if is_json else kwargs
        if not isinstance(data, dict):
            return request.make_response('failed', status=400)
        tx = request.env['payment.transaction'].sudo()._search_by_reference('alipay', data)
        if not tx or tx.provider_id.is_global != is_json:
            return request.make_response('failed', status=400)
        try:
            if tx.provider_id.is_global:
                # Payment notifications only wake up a signed server-side inquiry.
                if not tx._alipay_query_and_record():
                    return request.make_response('failed', status=503)
                return request.make_json_response({'result': {
                    'resultCode': 'SUCCESS', 'resultStatus': 'S', 'resultMessage': 'success',
                }})
            if not tx.provider_id._validate_payment_data(data):
                return request.make_response('failed', status=400)
            tx._record(data)
        except (ValidationError, UserError):
            return request.make_response('failed', status=400)
        return 'success'
