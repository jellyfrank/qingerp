import base64
import hashlib
import io
import json

from PIL import Image

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import consteq, float_compare


SIGNATURE_FIELDS = {
    'qing_price_signature', 'qing_price_signed_by', 'qing_price_signed_on', 'qing_signed_price_hash',
    'qing_acceptance_signature', 'qing_accepted_by', 'qing_accepted_on',
}


class RepairOrder(models.Model):
    _name = 'repair.order'
    _inherit = ['repair.order', 'portal.mixin']

    def _compute_access_url(self):
        super()._compute_access_url()
        for repair in self:
            repair.access_url = '/my/repairs/%s' % repair.id

    def _get_report_base_filename(self):
        self.ensure_one()
        return self.name

    def _qing_price_snapshot(self):
        self.ensure_one()
        return {
            'mode': self.qing_pricing_mode, 'currency': self.qing_currency_id.id,
            'partner': self.partner_id.id, 'company': self.company_id.id,
            'warranty': self.under_warranty, 'lines': self._qing_customer_lines(),
            'untaxed': self.qing_amount_untaxed, 'tax': self.qing_amount_tax, 'total': self.qing_amount_total,
        }

    def _qing_price_hash(self):
        snapshot = self.with_context(lang='en_US')._qing_price_snapshot()
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def _qing_check_price_confirmation(self):
        super()._qing_check_price_confirmation()
        if self.filtered('qing_price_signature'):
            raise UserError(self.env._('The customer has signed this price. An administrator must reopen price confirmation before changing it.'))

    @api.depends('qing_price_signature', 'qing_signed_price_hash', 'qing_amount_total', 'qing_amount_tax', 'qing_amount_untaxed', 'partner_id')
    def _compute_qing_price_confirmed(self):
        for repair in self:
            repair.qing_price_confirmed = bool(repair.qing_price_signature and consteq(repair.qing_signed_price_hash or '', repair._qing_price_hash()))

    @api.depends('qing_price_signature')
    def _compute_qing_can_change_mode(self):
        super()._compute_qing_can_change_mode()
        for repair in self.filtered('qing_price_signature'):
            repair.qing_can_change_mode = False

    def _qing_sign(self, kind, name, signature, price_hash):
        """Called after portal token or internal repair write-access validation."""
        self.ensure_one()
        self._qing_lock()
        if not self.env.su:
            raise AccessError(self.env._('Customer signatures must be submitted through the repair portal.'))
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValidationError(self.env._('Please provide the customer name.'))
        if not isinstance(signature, str) or len(signature) > 1400000:
            raise ValidationError(self.env._('Invalid signature image.'))
        try:
            raw = base64.b64decode(signature, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG', 'JPEG') or image.width * image.height > 4000000:
                    raise ValueError('Invalid image')
                image.verify()
        except (ValueError, OSError, Image.DecompressionBombError):
            raise ValidationError(self.env._('Invalid signature image.')) from None
        if not isinstance(price_hash, str) or not consteq(price_hash, self._qing_price_hash()):
            raise UserError(self.env._('The repair price changed. Refresh this page and review the new price before signing.'))
        if kind == 'price':
            if self.qing_pricing_mode != 'direct' or not self.qing_price_signature_enabled or self.state not in ('draft', 'confirmed'):
                raise UserError(self.env._('This repair does not require a direct price signature.'))
            if self.qing_price_signature or not self._qing_direct_lines():
                raise UserError(self.env._('The price is already signed or has no charges.'))
            vals = {
                'qing_price_signature': signature, 'qing_price_signed_by': name.strip(),
                'qing_price_signed_on': fields.Datetime.now(), 'qing_signed_price_hash': price_hash,
            }
            body = self.env._('Repair price confirmed by %s.', name.strip())
        elif kind == 'acceptance':
            if not self.qing_acceptance_enabled or self.state != 'done' or self.qing_acceptance_signature:
                raise UserError(self.env._('Only completed repairs awaiting acceptance can be signed.'))
            vals = {
                'qing_acceptance_signature': signature, 'qing_accepted_by': name.strip(),
                'qing_accepted_on': fields.Datetime.now(),
            }
            body = self.env._('Completed repair accepted by %s.', name.strip())
        else:
            raise ValidationError(self.env._('Invalid signature type.'))
        self.write(vals)
        pdf, filetype = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'qing_repair_portal.action_report_repair', res_ids=self.ids,
        )
        if filetype != 'pdf' or not pdf.startswith(b'%PDF'):
            raise UserError(self.env._('The signed repair PDF could not be generated. Please try again.'))
        evidence = json.dumps({
            'kind': kind, 'signed_by': name.strip(), 'price_hash': price_hash,
            'signed_on': str(fields.Datetime.now()), 'price': self._qing_price_snapshot(),
            'submitted_by': self.env.user.id,
        }, sort_keys=True, ensure_ascii=True).encode()
        self.message_post(
            body=body, attachments=[
                ('%s-%s.pdf' % (self.name.replace('/', '-'), kind), pdf),
                ('%s-signature.png' % kind, raw), ('%s-price-snapshot.json' % kind, evidence),
            ],
            author_id=self.partner_id.id, message_type='comment', subtype_xmlid='mail.mt_note',
        )

    qing_acceptance_enabled = fields.Boolean(string='Acceptance Signature Enabled', default=lambda self: self.env.company.qing_repair_acceptance_enabled, copy=False)
    qing_acceptance_signature = fields.Image(string='Acceptance Signature', attachment=True, copy=False, max_width=1024, max_height=512)
    qing_accepted_by = fields.Char(string='Accepted By', copy=False)
    qing_accepted_on = fields.Datetime(string='Accepted On', copy=False)
    qing_price_confirmed = fields.Boolean(string='Price Confirmed', compute='_compute_qing_price_confirmed')
    qing_price_signature = fields.Image(string='Price Signature', attachment=True, copy=False, max_width=1024, max_height=512)
    qing_price_signature_enabled = fields.Boolean(string='Price Signature Enabled', default=lambda self: self.env.company.qing_repair_price_signature_enabled, copy=False)
    qing_price_signed_by = fields.Char(string='Price Signed By', copy=False)
    qing_price_signed_on = fields.Datetime(string='Price Signed On', copy=False)
    qing_signed_price_hash = fields.Char(copy=False)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env['res.company'].browse(values.get('company_id') or self.env.company.id)
        for field, setting in (
            ('qing_price_signature_enabled', 'qing_repair_price_signature_enabled'),
            ('qing_acceptance_enabled', 'qing_repair_acceptance_enabled'),
        ):
            if field in fields_list and 'default_' + field not in self.env.context:
                values[field] = company[setting]
        return values

    @api.onchange('company_id')
    def _onchange_qing_signature_settings(self):
        if not self._origin.id:
            self.qing_price_signature_enabled = self.company_id.qing_repair_price_signature_enabled
            self.qing_acceptance_enabled = self.company_id.qing_repair_acceptance_enabled

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su and any(vals.get(field) for field in SIGNATURE_FIELDS):
                raise AccessError(self.env._('Customer signatures must be submitted through the repair portal.'))
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if not self.env.su and not self.env.user.has_group('base.group_system'):
                for field, default in (
                    ('qing_price_signature_enabled', company.qing_repair_price_signature_enabled),
                    ('qing_acceptance_enabled', company.qing_repair_acceptance_enabled),
                ):
                    if vals.get(field, default) != default:
                        raise AccessError(self.env._('Only administrators can change repair signature requirements.'))
            vals.setdefault('qing_price_signature_enabled', company.qing_repair_price_signature_enabled)
            vals.setdefault('qing_acceptance_enabled', company.qing_repair_acceptance_enabled)
        return super().create(vals_list)

    def copy_data(self, default=None):
        values = super().copy_data(default=default)
        for vals in values:
            for field in ('qing_price_signature_enabled', 'qing_acceptance_enabled'):
                if not default or field not in default:
                    vals.pop(field, None)
        return values

    def write(self, vals):
        if SIGNATURE_FIELDS & vals.keys() and not self.env.su:
            raise AccessError(self.env._('Customer signatures must be submitted through the repair portal.'))
        settings = {'qing_price_signature_enabled', 'qing_acceptance_enabled'}
        if settings & vals.keys():
            if not self.env.su and not self.env.user.has_group('base.group_system'):
                raise AccessError(self.env._('Only administrators can change repair signature requirements.'))
            self._qing_check_mode_change()
        return super().write(vals)

    def action_validate(self):
        for repair in self:
            if repair.qing_pricing_mode == 'direct' and repair.qing_price_signature_enabled and not repair.qing_price_confirmed:
                raise UserError(self.env._('The customer must confirm the repair price before repair confirmation.'))
        return super().action_validate()

    def action_repair_done(self):
        for repair in self.filtered(lambda record: record.qing_pricing_mode == 'direct' and record.qing_price_signature_enabled):
            repair._qing_lock()
            if repair.state != 'confirmed' or not repair.qing_price_confirmed:
                raise UserError(self.env._('The customer must confirm the repair price before repair confirmation.'))
        for repair in self.filtered('qing_price_signature'):
            repair._qing_lock()
            if any(float_compare(move.quantity, move.product_uom_qty, precision_digits=6) for move in repair.move_ids.filtered(lambda record: record.repair_line_type == 'add')):
                raise UserError(self.env._('Consumed parts differ from the signed price. Reopen price confirmation before completing the repair.'))
        return super().action_repair_done()

    def action_qing_share(self):
        self.ensure_one()
        self.check_access('read')
        return self.with_context(active_model=self._name, active_id=self.id).action_share()

    def action_qing_preview(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_url', 'url': self.get_portal_url(), 'target': 'new'}

    def action_qing_download_pdf(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_url', 'url': self.get_portal_url(report_type='pdf', download=True), 'target': 'download'}

    def _qing_signature_action(self, kind):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(self.env._('Only repair users can collect customer signatures.'))
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Customer Signature'),
            'res_model': 'qing.repair.signature', 'view_mode': 'form', 'target': 'new',
            'context': {
                'default_repair_id': self.id, 'default_kind': kind,
                'default_signed_by': self.partner_id.name,
                'default_price_hash': self._qing_price_hash(),
            },
        }

    def action_qing_sign_price(self):
        return self._qing_signature_action('price')

    def action_qing_sign_acceptance(self):
        return self._qing_signature_action('acceptance')

    def action_qing_reopen_price(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can reopen repair price confirmation.'))
        self._qing_lock()
        if self.qing_pricing_mode != 'direct' or self.state not in ('draft', 'confirmed') or self.invoice_ids:
            raise UserError(self.env._('Only uninvoiced, unfinished direct repairs can reopen price confirmation.'))
        self.message_post(body=self.env._('Price confirmation reopened. The previous signature remains in the repair history.'), subtype_xmlid='mail.mt_note')
        self.sudo().write({
            'qing_price_signature': False, 'qing_price_signed_by': False,
            'qing_price_signed_on': False, 'qing_signed_price_hash': False,
        })
