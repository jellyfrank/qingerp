import logging

from sf.api import SF
from sf.model.address import Address
from sf.model.cargo import CargoDetail
from sf.model.contact import ContactInfo
from sf.order import QUERY_URL

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

EXPRESS_TYPES = [(code, name) for code, _product, name in SF.get_express_types()]
LANGUAGE_TYPES = [
    ('follow', 'Follow User Language'),
    ('fixed', 'Fixed'),
]
LANGUAGES = [
    ('zh-CN', 'Chinese (Simplified)'),
    ('zh-TW', 'Chinese (Taiwan)'),
    ('zh-HK', 'Chinese (Hong Kong)'),
    ('zh-MO', 'Chinese (Macau)'),
    ('en', 'English'),
]
PAYMENT_METHODS = [
    ('1', 'Sender Pays'),
    ('2', 'Receiver Pays'),
    ('3', 'Third Party Pays'),
]
CONTACT_SENDER = 1
CONTACT_RECEIVER = 2
CANCEL_DEAL_TYPE = 2


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    def _qing_secret_values(self):
        values = super()._qing_secret_values()
        for carrier in self.filtered(lambda c: c.delivery_type == 'sf'):
            values += [carrier.sf_checkword, carrier.sf_sandbox_checkword]
        return values

    @api.model
    def _sf_language_code(self, lang):
        """Map an Odoo language code to an SF response language."""
        lang = lang or 'zh_CN'
        if lang.startswith('en'):
            return 'en'
        code = lang.replace('_', '-')
        return code if code in dict(LANGUAGES) else 'zh-CN'

    def _sf_language(self):
        self.ensure_one()
        if self.sf_language_type == 'fixed':
            return self.sf_language or 'zh-CN'
        return self._sf_language_code(self.env.lang)

    def _sf_client(self):
        """Return an SF SDK client; credentials are read with sudo (system-only fields)."""
        self.ensure_one()
        carrier = self.sudo()
        checkword = carrier.sf_checkword if carrier.prod_environment else carrier.sf_sandbox_checkword
        if not carrier.sf_client_code or not checkword:
            raise UserError(self.env._(
                'Set the SF client code and check word on delivery method %s.', self.display_name))
        return SF(carrier.sf_client_code, checkword, sandbox=not carrier.prod_environment,
                  language=self._sf_language())

    def _sf_contact(self, partner, contact_type):
        values = self._qing_contact_values(partner)
        return ContactInfo(
            values['address'],
            country=values['country_code'],
            contactType=contact_type,
            company=values['company'] or None,
            contact=values['name'],
            mobile=values['phone'],
            province=values['province'] or None,
            city=values['city'] or None,
            postCode=values['zip'] or None,
        )

    def _sf_address(self, partner):
        values = self._qing_contact_values(partner)
        return Address(
            values['province'] or None,
            values['city'] or None,
            values['district'] or None,
            values['street'] or None,
        )

    def _sf_cargos(self, picking, international):
        cargos = []
        for commodity in self._qing_get_commodities(picking):
            extra = {}
            if international:
                extra = {
                    'count': commodity['quantity'],
                    'unit': commodity['unit'],
                    'weight': commodity['weight'],
                    'amount': commodity['unit_price'],
                    'currency': commodity['currency'],
                    'sourceArea': commodity['country_of_origin'] or None,
                }
            cargos.append(CargoDetail(commodity['name'], cargoDeclaredValue=commodity['value'], **extra))
        return cargos

    def _sf_template_code(self):
        self.ensure_one()
        if not self.sf_template_id:
            raise UserError(self.env._('Select an SF label template on delivery method %s.', self.display_name))
        return f'{self.sf_template_id.code}_{self.sudo().sf_client_code}'

    def _sf_check_result(self, result, operation):
        if result.get('success'):
            return result
        message = result.get('errorMsg') or result.get('errorMessage') or result.get('msg') or ''
        raise UserError(self.env._(
            'SF Express %(operation)s failed: %(error)s',
            operation=operation, error=self._qing_redact(message)))

    def _sf_monthly_card(self, sender):
        return sender.sf_monthly_card or self.sf_monthly_card or None

    def _sf_order_values(self, picking, sender):
        receiver = picking.partner_id
        values = {
            'cargoDesc': self._qing_get_goods_name(picking),
            'expressTypeId': int(picking._sf_express_type()),
            'payMethod': int(picking._sf_payment_method()),
            'monthlyCard': self._sf_monthly_card(sender),
        }
        if receiver.country_id.code not in (False, 'CN'):
            currency = picking.sf_declared_currency_id or self.sf_declared_currency_id \
                or self._qing_get_currency(picking)
            values['customsInfo'] = {
                'declaredValue': picking.sf_declared_value or self.sf_declared_value,
                'declaredValueCurrency': currency.name,
            }
        return values

    def _sf_print_labels(self, client, tracking_number):
        template = self._sf_template_code()
        return self._qing_call(
            self.env._('label printing'),
            lambda: client.sheet.sync_print(template, [{'masterWaybillNo': tracking_number}]),
        )

    def _sf_create_shipment(self, client, picking):
        receiver = picking.partner_id
        sender = self._qing_get_sender(picking=picking)
        self._qing_check_contact(receiver, self.env._('receiver'))
        self._qing_check_contact(sender, self.env._('sender'))
        international = receiver.country_id.code not in (False, 'CN')
        reference = self._qing_get_reference(picking)
        contacts = [self._sf_contact(receiver, CONTACT_RECEIVER), self._sf_contact(sender, CONTACT_SENDER)]
        cargos = self._sf_cargos(picking, international)
        values = self._sf_order_values(picking, sender)
        self._qing_log_payload({
            'orderId': reference,
            'contactInfoList': [contact.to_dict() for contact in contacts],
            'cargoDetails': [cargo.to_dict() for cargo in cargos],
            **values,
        }, 'sf_create_order')
        result = self._qing_call(
            self.env._('waybill creation'),
            lambda: client.order.create_order(reference, contacts, cargos, **values),
        )
        self._qing_log_payload(result, 'sf_create_order_response')
        self._sf_check_result(result, self.env._('waybill creation'))
        tracking_number = result['msgData']['waybillNoInfoList'][0]['waybillNo']
        picking.qing_carrier_order_ref = reference
        return tracking_number

    def _sf_attach_labels(self, client, picking, tracking_number):
        """Attach labels; a failure must not lose a waybill already created at SF."""
        try:
            labels = self._sf_print_labels(client, tracking_number)
        except UserError as error:
            self._qing_post_shipment(picking, tracking_number)
            picking.message_post(body=self.env._(
                'The SF label could not be printed: %s Use Print SF Label to try again.', error))
            return
        self._qing_post_shipment(picking, tracking_number, labels)

    delivery_type = fields.Selection(selection_add=[('sf', 'SF Express')], ondelete={
        'sf': lambda carriers: carriers.write({'delivery_type': 'fixed', 'fixed_price': 0.0}),
    })
    sf_checkword = fields.Char(string='Check Word', groups='base.group_system')
    sf_client_code = fields.Char(string='Client Code', groups='base.group_system')
    sf_declared_currency_id = fields.Many2one('res.currency', string='Declared Value Currency')
    sf_declared_value = fields.Float(string='Declared Value', help='Default declared value for cross-border shipments.')
    sf_default_express_type = fields.Selection(EXPRESS_TYPES, string='Product Type', default='1')
    sf_default_payment = fields.Selection(PAYMENT_METHODS, string='Payment Method', default='1')
    sf_language = fields.Selection(LANGUAGES, string='Language', default='zh-CN')
    sf_language_type = fields.Selection(LANGUAGE_TYPES, string='Response Language', default='follow')
    sf_manual_order = fields.Boolean(
        string='Order Before Validation',
        help='Show an "Order SF" button so the waybill can be created before the transfer is validated.',
    )
    sf_monthly_card = fields.Char(string='Monthly Card', help='SF Express monthly settlement card number.')
    sf_payment_select = fields.Boolean(
        string='Choose Payment on Transfers', help='Let users choose the payment method on each transfer.')
    sf_sandbox_checkword = fields.Char(string='Sandbox Check Word', groups='base.group_system')
    sf_select_express_type = fields.Boolean(
        string='Choose Product on Transfers', help='Let users choose the SF product type on each transfer.')
    sf_template_id = fields.Many2one('qing.delivery.sf.template', string='Label Template')

    def sf_rate_shipment(self, order):
        self.ensure_one()
        client = self._sf_client()
        sender = self._qing_get_sender(order=order)
        weight = round(order._get_estimated_weight(), 2) or None
        result = self._qing_call(
            self.env._('rate estimate'),
            lambda: client.order.query_delivery(
                self.sf_default_express_type,
                self._sf_address(order.partner_shipping_id),
                self._sf_address(sender),
                weight=weight,
                searchPrice=1,
            ),
        )
        if not result.get('success'):
            return {
                'success': False,
                'price': 0.0,
                'error_message': self._qing_redact(result.get('errorMsg') or ''),
                'warning_message': False,
            }
        quotes = (result.get('msgData') or {}).get('deliverTmDto') or []
        if not quotes:
            return {
                'success': False,
                'price': 0.0,
                'error_message': self.env._('SF Express returned no price for this route.'),
                'warning_message': False,
            }
        company = self.company_id or order.company_id or self.env.company
        price = self.env.ref('base.CNY')._convert(
            float(quotes[0].get('fee') or 0.0), company.currency_id, company, order.date_order or fields.Date.today())
        return {'success': True, 'price': price, 'error_message': False, 'warning_message': False}

    def sf_send_shipping(self, pickings):
        self.ensure_one()
        client = self._sf_client()
        self._sf_template_code()
        results = []
        for picking in pickings:
            tracking_number = self._sf_create_shipment(client, picking)
            self._sf_attach_labels(client, picking, tracking_number)
            results.append({'exact_price': 0.0, 'tracking_number': tracking_number})
        return results

    def sf_get_tracking_link(self, picking):
        return f'{QUERY_URL}{(picking.carrier_tracking_ref or "").split(",")[0]}'

    def sf_cancel_shipment(self, pickings):
        self.ensure_one()
        client = self._sf_client()
        for picking in pickings.filtered('carrier_tracking_ref'):
            reference = picking.qing_carrier_order_ref
            if not reference:
                if self.qing_reference_type == 'random':
                    raise UserError(self.env._(
                        'The SF order number of %s is unknown; cancel it in the SF portal.', picking.display_name))
                reference = self._qing_get_reference(picking)
            result = self._qing_call(
                self.env._('cancellation'),
                lambda: client.order.confirm_order(reference, dealType=CANCEL_DEAL_TYPE),
            )
            self._sf_check_result(result, self.env._('cancellation'))
            # The native cancel_shipment logs and clears carrier_tracking_ref afterwards.
            picking.write({
                'carrier_price': 0.0,
                'qing_carrier_order_ref': False,
                'qing_tracking_date': False,
                'qing_tracking_detail': False,
            })

    def sf_qing_get_tracking_events(self, picking):
        self.ensure_one()
        client = self._sf_client()
        tracking_number = picking.carrier_tracking_ref.split(',')[0]
        result = self._qing_call(
            self.env._('route lookup'), lambda: client.order.get_route_info(tracking_number))
        self._sf_check_result(result, self.env._('route lookup'))
        responses = (result.get('msgData') or {}).get('routeResps') or [{}]
        routes = responses[0].get('routes') or []
        if isinstance(routes, dict):
            routes = [routes]
        return [{
            'time': route.get('acceptTime') or '',
            'description': route.get('remark') or '',
            'location': route.get('acceptAddress') or '',
        } for route in routes]

    def sf_reprint_label(self, picking):
        self.ensure_one()
        if not picking.carrier_tracking_ref:
            raise UserError(self.env._('%s has no SF waybill yet.', picking.display_name))
        client = self._sf_client()
        tracking_number = picking.carrier_tracking_ref.split(',')[0]
        labels = self._sf_print_labels(client, tracking_number)
        self._qing_post_shipment(picking, tracking_number, labels)

    def action_sf_sync_templates(self):
        """Add or update custom label templates defined in the SF account."""
        self.ensure_one()
        client = self._sf_client()
        result = self._qing_call(
            self.env._('template synchronization'), lambda: client.sheet.get_custom_templates(''))
        self._sf_check_result(result, self.env._('template synchronization'))
        templates = self.env['qing.delivery.sf.template'].sudo()
        existing = {template.code: template for template in templates.search([])}
        count = 0
        for item in result.get('obj') or []:
            code, name = item.get('customTemplateCode'), item.get('customTemplateName')
            if not code:
                continue
            values = {'name': name or code, 'code': code, 'custom': True}
            if code in existing:
                existing[code].write(values)
            else:
                templates.create(values)
            count += 1
        return self._qing_notify(
            self.env._('%s SF label templates synchronized.', count),
            notification_type='success', reload=True,
        )
