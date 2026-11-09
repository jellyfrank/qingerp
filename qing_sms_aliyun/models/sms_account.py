from odoo import fields, models
from ..services.aliyun_adapter import AliyunAdapter


class SmsAccount(models.Model):
    _inherit = 'qing.sms.account'

    def _get_adapter(self):
        self.ensure_one()
        if self.provider == 'aliyun':
            return AliyunAdapter(self)
        return super()._get_adapter()

    def _create_template_application(self, application):
        self.ensure_one()
        if self.provider == 'aliyun':
            return self._get_adapter().create_template(application)
        return super()._create_template_application(application)

    def _validate_template_application(self, application):
        self.ensure_one()
        if self.provider == 'aliyun':
            return self._get_adapter().validate_template_application(application)
        return super()._validate_template_application(application)

    def _query_template_application(self, application):
        self.ensure_one()
        if self.provider == 'aliyun':
            return self._get_adapter().query_template(application)
        return super()._query_template_application(application)

    legacy_config_id = fields.Many2one('qing.sms.aliyun.config', ondelete='restrict', copy=False,
                                       groups='base.group_system')
    provider = fields.Selection(selection_add=[('aliyun', 'Aliyun SMS')], ondelete={'aliyun': 'set default'})

    _legacy_company_unique = models.Constraint('unique(legacy_config_id, company_id)', '旧账户在同一公司只能迁移一次。')
