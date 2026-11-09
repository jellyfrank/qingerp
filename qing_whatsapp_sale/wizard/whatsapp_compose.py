import json
import hashlib
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import format_amount


class WhatsappCompose(models.TransientModel):
    _inherit = 'qing.whatsapp.compose'

    def _check_sale_recipient(self):
        self.ensure_one()
        order = self.sale_order_id
        number = order._qing_check_send()
        self.account_id._assert_operator()
        if (self.account_id.company_id != order.company_id or self.partner_id != order.partner_id
                or self.env['qing.whatsapp.conversation']._normalize_number(self.number) != number):
            raise ValidationError(_('The quotation must be sent to its customer through an account of the same company.'))
        if self.conversation_id and (self.conversation_id.number != number or
                self.conversation_id.account_id != self.account_id):
            raise ValidationError(_('The selected conversation does not match the quotation recipient.'))

    def _sale_fingerprint(self):
        order = self.sale_order_id
        values = [order.write_date, order.partner_id.write_date, order.amount_total,
                  [(line.id, line.write_date) for line in order.order_line.sorted('id')]]
        return hashlib.sha256(json.dumps(values, default=str).encode()).hexdigest()

    def _prepare_sale_preview(self):
        self._check_sale_recipient()
        order = self.sale_order_id
        link = order.get_base_url() + order.get_portal_url()
        pdf, _type = self.env['ir.actions.report']._render_qweb_pdf('sale.action_report_saleorder', res_ids=order.ids)
        attachment = self.env['ir.attachment'].create({'name': '%s.pdf' % order.name,
            'raw': pdf, 'mimetype': 'application/pdf', 'res_model': self._name, 'res_id': self.id})
        self.sudo().write({'attachment_id': attachment.id, 'sale_snapshot_at': order.write_date, 'sale_snapshot_hash': self._sale_fingerprint(),
            'body': _('%(customer)s, quotation %(order)s: %(total)s. View your quotation: %(url)s',
                customer=order.partner_id.name, order=order.name,
                total=format_amount(self.env, order.amount_total, order.currency_id), url=link)})
        self._fill_sale_variables()

    def _fill_sale_variables(self):
        if not self.sale_order_id or not self.template_id:
            return
        self._check_sale_recipient()
        if self.template_id.account_id != self.account_id:
            raise ValidationError(_('Select a template for this account.'))
        order = self.sale_order_id
        sources = {'customer': order.partner_id.name, 'order': order.name,
            'total': format_amount(self.env, order.amount_total, order.currency_id),
            'currency': order.currency_id.name, 'company': order.company_id.name,
            'portal_url': order.get_base_url() + order.get_portal_url()}
        mapped = {(v.component, v.position): sources[v.source] for v in self.template_id.sale_variable_ids}
        values = {}
        for definition in json.loads(self.template_id.components_json or '[]'):
            section = definition.get('type', '').lower()
            if section not in ('body', 'header'):
                continue
            slots = sorted(set(map(int, re.findall(r'\{\{(\d+)\}\}', definition.get('text', '')))))
            if any((section, slot) not in mapped for slot in slots):
                raise ValidationError(_('Configure a sales field mapping for every template placeholder.'))
            values['parameter_values' if section == 'body' else 'header_value'] = '\n'.join(mapped[section, slot] for slot in slots)
        self.update(values)

    def _after_enqueue(self, message):
        super()._after_enqueue(message)
        if not self.sale_order_id:
            return
        self._check_sale_recipient()
        message.sudo().sale_order_id = self.sale_order_id
        message._preflight()
        self.sale_order_id.qing_conversation_id = message.conversation_id
        self.sale_order_id.message_post(body=_('WhatsApp quotation queued for %s.', self.number),
                                        subtype_xmlid='mail.mt_note')

    @api.onchange('template_id')
    def _onchange_sale_template(self):
        self._fill_sale_variables()

    sale_order_id = fields.Many2one('sale.order', readonly=True)
    sale_snapshot_at = fields.Datetime(readonly=True)
    sale_snapshot_hash = fields.Char(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get('sale_order_id') and
                any(v.get(k) for k in ('attachment_id', 'sale_snapshot_at', 'sale_snapshot_hash', 'upload_data')) for v in vals_list):
            raise ValidationError(_('Generate the quotation preview before sending.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and (self.sale_order_id or vals.get('sale_order_id')) and set(vals) & {
                'sale_order_id', 'sale_snapshot_at', 'sale_snapshot_hash', 'attachment_id', 'upload_data'}:
            raise ValidationError(_('Use Refresh quotation to replace the generated PDF.'))
        return super().write(vals)

    def action_refresh_sale_preview(self):
        self._prepare_sale_preview()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}

    def action_send(self):
        if self.sale_order_id:
            self._check_sale_recipient()
            if self.sale_snapshot_hash != self._sale_fingerprint():
                raise ValidationError(_('The quotation changed. Refresh the PDF and review it before sending.'))
            if self.kind not in ('document', 'template'):
                raise ValidationError(_('Send the quotation as a PDF or an approved document template.'))
            if self.kind == 'template':
                headers = [c for c in json.loads(self.template_id.components_json or '[]') if c.get('type') == 'HEADER']
                if not headers or headers[0].get('format') != 'DOCUMENT':
                    raise ValidationError(_('Choose an approved template with a document header for this quotation.'))
        return super().action_send()
