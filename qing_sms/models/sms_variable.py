from odoo import fields, models

SOURCES = [('input', 'Call Parameter'), ('field', 'Business Field'), ('constant', 'Fixed Value')]


class SmsVariable(models.Model):
    _name = 'qing.sms.variable'
    _description = '短信变量映射'
    _order = 'sequence, id'

    binding_id = fields.Many2one('qing.sms.binding', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='binding_id.company_id', store=True, index=True)
    constant = fields.Char(string='固定值')
    field_path = fields.Char(string='字段路径', help='如 partner_id.name，只允许读取业务字段，不执行代码。')
    key = fields.Char(string='业务变量', required=True)
    max_length = fields.Integer(string='最大长度', default=30)
    remote_name = fields.Char(string='云端变量', required=True)
    sequence = fields.Integer(default=10)
    source = fields.Selection(SOURCES, default='input', required=True, string='Value Source')

    _remote_unique = models.Constraint('unique(binding_id, remote_name)', '每个云端变量只能映射一次。')
