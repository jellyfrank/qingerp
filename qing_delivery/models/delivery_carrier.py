import json
import logging
import re
import secrets

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SENDER_TYPES = [
    ('company', 'Company Address'),
    ('warehouse', 'Warehouse Address'),
    ('fixed', 'Fixed Contact'),
]
REFERENCE_TYPES = [
    ('origin', 'Source Document'),
    ('name', 'Transfer Reference'),
    ('both', 'Source Document and Transfer Reference'),
    ('random', 'Transfer Reference with Random Suffix'),
]
REDACTED = '***'
SECRET_MIN_LENGTH = 4


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    @api.model
    def _qing_address_lines(self, partner):
        """Return the ordered address parts used to build a printable address."""
        if partner.country_id.code != 'CN':
            parts = [partner.street, partner.street2, partner.city, partner.state_id.name, partner.zip]
            return [part for part in parts if part]
        parts = []
        for part in (
            partner.state_id.name,
            partner.city_id.name if partner.city_id.qing_cn_code else partner.city,
            partner.qing_cn_district_id.name,
            partner.qing_cn_subdistrict_id.name,
            partner.street,
            partner.street2,
        ):
            # Municipalities repeat the province as city name; skip duplicates.
            if part and part not in parts:
                parts.append(part)
        return parts

    @api.model
    def _qing_normalize_phone(self, partner):
        """Return a carrier-friendly phone number: national digits for China."""
        number = (partner.phone or '').strip()
        if not number:
            return ''
        if hasattr(partner, '_phone_format'):
            force_format = 'NATIONAL' if partner.country_id.code in (False, 'CN') else 'INTERNATIONAL'
            number = partner._phone_format(
                number=number, country=partner.country_id or self.env.ref('base.cn'),
                force_format=force_format,
            ) or number
        if partner.country_id.code in (False, 'CN'):
            return re.sub(r'\D', '', number)
        return re.sub(r'[^\d+]', '', number)

    def _qing_check_contact(self, partner, role):
        """Raise a readable error when a carrier cannot use the given contact."""
        self.ensure_one()
        missing = []
        if not partner.street:
            missing.append(self.env._('street'))
        if partner.country_id.code == 'CN' and not partner.state_id:
            missing.append(self.env._('province'))
        if not (partner.city_id or partner.city):
            missing.append(self.env._('city'))
        if not partner.phone:
            missing.append(self.env._('phone'))
        if missing:
            raise UserError(self.env._(
                'The %(role)s contact %(partner)s is missing: %(fields)s.',
                role=role, partner=partner.display_name, fields=', '.join(missing),
            ))

    def _qing_contact_values(self, partner):
        """Return carrier-neutral contact values built from the QingERP address structure."""
        self.ensure_one()
        city = partner.city_id.name if partner.city_id.qing_cn_code else partner.city
        company = partner.commercial_company_name or (partner.parent_id.name if partner.parent_id else '')
        name = partner.name or company or ''
        separator = '' if partner.country_id.code == 'CN' else ', '
        return {
            'address': separator.join(self._qing_address_lines(partner)),
            'city': city or '',
            'company': company if company != name else '',
            'country_code': partner.country_id.code or 'CN',
            'district': partner.qing_cn_district_id.name or '',
            'email': partner.email or '',
            'name': name,
            'phone': self._qing_normalize_phone(partner),
            'province': partner.state_id.name or '',
            'street': ' '.join(part for part in (partner.street, partner.street2) if part),
            'subdistrict': partner.qing_cn_subdistrict_id.name or '',
            'zip': partner.zip or '',
        }

    def _qing_get_sender(self, picking=None, order=None):
        """Return the shipping origin contact according to the sender policy."""
        self.ensure_one()
        document = picking or order
        if self.qing_sender_type == 'fixed':
            sender = self.qing_fixed_sender_id
        elif self.qing_sender_type == 'warehouse':
            if picking:
                warehouse = picking.picking_type_id.warehouse_id or picking.location_id.warehouse_id
            else:
                warehouse = order.warehouse_id if order else self.env['stock.warehouse']
            sender = warehouse.partner_id
        else:
            company = document.company_id if document else (self.company_id or self.env.company)
            sender = company.partner_id
        if not sender:
            raise UserError(self.env._('Set a sender address on delivery method %s.', self.display_name))
        return sender

    def _qing_get_reference(self, picking):
        """Return the customer order reference sent to the carrier."""
        self.ensure_one()
        reference_type = self.qing_reference_type
        if reference_type == 'name':
            return picking.name
        if reference_type == 'both':
            return '-'.join(part for part in (picking.origin, picking.name) if part)
        if reference_type == 'random':
            return f'{picking.name}-{secrets.token_hex(2).upper()}'
        return picking.origin or picking.name

    def _qing_get_goods_name(self, picking=None):
        """Return the goods description: transfer value first, then the carrier default."""
        self.ensure_one()
        return (picking and picking.qing_goods_name) or self.qing_goods_name or self.env._('Goods')

    def _qing_get_currency(self, picking):
        """Return the commercial currency of a transfer."""
        if picking.sale_id:
            return picking.sale_id.currency_id
        if 'purchase_id' in picking._fields and picking.purchase_id:
            return picking.purchase_id.currency_id
        return picking.company_id.currency_id

    def _qing_get_commodities(self, picking):
        """Return one carrier-neutral commodity per move of the transfer."""
        currency = self._qing_get_currency(picking)
        commodities = []
        for move in picking.move_ids.filtered(lambda m: m.state != 'cancel' and m.product_uom_qty):
            unit_price = move.sale_line_id.price_unit if move.sale_line_id else move.product_id.lst_price
            commodities.append({
                'country_of_origin': move.product_id.country_of_origin.code or '',
                'currency': currency.name,
                'hs_code': move.product_id.hs_code or '',
                'name': move.product_id.display_name,
                'quantity': move.product_uom_qty,
                'unit': move.uom_id.name,
                'unit_price': currency.round(unit_price),
                'value': currency.round(unit_price * move.product_uom_qty),
                'weight': move.product_id.weight,
            })
        return commodities

    def _qing_secret_values(self):
        """Return credential values that must never appear in errors or logs.

        Connectors extend this hook with their own credential fields.
        """
        return []

    def _qing_redact(self, text):
        """Replace configured credentials in a text."""
        text = str(text)
        for secret in self.sudo()._qing_secret_values():
            if secret and len(secret) >= SECRET_MIN_LENGTH:
                text = text.replace(secret, REDACTED)
        return text

    def _qing_call(self, operation, function, *args, **kwargs):
        """Call a carrier SDK function and turn failures into readable, redacted errors."""
        self.ensure_one()
        try:
            return function(*args, **kwargs)
        except UserError:
            raise
        except Exception as error:  # noqa: BLE001 - SDKs raise bare exceptions
            message = self._qing_redact(error)
            # No traceback: request exceptions can embed credentials in URLs.
            _logger.warning('Carrier %s: %s failed: %s', self.delivery_type, operation, message)
            raise UserError(self.env._(
                '%(carrier)s: %(operation)s failed: %(error)s',
                carrier=self.display_name, operation=operation, error=message,
            )) from None

    def _qing_log_payload(self, payload, operation):
        """Store a redacted request or response through native debug logging."""
        self.ensure_one()
        if not self.debug_logging:
            return
        text = json.dumps(payload, ensure_ascii=False, default=str)
        self.log_xml(self._qing_redact(text), operation)

    def _qing_post_shipment(self, picking, tracking_number, labels=None, label_type='pdf'):
        """Post the tracking number and label files on the transfer."""
        self.ensure_one()
        labels = labels or []
        prefix = self._get_delivery_label_prefix()
        attachments = []
        for index, content in enumerate(labels, start=1):
            suffix = f'-{index}' if len(labels) > 1 else ''
            attachments.append((f'{prefix}-{tracking_number}{suffix}.{label_type}', content))
        body = Markup('%s<br/><b>%s</b> %s') % (
            self.env._('Shipment created with %s.', self.display_name),
            self.env._('Tracking number:'),
            tracking_number,
        )
        picking.message_post(body=body, attachments=attachments)

    def _qing_tracking_supported(self):
        self.ensure_one()
        return hasattr(self, f'{self.delivery_type}_qing_get_tracking_events')

    def _qing_get_tracking_events(self, picking):
        """Dispatch to ``<delivery_type>_qing_get_tracking_events``.

        Connectors return a list of dicts with ``time``, ``description`` and ``location``.
        """
        self.ensure_one()
        if not self._qing_tracking_supported():
            raise UserError(self.env._('%s does not provide tracking details.', self.display_name))
        return getattr(self, f'{self.delivery_type}_qing_get_tracking_events')(picking)

    qing_fixed_sender_id = fields.Many2one(
        'res.partner', string='Fixed Sender',
        help='Contact used as sender when the sender type is a fixed contact.',
    )
    qing_goods_name = fields.Char(
        string='Default Goods Description', translate=True,
        help='Sent to the carrier when the transfer has no goods description.',
    )
    qing_reference_type = fields.Selection(
        REFERENCE_TYPES, string='Customer Order Reference', default='origin', required=True,
        help='Reference sent to the carrier as customer order number. It must be unique for most carriers; '
             'use the random suffix to ship the same transfer again after a cancellation.',
    )
    qing_sender_type = fields.Selection(
        SENDER_TYPES, string='Sender Address', default='company', required=True,
    )

    def install_more_provider(self):
        action = super().install_more_provider()
        action['domain'] = ['|', ['name', '=like', 'qing_delivery_%']] + action['domain']
        return action
