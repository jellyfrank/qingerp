from odoo import api, fields, models, _
from odoo.exceptions import AccessDenied
import logging

_logger = logging.getLogger(__name__)

SYNC_POLICES = [
    ('email', 'Email'),
    ('phone', 'Phone'),
    ('email_or_phone', 'Email Or Phone')
]


class wework_connect_wizard(models.TransientModel):

    _name = "wework.connect.wizard"
    _description = "Connect Wizard"

    policy = fields.Selection(SYNC_POLICES, string="Connect Police")

    def button_confirm(self):
        """Connect Employee and User."""
        users = self.env['res.users'].sudo().search(
            [('employee_id', '=', False)])
        if not users:
            raise AccessDenied(_("No users need to connect."))

        emp_obj = self.env['hr.employee']

        success_user_ids = []
        for user in users:
            email = user.partner_id.email
            phones = []
            if user.partner_id.mobile:
                phones.append(user.partner_id.mobile)
            if user.partner_id.phone:
                phones.append(user.partner_id.phone)
            # mobile, phone = user.partner_id.mobile, user.partner_id.phone
            if self.policy == "email":
                domain = [('work_email', '=', email)]
            elif self.policy == 'phone':
                if not phones:
                    continue
                domain = ['|', ('mobile_phone', 'in', phones),
                          ('work_phone', 'in', phones)]
            else:
                if not phones:
                    domain = [('work_email', '=', email)]
                else:
                    domain = ['|', ('work_email', '=', email), '|', ('mobile_phone', 'in', phones),
                              ('work_phone', 'in', phones)]

            employee = emp_obj.search(domain, limit=1)
            if not employee:
                _logger.warning(
                    _(f"[Wework]User:{user.name} could not connect cause no employee matched."))
            else:
                employee.user_id = user.id
                success_user_ids.append(user.id)
                _logger.info(
                    _(f"[Wework]User:{user.name} connected with empolyee:{employee.id}"))
        return self.env['window.pops'].create({
            "name": "Connect Success",
            "text": f"total:{len(users)} users, success:{len(success_user_ids)}."
        }).get_action()
