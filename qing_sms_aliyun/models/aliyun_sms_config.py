from ..services.aliyun_client import AliyunSmsClient
from odoo import fields, models, _
from odoo.exceptions import UserError


class AliyunSmsConfig(models.Model):
    _name = "qing.sms.aliyun.config"
    _description = "Aliyun Sms Configuration"

    key = fields.Char("Access Key", groups="base.group_system", copy=False)
    name = fields.Char("Name", required=True)
    secret = fields.Char("Access Secret", groups="base.group_system", copy=False)
    sign = fields.Char("Signature")

    def get_aliyun_sms_client(self):
        """Administrator API; sending code uses only the company's bound record."""
        self.ensure_one()
        self.check_access("read")
        if not self.key or not self.secret or not self.sign:
            raise UserError(_("Please configure the Aliyun access key, secret and signature."))
        return AliyunSmsClient(self.key, self.secret)

    def action_test_configuration(self):
        """Check this configuration using Aliyun's read-only signature API."""
        client = self.get_aliyun_sms_client()
        try:
            result = client.query_sign(self.sign)
        except Exception:
            # Signed URLs in transport exceptions contain credentials.
            raise UserError(_("连接阿里云失败，请检查网络、AccessKey 和 SDK 配置后重试。")) from None
        if not isinstance(result, dict) or not isinstance(result.get("Code"), str):
            raise UserError(_("阿里云返回了无效响应，请稍后重试。"))
        code = result["Code"]
        if code != "OK":
            hints = {
                "SignatureNotFound": "该账户下未找到此签名，请核对签名名称。",
                "InvalidAccessKeyId.NotFound": "AccessKey 不存在，请核对密钥。",
                "SignatureDoesNotMatch": "请求签名不匹配，请核对 Access Secret 和服务器时间。",
                "ForbiddenAction": "账户无权查询签名，请检查 RAM 的 dysms:GetSmsSign 权限。",
                "NoPermission": "请为此 RAM 用户授予 dysms:GetSmsSign 查询权限。",
            }
            hint = hints.get(code, "请检查密钥、RAM 的 dysms:GetSmsSign 权限和阿里云短信控制台。")
            # Do not echo arbitrary provider messages or signed URLs.
            safe_code = code if len(code) <= 100 and all(c.isalnum() or c in '._-' for c in code) else 'API_ERROR'
            raise UserError(_("配置检查未通过：%(code)s。%(hint)s", code=safe_code, hint=hint))
        status = result.get("SignStatus")
        if status not in (0, 1, 2, 10) or isinstance(status, bool):
            raise UserError(_("接口连接成功，但未获取到有效签名审核状态，请在阿里云控制台核查。"))
        status_text = {0: "审核中", 1: "审核通过", 2: "审核失败", 10: "取消审核"}[status]
        return self._qing_message_action(
            _("配置测试结果"),
            _("密钥认证与签名查询成功。签名状态：%(status)s。本次未发送短信；发送权限、模板、余额和实际送达仍需另行验证。", status=status_text),
        )

    def action_migrate_configuration(self):
        self.check_access('write')
        from ..hooks import migrate_legacy
        migrate_legacy(self.env)
        return self._qing_message_action(_('迁移完成'), _('已建立通用账户与待核验绑定；请在国内短信中同步、配置变量并启用。'))
