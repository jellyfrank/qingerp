from odoo import api, fields, models


PHOTO_KINDS = [('before', 'Before Repair'), ('after', 'After Repair')]


class RepairPhoto(models.Model):
    _name = 'qing.repair.photo'
    _description = 'Repair Photo'
    _inherit = 'image.mixin'
    _order = 'sequence, id'
    _check_company_auto = True

    company_id = fields.Many2one(related='repair_id.company_id', store=True, index=True)
    image_1920 = fields.Image(string='Photo', required=True, max_width=1920, max_height=1920)
    kind = fields.Selection(PHOTO_KINDS, required=True, index=True)
    legacy_source = fields.Selection(PHOTO_KINDS, readonly=True, copy=False)
    name = fields.Char(string='Caption', default=lambda self: self.env._('Repair Photo'), required=True)
    repair_id = fields.Many2one('repair.order', required=True, ondelete='cascade', index=True, check_company=True)
    sequence = fields.Integer(default=10)

    @api.model_create_multi
    def create(self, vals_list):
        Repair = self.env['repair.order']
        repairs = Repair.browse({vals.get('repair_id') or self.env.context.get('default_repair_id') for vals in vals_list} - {None, False})
        repairs.check_access('write')
        return super().create(vals_list)

    def write(self, vals):
        repairs = self.repair_id
        if vals.get('repair_id'):
            repairs |= self.env['repair.order'].browse(vals['repair_id'])
        repairs.check_access('write')
        return super().write(vals)

    def unlink(self):
        self.repair_id.check_access('write')
        return super().unlink()
