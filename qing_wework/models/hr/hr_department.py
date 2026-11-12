#!/usr/bin/python3
# @Time    : 2021-06-17
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ...tools.actions import confirm_action, notification_action
from wechatpy.exceptions import WeChatClientException
import logging
import base64
import requests

_logger = logging.getLogger(__name__)

class HrDepartment(models.Model):

    _inherit = "hr.department"

    def _show_queued_notification(self, message):
        """Display the queue notification and close the confirmation dialog."""
        action = notification_action(message)
        action["params"]["next"] = {"type": "ir.actions.act_window_close"}
        return action

    @api.model_create_multi
    def create(self, vals_list):
        departments = super().create(vals_list)
        if not self.env.context.get("skip_wework_contact_sync"):
            for department in departments:
                config = department.company_id.wework_id
                if config and config.contact_sync_enabled:
                    self.env["wework.contact.sync.job"]._queue_department(department, config)
        return departments

    def write(self, vals):
        result = super().write(vals)
        sync_fields = {"company_id", "name", "parent_id"}
        if sync_fields.intersection(vals) and not self.env.context.get("skip_wework_contact_sync"):
            for department in self:
                config = department.company_id.wework_id
                if config and config.contact_sync_enabled:
                    self.env["wework.contact.sync.job"]._queue_department(department, config)
        return result

    def _action_pops_up_confirm(self):
        """
        Queue department synchronization from WeWork.

        The confirmation callback must return quickly. The external API call is
        performed by ``wework.contact.sync.job`` in the background.
        """
        self.env["wework.contact.sync.job"].action_queue_departments_from_wework()
        return self._show_queued_notification(_("Department synchronization has been queued."))

    @api.model
    def _sync_employees(self):
        """
        Queue employee synchronization from WeWork department by department.
        """
        jobs = self.env["wework.contact.sync.job"].action_queue_users_from_wework()
        return self._show_queued_notification(
            _("Queued %s employee synchronization jobs.") % len(jobs),
        )

    def _build_binding_employee_domain(self, data):
        """
        Build domain according sync policy

        :param data: user data from wework
        :return domain: domain to search employee
        """
        _logger.debug(f"[QingERP WeCom] Sync Employee data:{data}")
        user_id = data.get('userid')
        if not user_id:
            # No WeWork userid means we cannot safely bind an existing employee.
            return [('id', '=', 0)]

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        mobile = (data.get('mobile') or '').strip()
        telephone = (data.get('telephone') or '').strip()
        phone_candidates = [phone for phone in [telephone, mobile] if phone]

        domain = [('wework_user_id', '=', user_id), ('company_id', '=', self.env.company.id)]
        employee_id = self.env['hr.employee'].sudo().search(domain, limit=1)
        if employee_id:
            _logger.debug("[QingERP WeCom] Found employee: {} from wework.".format(employee_id.name))
            return domain

        policy = self.company_id.wework_id.emp_sync_policy
        base_domain = [('company_id', '=', self.env.company.id), ('wework_user_id', '=', False)]

        if policy == 'name':
            domain = base_domain + [('name', '=', name)] if name else [('id', '=', 0)]
        elif policy == 'phone':
            domain = base_domain + [('mobile_phone', 'in', phone_candidates)] if phone_candidates else [('id', '=', 0)]
        elif policy == 'email':
            domain = base_domain + [('work_email', '=', email)] if email else [('id', '=', 0)]
        elif policy == "email_or_phone":
            if email and phone_candidates:
                domain = base_domain + [
                    '|', ('mobile_phone', 'in', phone_candidates), ('work_email', '=', email),
                ]
            elif email:
                domain = base_domain + [('work_email', '=', email)]
            elif phone_candidates:
                domain = base_domain + [('mobile_phone', 'in', phone_candidates)]
            else:
                domain = [('id', '=', 0)]
        else:
            or_domain = []
            if name:
                or_domain.append(('name', '=', name))
            if phone_candidates:
                or_domain.append(('mobile_phone', 'in', phone_candidates))
            if email:
                or_domain.append(('work_email', '=', email))

            if not or_domain:
                domain = [('id', '=', 0)]
            elif len(or_domain) == 1:
                domain = base_domain + [or_domain[0]]
            elif len(or_domain) == 2:
                domain = base_domain + ['|', or_domain[0], or_domain[1]]
            else:
                domain = base_domain + ['|', or_domain[0], '|', or_domain[1], or_domain[2]]

        _logger.debug("[QingERP WeCom] search employee domain: %s" % domain)
        return domain

    @api.model
    def _bind_empolyee_user(self, employee, data):
        """
        Auto bind res users according sync policy

        :param employee: hr.employee record
        :param data: employee data
        :return True/False
        """
        employee_obj = self.env['hr.employee'].sudo().with_context(skip_wework_contact_sync=True)
        user_obj = self.env['res.users'].sudo().with_context(skip_wework_contact_sync=True)
        name = data.get('name')
        if not employee:
            _logger.debug("[QingERP WeCom] Creating new employee: {} from wework.".format(name))
            employee = employee_obj.create(dict(data))

        domain = [('partner_id.name','=', name),('login','=', data['wework_user_id'])]
        user_id = user_obj.search(domain, limit=1)
        if not user_id:
            user_create = self.company_id.wework_id.emp_user_create
            if user_create:
                _logger.info(_("Creating user: %s, data:%s") % (employee.name, data))
                #[FIXME] here we are supposed to use email not wework_id.
                users_data = {
                    "name": data['name'],
                    "login": data['wework_user_id']
                }
                user_id = user_obj.create(users_data)
        if user_id and not user_id.employee_id:
            _logger.debug("[QingERP WeCom]: Bind user:%s, %s" % (user_id.name, user_id))
            employee.with_context(skip_wework_contact_sync=True).write({'user_id': user_id.id})
        return employee

    def _sync_employee_direct_leader(self, employee, user_data):
        """
        Sync employee manager according `direct_leader` list from WeWork.

        :param employee: hr.employee record
        :param user_data: user data from WeWork
        :return : True
        """
        leaders = user_data.get('direct_leader') or []
        leader_user_id = leaders[0] if leaders else False
        if not leader_user_id:
            if employee.parent_id:
                employee.with_context(skip_wework_contact_sync=True).write({'parent_id': False})
            return True

        domain = [
            ('wework_user_id', '=', leader_user_id),
            ('company_id', '=', self.env.company.id),
        ]
        leader_employee = self.env['hr.employee'].sudo().search(domain, limit=1)
        if not leader_employee:
            _logger.debug(
                "[QingERP WeCom] direct leader %s not found in Odoo for employee %s",
                leader_user_id,
                employee.name,
            )
            return True

        if leader_employee == employee:
            return True

        if employee.parent_id != leader_employee:
            employee.with_context(skip_wework_contact_sync=True).write({'parent_id': leader_employee.id})
        return True


    wework_department_id = fields.Integer("Wework Department Id")

    @api.model
    def action_sync_wework_departments(self):
        """
        Show confirm dialog sync departments
        """
        return confirm_action(self, _("Sync departments"), _("Are you sure to sync departments from wework?"))

    def action_queue_wework_department_sync(self):
        self.ensure_one()
        config = self.company_id.wework_id
        if not config:
            raise UserError(_("Please configure WeWork for this company first."))
        self.env["wework.contact.sync.job"]._queue_department(self, config)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("The department was queued for WeWork synchronization."),
                "type": "success",
            },
        }

    @api.model
    def action_syn_wework_employees(self):
        """
        Show confirm dialog sync employees
        """
        return confirm_action(self, _("Sync employees"), _("Are you sure to sync employees from wework?"), callback="_sync_employees")

    def sync_employees_from_wework(self):
        """
        Getting employees from wework.

        Wework no long return name,email,phone etc. since 06/20/2022
        so we have to use the oauth access token to get users info.
        """
        self.ensure_one()
        employee_obj = self.env['hr.employee'].sudo().with_context(skip_wework_contact_sync=True)
        result = {"created": 0, "skipped": 0, "updated": 0}
        wework = self.company_id.wework_id
        if not wework:
            raise UserError(_("Please configure WeWork for this company first."))

        try:
            if not self.wework_department_id:
                _logger.warning(f"[WeWork]Department {self.name} has no wework department id, skipping syncing employees.")
                result["skipped"] += 1
                return result
            res = wework.get_contact_client().user.list(
                self.wework_department_id, fetch_child=False)
            _logger.debug(f"[WeWork]employees from wework:{res}")
        except WeChatClientException as err:
            if getattr(err, 'errcode', False) == 60003:
                _logger.warning(
                    "[WeWork]Department %s not found on WeWork, skip syncing this department.",
                    self.wework_department_id,
                )
                result["skipped"] += 1
                return result
            _logger.exception("[WeWork]Getting employees from wework error, department id:%s" % self.wework_department_id)
            raise UserError(err)

        for user_data in res:
            if user_data['status'] != 1:
                result["skipped"] += 1
                continue
            # Check if employee exist, if not create new one, then bind user according sync policy
            if not user_data.get('userid'):
                _logger.warning(f"[WeWork]User data from wework has no userid, data:{user_data}")
                result["skipped"] += 1
                continue

            employee = employee_obj.search([('wework_user_id', '=', user_data['userid']),('company_id','=',self.env.company.id)], limit=1)
            emp_data = {
                "name": user_data['name'],
                "wework_user_id": user_data['userid'],
                "sex": "male" if user_data.get('gender') == "1" else "female",
                "department_id": self.id,
                "company_id": self.env.company.id,
            }

            if employee:
                _logger.debug("[QingERP WeCom] Found employee: {} from wework, updating info.".format(employee.name))
                employee.write(emp_data)
                result["updated"] += 1
            else:
                domain = self._build_binding_employee_domain(user_data)
                employee = employee_obj.search(domain, limit=1)
                if employee:
                    _logger.debug("[QingERP WeCom] Found employee by sync policy: %s, updating WeWork fields.", employee.name)
                    employee.write(emp_data)
                    result["updated"] += 1
                else:
                    result["created"] += 1
                employee = self._bind_empolyee_user(employee, emp_data)

            if employee:
                self._sync_employee_direct_leader(employee, user_data)
        return result
