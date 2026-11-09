from urllib.parse import urlencode

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

from .service_connection import ServiceHTTPError


class FapiaoProvider(models.Model):
    _inherit = 'qing.fapiao.provider'

    def _get_service_connection(self):
        self.ensure_one()
        connection = self.env['qing.fapiao.service.connection'].search([
            ('company_id', '=', self.company_id.id),
            ('seller_tax_id', '=', self.seller_tax_id),
        ], limit=1)
        if not connection:
            raise ValidationError(_('Apply for the Qing invoice service for this company and seller tax ID first.'))
        return connection

    def _get_capabilities(self):
        if self.code != 'qingerp_cloud':
            return super()._get_capabilities()
        return {
            'issue_blue': True, 'query': True, 'documents': True, 'callback': False,
            'invoice_types': ['normal', 'special'], 'currencies': ['CNY'],
        }

    def _check_configuration(self):
        result = super()._check_configuration()
        if self.code != 'qingerp_cloud':
            return result
        connection = self._get_service_connection()
        connection._validate_endpoint()
        expected_state = 'sandbox' if self.environment == 'test' else 'production'
        if connection.service_state != expected_state:
            raise ValidationError(_('The cloud service has not activated this environment for the seller account.'))
        if self.environment == 'test' and connection.service_state != 'sandbox':
            raise ValidationError(_('The cloud test environment is not active.'))
        if self.environment == 'production' and connection.service_state != 'production':
            raise ValidationError(_('Production access must be explicitly activated by the service operator.'))
        return result

    def _service_response(self, connection, path, payload=None, method=None):
        try:
            result = connection._request_service(path, payload=payload, method=method)
        except ServiceHTTPError as error:
            if error.status_code in (400, 404, 409):
                return {'status': 'rejected', 'error_code': 'SERVICE_REQUEST_REJECTED',
                        'message': 'The hosted service definitively rejected this request before creating a provider task.'}
            raise
        normalized = result.get('result')
        if not isinstance(normalized, dict) or normalized.get('status') not in ('pending', 'unknown', 'issued', 'rejected'):
            raise UserError(_('The invoice service returned no confirmed invoice result. Query the same request again.'))
        return normalized

    def _submit_invoice(self, order):
        if self.code != 'qingerp_cloud':
            return super()._submit_invoice(order)
        connection = self._get_service_connection()
        connection._check_company()
        payload = dict(order.snapshot)
        # The hosted service derives its own tenant-scoped idempotency key from this original key.
        try:
            return self._service_response(connection, '/api/v1/service/fapiao/orders', payload)
        except ServiceHTTPError as error:
            if error.status_code == 403:
                return {'status': 'rejected', 'error_code': 'SERVICE_ACCESS_DENIED',
                        'message': 'Hosted service access was denied before the provider task was created.'}
            raise

    def _query_invoice(self, order):
        if self.code != 'qingerp_cloud':
            return super()._query_invoice(order)
        connection = self._get_service_connection()
        query = urlencode({'request_key': order.snapshot['request_key']})
        return self._service_response(connection, '/api/v1/service/fapiao/order?' + query, method='GET')

    def _fetch_documents(self, order):
        return self._query_invoice(order)

    code = fields.Selection(selection_add=[('qingerp_cloud', 'QingERP Hosted Invoicing Service')],
                            ondelete={'qingerp_cloud': 'set default'})
