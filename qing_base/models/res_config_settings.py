from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..tools import mommy_base_loaded

DEFAULT_WEB_TITLE = 'QingERP 20.0'
REPORT_PRINT_MODES = [
    ('preview', 'Online Preview'),
    ('download', 'Download'),
]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    @api.constrains('qing_audit_retention_days')
    def _check_qing_audit_retention_days(self):
        for settings in self:
            if not 1 <= settings.qing_audit_retention_days <= 365:
                raise ValidationError(self.env._('Audit retention must be between 1 and 365 days.'))

    @api.constrains('qing_login_log_retention_days')
    def _check_qing_login_log_retention_days(self):
        for settings in self:
            if not 1 <= settings.qing_login_log_retention_days <= 3650:
                raise ValidationError(self.env._('Sign-in log retention must be between 1 and 3650 days.'))

    def _compute_qing_legacy_mommy_base(self):
        legacy = mommy_base_loaded(self.env)
        for settings in self:
            settings.qing_legacy_mommy_base = legacy

    @api.depends('qing_report_print_preview')
    def _compute_qing_report_print_mode(self):
        for settings in self:
            settings.qing_report_print_mode = (
                'preview' if settings.qing_report_print_preview else 'download'
            )

    def _inverse_qing_report_print_mode(self):
        for settings in self:
            settings.qing_report_print_preview = settings.qing_report_print_mode == 'preview'

    module_qing_account_fapiao = fields.Boolean(string='Tax Invoice Ledger')
    module_qing_account_fapiao_ocr = fields.Boolean(string='Local Tax Invoice OCR')
    module_qing_sms_aliyun = fields.Boolean(string='Aliyun SMS')
    module_qing_wework = fields.Boolean(string='WeCom')
    qing_audit_enabled = fields.Boolean(
        string='Record ORM Changes',
        config_parameter='qing_base.audit_enabled',
        default=True,
    )
    qing_audit_retention_days = fields.Integer(
        string='Audit Retention (Days)',
        config_parameter='qing_base.audit_retention_days',
        default=7,
    )
    qing_legacy_mommy_base = fields.Boolean(compute='_compute_qing_legacy_mommy_base')
    qing_login_ip_whitelist = fields.Text(
        related='company_id.qing_login_ip_whitelist', readonly=False,
    )
    qing_login_ip_whitelist_enabled = fields.Boolean(
        related='company_id.qing_login_ip_whitelist_enabled', readonly=False,
    )
    qing_login_ip_whitelist_exclude_portal = fields.Boolean(
        related='company_id.qing_login_ip_whitelist_exclude_portal', readonly=False,
    )
    qing_login_ip_whitelist_group_ids = fields.Many2many(
        related='company_id.qing_login_ip_whitelist_group_ids', readonly=False,
    )
    qing_login_log_retention_days = fields.Integer(
        string='Sign-in Log Retention (Days)',
        config_parameter='qing_base.login_log_retention_days',
        default=90,
    )
    qing_pdf_password_edit = fields.Char(
        string='Default PDF Edit Password',
        config_parameter='qing_base.pdf_password_edit',
    )
    qing_pdf_password_read = fields.Char(
        string='Default PDF Read Password',
        config_parameter='qing_base.pdf_password_read',
    )
    qing_report_print_mode = fields.Selection(
        REPORT_PRINT_MODES,
        string='Report Printing',
        compute='_compute_qing_report_print_mode',
        inverse='_inverse_qing_report_print_mode',
        readonly=False,
    )
    qing_report_print_preview = fields.Boolean(
        string='Preview PDF Reports Online',
        config_parameter='qing_base.report.print_preview',
        default=True,
    )
    qing_web_title = fields.Char(
        string='System Title',
        config_parameter='qing_base.title',
        default=DEFAULT_WEB_TITLE,
    )
