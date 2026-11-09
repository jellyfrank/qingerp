from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.binary import BinaryBytes


class StatementSnapshot(models.Model):
    _name = 'qing.statement.snapshot'
    _description = '客商对账快照'
    _order = 'id desc'

    @api.model
    def _from_wizard(self, wizard):
        wizard.ensure_one()
        data = wizard._statement_data()
        captured = fields.Datetime.now()
        return super().create({
            'name': '对账快照 %s — %s' % (wizard.date_from, wizard.date_to),
            'company_id': wizard.company_id.id, 'captured_at': captured,
            'captured_by': self.env.user.id, 'captured_by_name': self.env.user.display_name,
            'date_from': wizard.date_from, 'date_to': wizard.date_to,
            'account_type': dict(wizard._fields['account_type'].selection)[wizard.account_type],
            'partner_scope': '、'.join(wizard.partner_ids.mapped('display_name')) or '全部往来单位',
            'result': wizard._statement_html(data, frozen=True),
            'result_file': BinaryBytes(wizard._statement_csv(data)),
        })

    account_type = fields.Char(string='往来类型', readonly=True, required=True)
    captured_at = fields.Datetime(string='保存时间', readonly=True, required=True)
    captured_by = fields.Many2one('res.users', string='保存人', readonly=True, ondelete='restrict', required=True)
    captured_by_name = fields.Char(string='保存人名称（当时）', readonly=True, required=True)
    company_id = fields.Many2one('res.company', string='公司', readonly=True, required=True, index=True, ondelete='restrict')
    date_from = fields.Date(string='开始日期', readonly=True, required=True)
    date_to = fields.Date(string='截止日期', readonly=True, required=True)
    name = fields.Char(string='名称', readonly=True, required=True)
    partner_scope = fields.Text(string='往来范围（当时）', readonly=True, required=True)
    result = fields.Html(string='对账与账龄快照', readonly=True)
    result_file = fields.Binary(string='下载快照明细 CSV', readonly=True, attachment=False)
    result_filename = fields.Char(default='partner-statement-snapshot.csv', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(self.env._('请从客商对账向导保存快照。'))

    def write(self, vals):
        raise AccessError(self.env._('对账快照保存后不可修改，请重新生成并另存快照。'))

    def unlink(self):
        raise AccessError(self.env._('对账快照不可删除。'))

    def copy(self, default=None):
        raise AccessError(self.env._('对账快照不可复制，请从向导重新生成。'))
