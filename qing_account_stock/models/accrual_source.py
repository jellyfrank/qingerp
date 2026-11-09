from odoo import api, fields, models
from odoo.exceptions import UserError


class AccrualSource(models.Model):
    _name = 'qing.accrual.source'
    _description = '暂估批次来源占用'

    _purchase_line_unique = models.Constraint('UNIQUE(purchase_line_id)', '该采购明细已被另一批次锁定，请先取消原批次。')

    @api.model
    def _acquire(self, batch):
        batch._check_manager()
        domain = [('purchase_line_id', 'in', batch.source_line_ids.ids)]
        if self.search_count(domain):
            raise UserError(self.env._('部分采购明细已被其他批次锁定，请先取消原批次。'))
        # The database uniqueness constraint also arbitrates concurrent locks.
        return super().create([{'batch_id': batch.id, 'purchase_line_id': line.id}
                               for line in batch.source_line_ids])

    @api.model
    def _release(self, batch):
        batch._check_manager()
        domain = [('batch_id', '=', batch.id)]
        claims = self.search(domain)
        return super(AccrualSource, claims).unlink()

    batch_id = fields.Many2one('qing.accrual.batch', required=True, ondelete='cascade', readonly=True)
    company_id = fields.Many2one(related='batch_id.company_id', store=True, index=True)
    purchase_line_id = fields.Many2one('purchase.order.line', required=True, ondelete='restrict', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(self.env._('来源占用只能通过批次锁定生成。'))

    def write(self, values):
        raise UserError(self.env._('来源占用不可直接修改。'))

    def unlink(self):
        raise UserError(self.env._('请取消批次以释放来源占用。'))
