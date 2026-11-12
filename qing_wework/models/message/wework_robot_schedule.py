#!/usr/bin/python3
# @Time    : 2026-07-06
# @Author  : Kevin Kong (kfx2007@163.com)

import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

LOG_STATES = [
    ("success", "Success"),
    ("failed", "Failed"),
]

REPEAT_POLICIES = [
    ("once", "Send Once"),
    ("interval", "Repeat By Interval"),
]


class WeworkRobotSchedule(models.Model):
    _name = "wework.robot.schedule"
    _description = "WeWork Robot Schedule"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    def _eval_domain(self):
        """Evaluate user-defined domain with a small safe context."""
        self.ensure_one()
        if not self.domain:
            return []

        eval_context = {
            "context": dict(self.env.context),
            "date": fields.Date,
            "datetime": fields.Datetime,
            "uid": self.env.uid,
            "user": self.env.user,
        }
        domain = safe_eval(self.domain, eval_context)
        if not isinstance(domain, (list, tuple)):
            raise ValidationError(_("Domain must be a list or tuple."))
        return list(domain)

    def _get_due_domain(self, now_dt):
        """Build domain for records that should be reminded now."""
        self.ensure_one()
        field_name = self.datetime_field_id.name
        before_delta = timedelta(minutes=self.remind_before_minutes or 0)
        due_before = fields.Datetime.to_string(now_dt + before_delta)
        domain = self._eval_domain()
        domain += [
            (field_name, "!=", False),
            (field_name, "<=", due_before),
        ]
        if self.max_overdue_hours:
            overdue_delta = timedelta(hours=self.max_overdue_hours)
            due_after = fields.Datetime.to_string(now_dt - overdue_delta)
            domain.append((field_name, ">=", due_after))
        return domain

    def _get_last_success_log(self, record):
        """Return latest successful log for this schedule and record."""
        self.ensure_one()
        Log = self.env["wework.robot.schedule.log"].sudo()
        domain = [
            ("schedule_id", "=", self.id),
            ("res_id", "=", record.id),
            ("res_model", "=", record._name),
            ("state", "=", "success"),
        ]
        return Log.search(domain, order="create_date desc", limit=1)

    def _is_record_already_sent(self, record, now_dt):
        """Return whether current record should be skipped by repeat policy."""
        self.ensure_one()
        last_log = self._get_last_success_log(record)
        if not last_log:
            return False
        if self.repeat_policy == "once":
            return True

        last_create_dt = fields.Datetime.to_datetime(last_log.create_date)
        if not last_create_dt:
            return False

        repeat_delta = timedelta(hours=self.repeat_interval_hours or 1)
        return last_create_dt >= now_dt - repeat_delta

    def _prepare_log_values(self, record, state, error_message=False):
        """Prepare schedule log values for a business record."""
        self.ensure_one()
        field_name = self.datetime_field_id.name
        return {
            "datetime_value": record[field_name],
            "error_message": error_message,
            "name": "%s - %s" % (self.name, record.display_name),
            "res_id": record.id,
            "res_model": record._name,
            "schedule_id": self.id,
            "server_action_id": self.server_action_id.id,
            "state": state,
        }

    def _run_server_action_for_record(self, record):
        """Run configured server action for a single business record."""
        self.ensure_one()
        context = dict(self.env.context)
        context.update(
            active_id=record.id,
            active_ids=[record.id],
            active_model=record._name,
        )
        return self.server_action_id.with_context(context).sudo().run()

    @api.constrains("datetime_field_id", "model_id")
    def _check_datetime_field_id(self):
        for schedule in self:
            field = schedule.datetime_field_id
            if not field:
                continue
            if field.model_id != schedule.model_id:
                raise ValidationError(_("Datetime field must belong to selected model."))
            if field.ttype != "datetime":
                raise ValidationError(_("Datetime field must be a Datetime field."))

    @api.constrains("model_id", "server_action_id")
    def _check_server_action_id(self):
        for schedule in self:
            action = schedule.server_action_id
            if not action:
                continue
            action_model = action.binding_model_id or action.model_id
            if action_model != schedule.model_id:
                raise ValidationError(_("Server action must belong to selected model."))

    @api.constrains("batch_size", "max_overdue_hours", "remind_before_minutes", "repeat_interval_hours")
    def _check_schedule_numbers(self):
        for schedule in self:
            if schedule.batch_size <= 0:
                raise ValidationError(_("Batch size must be greater than 0."))
            if schedule.max_overdue_hours < 0:
                raise ValidationError(_("Max overdue hours must be greater than or equal to 0."))
            if schedule.remind_before_minutes < 0:
                raise ValidationError(_("Remind before minutes must be greater than or equal to 0."))
            if schedule.repeat_interval_hours <= 0:
                raise ValidationError(_("Repeat interval hours must be greater than 0."))

    active = fields.Boolean("Active", default=True, tracking=True)
    batch_size = fields.Integer("Batch Size", default=100, required=True)
    datetime_field_id = fields.Many2one(
        "ir.model.fields",
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]",
        ondelete="cascade",
        required=True,
        string="Datetime Field",
    )
    domain = fields.Text("Domain", default="[]", required=True)
    last_run_at = fields.Datetime("Last Run At", readonly=True)
    log_count = fields.Integer("Log Count", compute="_compute_log_count")
    max_overdue_hours = fields.Integer("Max Overdue Hours", default=24, required=True)
    model_id = fields.Many2one("ir.model", string="Model", ondelete="cascade", required=True)
    name = fields.Char("Name", required=True, tracking=True)
    remind_before_minutes = fields.Integer("Remind Before Minutes", default=0, required=True)
    repeat_interval_hours = fields.Integer("Repeat Interval Hours", default=24, required=True)
    repeat_policy = fields.Selection(REPEAT_POLICIES, string="Repeat Policy", default="once", required=True)
    server_action_id = fields.Many2one(
        "ir.actions.server",
        domain="['|', ('model_id', '=', model_id), ('binding_model_id', '=', model_id)]",
        required=True,
        string="Server Action",
    )

    @api.depends("name")
    def _compute_log_count(self):
        Log = self.env["wework.robot.schedule.log"].sudo()
        for schedule in self:
            domain = [("schedule_id", "=", schedule.id)]
            schedule.log_count = Log.search_count(domain)

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.datetime_field_id = False
        self.server_action_id = False

    @api.model
    def cron_run_due_schedules(self):
        """Run all active WeWork robot schedules."""
        domain = [("active", "=", True)]
        schedules = self.search(domain)
        return schedules.run_due_schedules()

    def action_view_logs(self):
        """Open logs generated by current schedule."""
        self.ensure_one()
        return {
            "domain": [("schedule_id", "=", self.id)],
            "name": _("Schedule Logs"),
            "res_model": "wework.robot.schedule.log",
            "target": "current",
            "type": "ir.actions.act_window",
            "view_mode": "list,form",
        }

    def run_due_schedules(self):
        """Run due records for selected schedules."""
        Log = self.env["wework.robot.schedule.log"].sudo()
        now_dt = fields.Datetime.to_datetime(fields.Datetime.now())
        for schedule in self:
            Model = self.env[schedule.model_id.model].sudo()
            due_domain = schedule._get_due_domain(now_dt)
            records = Model.search(due_domain, limit=schedule.batch_size or 100)
            _logger.info("[WeWork Robot] schedule %s found %s due records", schedule.name, len(records))
            for record in records:
                if schedule._is_record_already_sent(record, now_dt):
                    continue
                try:
                    schedule._run_server_action_for_record(record)
                    values = schedule._prepare_log_values(record, "success")
                except Exception as err:
                    _logger.exception("[WeWork Robot] schedule %s failed for %s", schedule.name, record.display_name)
                    values = schedule._prepare_log_values(record, "failed", error_message=str(err))
                Log.create(values)
            schedule.last_run_at = fields.Datetime.now()
        return True


class WeworkRobotScheduleLog(models.Model):
    _name = "wework.robot.schedule.log"
    _description = "WeWork Robot Schedule Log"
    _order = "create_date desc"

    datetime_value = fields.Datetime("Datetime Value", readonly=True)
    error_message = fields.Text("Error Message", readonly=True)
    name = fields.Char("Name", readonly=True)
    res_id = fields.Integer("Resource ID", readonly=True)
    res_model = fields.Char("Resource Model", readonly=True)
    schedule_id = fields.Many2one("wework.robot.schedule", ondelete="cascade", readonly=True, string="Schedule")
    server_action_id = fields.Many2one("ir.actions.server", readonly=True, string="Server Action")
    state = fields.Selection(LOG_STATES, string="Status", readonly=True)
