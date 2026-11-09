import csv
import hashlib
import io
import json

from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes


MAX_PURCHASE_LINES = 1000


class UnbilledPurchase(models.TransientModel):
    _name = 'qing.unbilled.purchase'
    _description = '到货未到票核对'

    def _source_lines(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以核对到货未到票。'))
        self.check_access('write')
        if self.company_id != self.env.company:
            raise UserError(self.env._('请切换回向导所属公司后重新生成。'))
        self.partner_ids.check_access('read')
        self.order_ids.check_access('read')
        if any(p.company_id and p.company_id != self.company_id for p in self.partner_ids):
            raise UserError(self.env._('往来单位不能属于其他公司。'))
        if any(o.company_id != self.company_id for o in self.order_ids):
            raise UserError(self.env._('采购单不能属于其他公司。'))
        domain = [('company_id', '=', self.company_id.id), ('state', 'in', ['purchase', 'done']),
                  ('display_type', '=', False), ('product_id.is_storable', '=', True)]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.order_ids:
            domain.append(('order_id', 'in', self.order_ids.ids))
        lines = self.env['purchase.order.line'].search(domain, order='order_id, id', limit=MAX_PURCHASE_LINES + 1)
        if len(lines) > MAX_PURCHASE_LINES:
            raise UserError(self.env._('范围超过1000条采购明细，请按供应商或采购单缩小范围。'))
        # Related sources must all be readable; never silently undercount bills.
        lines.move_ids.check_access('read')
        lines.invoice_lines.check_access('read')
        lines.invoice_lines.move_id.check_access('read')
        return lines

    def _rows(self):
        rows = []
        for line in self._source_lines():
            bills = line.invoice_lines.filtered(lambda l: l.move_id.state == 'posted'
                and l.move_type in ('in_invoice', 'in_refund'))
            if any(l.company_id != self.company_id or l.product_id != line.product_id for l in bills):
                raise UserError(self.env._('采购关联账单的公司或商品不一致，请先核实来源。'))
            invoiced = sum(l.product_uom_id._compute_quantity(l.quantity, line.uom_id, round=False)
                * (-1 if l.move_type == 'in_refund' else 1) for l in bills)
            received = line.qty_received
            gap = received - invoiced
            if line.uom_id.is_zero(gap) and not self.include_balanced:
                continue
            unit_price = line.price_subtotal / line.product_qty if line.product_qty else None
            reference = line.currency_id.round(max(gap, 0) * unit_price) if unit_price is not None else None
            status = '到货未到票' if line.uom_id.compare(gap, 0) > 0 else (
                '到票超过净收货（含待贷项）' if line.uom_id.compare(gap, 0) < 0 else '数量一致')
            rows.append({'line': line, 'received': received, 'invoiced': invoiced, 'gap': gap,
                'reference': reference, 'status': status, 'bills': bills.move_id,
                'pickings': line.move_ids.filtered(lambda m: m.state == 'done').picking_id})
        return rows

    def _fingerprint_rows(self, rows):
        data = []
        for row in sorted(rows, key=lambda item: item['line'].id):
            line = row['line']
            data.append([line.id, str(line.write_date), row['received'], row['invoiced'],
                row['gap'], row['reference'], line.currency_id.id,
                [(m.id, str(m.write_date), m.state) for m in line.move_ids.sorted('id')],
                [(l.id, str(l.write_date), l.move_id.state, str(l.move_id.write_date))
                 for l in line.invoice_lines.sorted('id')]])
        return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode()).hexdigest()

    def _render(self, rows):
        headers = ['采购单 / 明细', '供应商', '商品', '单位', '净收货', '已过账净到票',
                   '数量差额', '币种', '未到票参考额', '状态', '收退货单', '业务账单 / 贷项']

        def links(records, model):
            return Markup('、').join(Markup('<a href="/odoo/%s/%s">%s</a>')
                % (model, r.id, r.display_name) for r in records)

        body = []
        for row in rows:
            line = row['line']
            values = [links(line.order_id, 'purchase.order') + Markup(' / %s') % line.id,
                line.partner_id.display_name, line.product_id.display_name, line.uom_id.display_name,
                row['received'], row['invoiced'], row['gap'], line.currency_id.name,
                row['reference'] if row['reference'] is not None else '无法计算', row['status'],
                links(row['pickings'], 'stock.picking'), links(row['bills'], 'account.move')]
            body.append(Markup('<tr>%s</tr>') % Markup('').join(Markup('<td>%s</td>') % v for v in values))
        return (Markup('<p>生成时间（UTC）：%s；共%s条。参考额按采购单折扣后未税单价计算，未换算本位币，不是暂估凭证或税务发票余额。</p>')
            % (fields.Datetime.now(), len(rows)) + Markup('<div class="table-responsive"><table class="table table-bordered" style="min-width: 1500px"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>')
            % (Markup('').join(Markup('<th>%s</th>') % h for h in headers), Markup('').join(body)))

    def _csv(self, rows):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['公司', '采购单', '采购明细ID', '供应商', '商品', '单位', '净收货', '已过账净到票',
                         '数量差额', '币种', '未到票参考额', '状态', '收退货单', '业务账单/贷项'])
        for row in rows:
            line = row['line']
            values = [self.company_id.display_name, line.order_id.name, line.id, line.partner_id.display_name,
                line.product_id.display_name, line.uom_id.display_name, row['received'], row['invoiced'],
                row['gap'], line.currency_id.name, row['reference'], row['status'],
                ', '.join(row['pickings'].mapped('name')), ', '.join(row['bills'].mapped('name'))]
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                             else value for value in values])
        return ('\ufeff' + stream.getvalue()).encode('utf-8')

    company_id = fields.Many2one('res.company', string='公司', default=lambda self: self.env.company, required=True, readonly=True)
    include_balanced = fields.Boolean(string='包含数量一致的明细')
    order_ids = fields.Many2many('purchase.order', string='采购单')
    partner_ids = fields.Many2many('res.partner', string='供应商')
    result = fields.Html(string='核对结果', readonly=True)
    result_file = fields.Binary(string='下载核对 CSV', readonly=True, attachment=False)
    result_filename = fields.Char(default='unbilled-purchases.csv', readonly=True)

    def action_generate(self):
        rows = self._rows()
        self.write({'result': self._render(rows), 'result_file': BinaryBytes(self._csv(rows))})
        return {'type': 'ir.actions.act_window', 'name': self.env._('到货未到票核对'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def write(self, values):
        if {'company_id', 'partner_ids', 'order_ids', 'include_balanced'} & values.keys():
            values = dict(values, result=False, result_file=False)
        return super().write(values)

    def action_create_batch(self):
        self.ensure_one()
        batch = self.env['qing.accrual.batch']._from_wizard(self)
        return {'type': 'ir.actions.act_window', 'name': self.env._('暂估准备批次'),
                'res_model': batch._name, 'res_id': batch.id, 'view_mode': 'form'}
