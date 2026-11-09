from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SmsAccount(models.Model):
    _name = 'qing.sms.account'
    _description = '短信账户'
    _check_company_auto = True

    def _get_adapter(self):
        raise UserError(_('请安装并选择短信服务商连接器。'))

    def _check_manager(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.su and not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise UserError(_('只有短信管理员可以同步账户。'))

    def _apply_snapshot(self, signs, templates):
        Sign = self.env['qing.sms.sign'].with_context(active_test=False)
        Template = self.env['qing.sms.remote.template'].with_context(active_test=False)
        for Model, rows in ((Sign, signs), (Template, templates)):
            seen = set()
            for row in rows:
                code = row['remote_id']
                if not code or code in seen:
                    raise UserError(_('云端列表含空编号或重复编号，本次同步已回滚。'))
                seen.add(code)
                domain = [('account_id', '=', self.id), ('remote_id', '=', code)]
                record = Model.search(domain, limit=1)
                values = dict(row, account_id=self.id, last_sync_at=fields.Datetime.now())
                if record:
                    record._write_synced(values)
                else:
                    Model._create_synced(values)
            domain = [('account_id', '=', self.id), ('remote_id', 'not in', list(seen))]
            # Missing is deliberately not deletion: keep bindings and history for review.
            Model.search(domain)._write_synced({'audit_state': 'missing'})
        self.write({'last_sync_at': fields.Datetime.now(), 'sync_error': False})

    @api.model
    def _cron_sync(self):
        domain = [('active', '=', True), ('auto_sync', '=', True)]
        for account in self.search(domain, limit=100):
            account.action_sync()

    active = fields.Boolean(string='启用', default=True)
    auto_sync = fields.Boolean(string='定时同步', default=False)
    company_id = fields.Many2one('res.company', string='公司', required=True, default=lambda self: self.env.company, index=True)
    enabled = fields.Boolean(string='允许发送', default=False)
    key = fields.Char(string='Access Key', groups='base.group_system', copy=False)
    last_sync_at = fields.Datetime(string='最近同步', readonly=True)
    name = fields.Char(string='名称', required=True)
    provider = fields.Selection([('abstract', 'Select a Provider Connector')], string='SMS Provider', required=True, default='abstract')
    secret = fields.Char(string='Access Secret', groups='base.group_system', copy=False)
    sign_ids = fields.One2many('qing.sms.sign', 'account_id', string='签名')
    sync_error = fields.Text(string='同步错误', readonly=True)
    template_ids = fields.One2many('qing.sms.remote.template', 'account_id', string='云端模板')
    template_apply_not_before = fields.Datetime(readonly=True, groups='base.group_system')

    def _create_template_application(self, application):
        raise UserError(_('This provider does not support template creation. Export the application materials instead.'))

    def _validate_template_application(self, application):
        adapter = self._get_adapter()
        if not hasattr(adapter, 'validate_template_application'):
            raise UserError(_('This provider does not support template creation. Export the application materials instead.'))
        return adapter.validate_template_application(application)

    def _query_template_application(self, application):
        raise UserError(_('This provider does not support template status queries. Synchronize templates instead.'))

    def action_sync(self):
        self._check_manager()
        try:
            with self.env.cr.savepoint():
                # Serialize per account; fetch every page before changing the local mirror.
                self.lock_for_update()
                adapter = self._get_adapter()
                signs, templates = adapter.snapshot()
                self._apply_snapshot(signs, templates)
        except Exception:
            self.write({'sync_error': _('同步失败，未覆盖本地模板。请检查连接、查询权限和厂商控制台。')})
        return self._qing_message_action(_('同步结果'), self.sync_error or _('签名与模板同步完成，请复核业务绑定。'))

    def action_test_configuration(self):
        self._check_manager()
        try:
            self._get_adapter().test_configuration()
        except Exception:
            raise UserError(_('查询失败，请检查密钥、网络和模板查询权限。')) from None
        return self._qing_message_action(_('配置检查'), _('查询成功；尚未验证发送权限、余额或送达能力，本次未发送短信。'))
