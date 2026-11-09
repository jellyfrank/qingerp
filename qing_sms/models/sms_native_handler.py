from odoo import models, _
from odoo.exceptions import UserError


class SmsScenarioHandler(models.AbstractModel):
    _inherit = 'qing.sms.scenario.handler'

    def _get_handlers(self):
        return {**super()._get_handlers(), 'native.template': 'qing.sms.handler.native.template'}


class SmsNativeTemplateHandler(models.AbstractModel):
    _name = 'qing.sms.handler.native.template'
    _description = 'Native SMS template preflight'

    def _check_before_send(self, record, event):
        if (event.event_code != 'native.template' or not event.mail_message_id or
                event.mail_message_id.model != record._name or event.mail_message_id.res_id != record.id or
                not event.native_template_id or event.binding_id.template_id != event.native_template_id):
            raise UserError(_('The native SMS event has an invalid source or template.'))
