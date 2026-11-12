#!/usr/bin/python3
# @Time    : 2026-07-08
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tools.safe_eval import safe_eval
import logging
import re

_logger = logging.getLogger(__name__)

EXPRESSION_PATTERN = re.compile(r"\$\{([^}]+)\}")

ROBOT_TEMPLATE_TYPES = [
    ("text", "Text Message"),
    ("markdown", "Markdown Message"),
    ("markdown_v2", "Markdown V2 Message"),
    ("file", "File"),
]


class WeworkRobotTemplate(models.Model):
    _name = "wework.robot.template"
    _description = "Wework Robot Message Template"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    def _get_render_context(self, record, extra_context=None):
        """Build a small expression context for template rendering."""
        base_url = self.env["ir.config_parameter"].sudo().get_str(
            "web.base.url",
            default="",
        )
        raw_record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"
        company = record.company_id if "company_id" in record._fields else self.env.company
        authorized_record_url = raw_record_url
        if company.wework_id:
            authorized_record_url = company.wework_id.get_authorized_url(raw_record_url)
        context = {
            "authorized_record_url": authorized_record_url,
            "company": company,
            "ctx": dict(self.env.context),
            "env": self.env,
            "object": record,
            "raw_record_url": raw_record_url,
            "record": record,
            "record_url": authorized_record_url,
            "user": self.env.user,
        }
        if extra_context:
            context.update(extra_context)
        return context

    def _render_content(self, record, extra_context=None):
        """Render ${...} expressions against one business record."""
        self.ensure_one()
        context = self._get_render_context(record, extra_context=extra_context)

        def replace_expression(match):
            expression = match.group(1).strip()
            if not expression:
                return ""
            try:
                value = safe_eval(expression, context, mode="eval")
            except Exception as err:
                _logger.exception("[Wework Robot] template expression error")
                raise UserError(_("Failed to render expression %s: %s") % (expression, err))
            return "" if value is None or value is False else str(value)

        return EXPRESSION_PATTERN.sub(replace_expression, self.content_template or "")

    def _get_employee_from_user(self, user, company):
        """Return the best employee match for a user in a company."""
        if not user:
            return self.env["hr.employee"]
        employees = user.employee_ids if "employee_ids" in user._fields else user.employee_id
        employees = employees.filtered(lambda emp: emp.company_id == company) or employees
        return employees[:1]

    def _get_employees_from_field(self, record, field):
        """Resolve hr.employee recipients from a configured record field."""
        value = record[field.name]
        if not value:
            return self.env["hr.employee"]
        if field.relation == "hr.employee":
            return value
        if field.relation != "res.users":
            return self.env["hr.employee"]
        employees = self.env["hr.employee"]
        company = record.company_id if "company_id" in record._fields else self.env.company
        for user in value:
            employees |= self._get_employee_from_user(user, company)
        return employees

    def _get_record_employees(self, record):
        """Collect employees to mention from static and dynamic rules."""
        self.ensure_one()
        company = record.company_id if "company_id" in record._fields else self.env.company
        employees = self.employee_ids
        for group in self.group_ids:
            for user in group.users:
                employees |= self._get_employee_from_user(user, company)

        if self.mention_create_uid and "create_uid" in record._fields:
            employees |= self._get_employee_from_user(record.create_uid, company)

        if self.mention_responsible and "user_id" in record._fields:
            employees |= self._get_employee_from_user(record.user_id, company)

        if (
            self.mention_followers
            and "message_partner_ids" in record._fields
            and "employee_ids" in record.message_partner_ids._fields
        ):
            employees |= record.message_partner_ids.employee_ids

        for field in self.mention_field_ids:
            if field.name in record._fields:
                employees |= self._get_employees_from_field(record, field)

        return employees.filtered(lambda emp: emp.wework_user_id)

    def _append_markdown_mentions(self, content, employees):
        """Append WeWork markdown mention tags to rendered content."""
        self.ensure_one()
        if self.message_type != "markdown":
            return content
        if self.mention_all:
            return "%s\n\n<@all>" % (content or "")
        mentions = " ".join("<@%s>" % emp.wework_user_id for emp in employees if emp.wework_user_id)
        if not mentions:
            return content
        return "%s\n\n%s" % (content or "", mentions)

    def _prepare_robot_message_values(self, record, extra_context=None):
        """Prepare one robot message from the template and business record."""
        self.ensure_one()
        employees = self.env["hr.employee"]
        content = False
        if self.message_type != "file":
            content = self._render_content(record, extra_context=extra_context)
        if self.message_type in ("text", "markdown"):
            employees = self._get_record_employees(record)
            content = self._append_markdown_mentions(content, employees)
        if self.message_type in ("text", "markdown") and not self.mention_all and not employees:
            raise UserError(_("No WeWork employee can be mentioned for record %s.") % record.display_name)
        return {
            "content": content,
            "employee_ids": [Command.set(employees.ids)],
            "file": self.file,
            "file_name": self.file_name,
            "mention_all": self.mention_all,
            "name": "%s - %s" % (self.name, record.display_name),
            "res_id": record.id,
            "res_model": record._name,
            "robot_id": self.robot_id.id,
            "template_id": self.id,
            "type": self.message_type,
        }

    active = fields.Boolean("Active", default=True)
    content_template = fields.Text(
        "Content Template",
        help="Use ${object.name}, ${record_url}, ${raw_record_url}, or other expressions from the record context.",
    )
    employee_ids = fields.Many2many(
        "hr.employee",
        string="Fixed Mentioned Employees",
        domain=[("wework_user_id", "!=", False)],
    )
    file = fields.Binary("File")
    file_name = fields.Char("File Name")
    group_ids = fields.Many2many(
        "res.groups",
        "wework_robot_template_res_groups_rel",
        "template_id",
        "group_id",
        string="指定用户组",
        help="Mention employees whose users belong to these groups.",
    )
    mention_all = fields.Boolean("Mention All")
    mention_create_uid = fields.Boolean("Mention Creator")
    mention_field_ids = fields.Many2many(
        "ir.model.fields",
        string="Mention Fields",
        help="Select fields on the target model that point to res.users or hr.employee.",
    )
    mention_followers = fields.Boolean("Mention Followers")
    mention_responsible = fields.Boolean("Mention Responsible User")
    message_type = fields.Selection(ROBOT_TEMPLATE_TYPES, string="Message Type", default="text", required=True)
    model_id = fields.Many2one("ir.model", string="Target Model", ondelete="cascade", required=True)
    name = fields.Char("Name", required=True)
    robot_id = fields.Many2one("wework.robot", string="Robot", required=True)
    server_action_id = fields.Many2one("ir.actions.server", string="Server Action", readonly=True)

    @api.constrains("content_template", "file", "message_type")
    def _check_message_content(self):
        for template in self:
            if template.message_type == "file" and not template.file:
                raise UserError(_("A file is required for a file robot template."))
            if template.message_type != "file" and not template.content_template:
                raise UserError(_("Content is required for text and markdown robot templates."))

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.mention_field_ids = False

    def action_create_server_action(self):
        """Create or update a server action that sends this robot template."""
        self.ensure_one()
        code = "env['wework.robot.template'].browse(%s).send_for_records(records)" % self.id
        values = {
            "binding_model_id": self.model_id.id,
            "code": code,
            "model_id": self.model_id.id,
            "name": _("Send WeWork Robot: %s") % self.name,
            "state": "code",
        }
        if self.server_action_id:
            self.server_action_id.write(values)
            action = self.server_action_id
        else:
            action = self.env["ir.actions.server"].sudo().create(values)
            self.server_action_id = action.id

        return {
            "res_id": action.id,
            "res_model": "ir.actions.server",
            "target": "current",
            "type": "ir.actions.act_window",
            "view_mode": "form",
        }

    def send_for_records(self, records, extra_context=None):
        """Build and send robot messages for a recordset."""
        self.ensure_one()
        if not records:
            return self.env["wework.robot.message"]
        if any(record._name != self.model_id.model for record in records):
            raise UserError(_("Template %s can only be used on model %s.") % (self.name, self.model_id.model))

        message_obj = self.env["wework.robot.message"].sudo()
        messages = self.env["wework.robot.message"]
        for record in records:
            message = message_obj.create(self._prepare_robot_message_values(record, extra_context=extra_context))
            message.send()
            messages |= message
        return messages


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def action_send_wework_robot_template(self, template_id=False, xmlid=False, extra_context=None):
        """Send a configured WeWork robot template for this recordset."""
        if xmlid:
            template = self.env.ref(xmlid)
        elif template_id:
            template = self.env["wework.robot.template"].browse(template_id)
        else:
            raise UserError(_("Please provide a WeWork robot template."))
        return template.send_for_records(self, extra_context=extra_context)
