from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..protocol import ProtocolError, canonical, validate_result

PROVIDER_CODES = [('none', 'No Adapter Installed')]
ENVIRONMENTS = [('test', 'Test'), ('production', 'Production')]
PROVIDER_ROUTING_FIELDS = frozenset({'code', 'company_id', 'environment'})


class FapiaoProvider(models.Model):
    _name = 'qing.fapiao.provider'
    _description = 'Invoice Provider Account'
    _check_company_auto = True
    _order = 'name, id'

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group('qing_fapiao.group_fapiao_manager'):
            raise AccessError(_('Only invoice interface managers may perform this operation.'))
        self.check_access('read')
        if not self.env.su and any(provider.company_id != self.env.company for provider in self):
            raise AccessError(_('Switch to the invoice provider company before continuing.'))

    def _get_capabilities(self):
        """Adapters extend this dictionary and implement the corresponding private hooks."""
        self.ensure_one()
        return {
            'issue_blue': False, 'query': False, 'documents': False, 'callback': False,
            'invoice_types': [], 'currencies': [],
        }

    def _check_configuration(self):
        self.ensure_one()
        if not self.seller_tax_id or self.seller_tax_id != self.seller_tax_id.strip():
            raise ValidationError(_('Set the exact seller tax ID for this provider account.'))
        return True

    def _check_order_prerequisites(self, payload):
        self.ensure_one()
        self._check_manager()
        self._check_configuration()
        capabilities = self._get_capabilities()
        if not self.enabled or not capabilities.get('issue_blue') or not capabilities.get('query'):
            raise UserError(_('Enable an invoice provider supporting issuance and result queries.'))
        if payload['invoice_type'] not in capabilities.get('invoice_types', []):
            raise ValidationError(_('The provider does not support this invoice type.'))
        if payload['currency'] not in capabilities.get('currencies', []):
            raise ValidationError(_('The provider does not support this invoice currency.'))

    def _check_order(self, payload):
        self._check_order_prerequisites(payload)
        if payload['seller']['tax_id'] != self.seller_tax_id:
            raise ValidationError(_('The provider seller tax ID differs from the approved invoice.'))

    def _check_existing_order(self, order):
        self.ensure_one()
        self._check_order_prerequisites(order.snapshot)

    def _submit_invoice(self, order):
        raise NotImplementedError('Invoice issuance adapter is not installed.')

    def _query_invoice(self, order):
        raise NotImplementedError('Invoice query adapter is not installed.')

    def _fetch_documents(self, order):
        raise NotImplementedError('Invoice document adapter is not installed.')

    def _verify_notification(self, headers, body):
        """Return event_id, request_key and normalized result only after authenticating the sender."""
        raise NotImplementedError('Invoice notification adapter is not installed.')

    def _process_notification(self, headers, body):
        """Trusted adapter controllers call this private entry; no public generic route exists."""
        self.ensure_one()
        if not self._get_capabilities().get('callback'):
            raise ValidationError(_('This provider does not support notifications.'))
        notification = canonical(self._verify_notification(headers, body))
        if not isinstance(notification, dict):
            raise ValidationError(_('Invalid verified invoice notification.'))
        event_id = notification.get('event_id')
        request_key = notification.get('request_key')
        if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
            raise ValidationError(_('Invalid invoice notification identifier.'))
        if not isinstance(request_key, str) or not request_key or len(request_key) > 128:
            raise ValidationError(_('Invalid invoice request identifier.'))
        Order = self.env['qing.fapiao.order']
        domain = [('provider_id', '=', self.id), ('request_key', '=', request_key)]
        order = Order.search(domain, limit=1)
        if not order:
            raise ValidationError(_('The notification does not match an invoice order.'))
        order._lock()
        try:
            result = validate_result(notification.get('result'), order.snapshot, order.currency_id.decimal_places)
        except ProtocolError as error:
            raise ValidationError(str(error)) from error
        Event = self.env['qing.fapiao.event']
        domain = [('order_id', '=', order.id), ('notification_key', '=', event_id)]
        existing = Event.search(domain, limit=1)
        if existing:
            if existing.payload != result:
                raise ValidationError(_('A notification identifier was reused with a different result.'))
            return existing
        return Event._append(order, 'notification', payload=result, notification_key=event_id)

    @api.constrains('seller_tax_id', 'environment', 'code')
    def _check_provider_configuration(self):
        for provider in self:
            provider._check_configuration()

    code = fields.Selection(PROVIDER_CODES, required=True, default='none')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    enabled = fields.Boolean(default=False, copy=False, help='Disable new orders; existing orders still accept queries and notifications.')
    environment = fields.Selection(ENVIRONMENTS, required=True, default='test')
    name = fields.Char(required=True)
    revision = fields.Integer(readonly=True, default=0, copy=False)
    seller_tax_id = fields.Char(required=True)

    @api.model_create_multi
    def create(self, vals_list):
        self._check_manager()
        if any('revision' in values for values in vals_list):
            raise AccessError(_('Provider revision is maintained by the invoice workflow.'))
        clean = self.with_context({key: value for key, value in self.env.context.items() if not key.startswith('default_')})
        providers = super(FapiaoProvider, clean).create(vals_list)
        providers._check_manager()
        return providers

    def write(self, vals):
        self._check_manager()
        if 'revision' in vals:
            raise AccessError(_('Provider revision is maintained by the invoice workflow.'))
        self.env.cr.execute('SELECT id FROM qing_fapiao_provider WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(self.ids)])
        self.invalidate_recordset()
        if PROVIDER_ROUTING_FIELDS.intersection(vals):
            Order = self.env['qing.fapiao.order']
            domain = [('provider_id', 'in', self.ids)]
            if Order.search_count(domain):
                raise UserError(_(
                    'An account with invoice orders cannot change its company, adapter, or environment. '
                    'The seller tax ID may be changed; existing orders keep their saved tax ID.'
                ))
        result = super().write(vals)
        self._check_manager()
        return result

    def unlink(self):
        self._check_manager()
        return super().unlink()
