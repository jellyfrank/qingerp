# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from wechatpy.exceptions import WeChatClientException

from .wework_contact_sync_constants import SYNC_JOB_STATES, SYNC_JOB_TYPES


_logger = logging.getLogger(__name__)

ACTIVE_JOB_STATES = ["pending", "waiting_department", "retry", "running"]
PENDING_JOB_STATES = ["pending", "waiting_department", "retry"]
STALE_JOB_MINUTES = 30


class WeworkContactSyncJob(models.Model):
    _name = "wework.contact.sync.job"
    _description = "WeWork Contact Sync Job"
    _order = "id desc"

    def _advisory_lock(self, key):
        """Serialize queue de-duplication for one logical synchronization target."""
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [key])

    def _build_department_payload(self, binding):
        department = binding.department_id
        parent_binding = self._get_department_binding(department.parent_id, binding.wework_id)
        parent_department_id = parent_binding.wework_department_id if parent_binding else False
        return {
            "id": binding.wework_department_id,
            "name": department.name,
            "parentid": parent_department_id,
        }

    def _build_user_payload(self, binding):
        employee = binding.employee_id
        department_binding = self._get_department_binding(employee.department_id, binding.wework_id)
        if not department_binding or department_binding.state != "active" or not department_binding.last_sync_at:
            raise UserError(_("The employee department has not been synchronized to WeWork."))

        gender_value = (
            employee.gender
            if "gender" in employee._fields
            else employee.sex
            if "sex" in employee._fields
            else False
        )
        gender = "1" if gender_value == "male" else "2" if gender_value == "female" else "0"
        enabled = bool(binding.user_id.active and employee.active)
        payload = {
            "department": [department_binding.wework_department_id],
            "email": employee.work_email if "work_email" in employee._fields else False,
            "enable": 1 if enabled else 0,
            "gender": gender,
            "main_department": department_binding.wework_department_id,
            "mobile": employee.mobile_phone if "mobile_phone" in employee._fields else False,
            "name": employee.name,
            "position": employee.job_title if "job_title" in employee._fields else False,
            "telephone": employee.work_phone if "work_phone" in employee._fields else False,
            "userid": binding.wework_user_id,
        }
        return {key: value for key, value in payload.items() if value is not False}

    def _build_user_payload_safe(self, binding):
        employee = binding.employee_id
        return {
            "department_id": employee.department_id.id if employee.department_id else False,
            "employee_id": employee.id,
            "user_id": binding.user_id.id,
        }

    def _claim_jobs(self, batch_size):
        """Atomically claim due jobs so overlapping cron workers cannot run them twice."""
        self.flush_model(["next_run", "state"])
        self.env.cr.execute(
            """
                WITH claimed AS (
                    SELECT id
                      FROM wework_contact_sync_job
                     WHERE state = ANY(%s)
                       AND next_run <= clock_timestamp()
                     ORDER BY CASE WHEN job_type IN ('department', 'department_pull') THEN 0 ELSE 1 END, id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                )
                UPDATE wework_contact_sync_job AS job
                   SET started_at = NOW(),
                       state = 'running',
                       write_date = NOW(),
                       write_uid = %s
                  FROM claimed
                 WHERE job.id = claimed.id
             RETURNING job.id
            """,
            [PENDING_JOB_STATES, max(batch_size, 1), self.env.uid],
        )
        job_ids = [row[0] for row in self.env.cr.fetchall()]
        return self.sudo().browse(job_ids)

    def _config_company(self, wework):
        company_model = self.env["res.company"].sudo()
        company_domain = [("wework_id", "=", wework.id)]
        return company_model.search(company_domain, limit=1)

    def _get_department_binding(self, department, wework):
        if not department or not wework:
            return self.env["wework.department.binding"]

        binding_model = self.env["wework.department.binding"].sudo()
        binding_domain = [("department_id", "=", department.id), ("wework_id", "=", wework.id)]
        binding = binding_model.search(binding_domain, limit=1)
        if binding:
            return binding

        is_root = department == wework.top_department_id
        department_id = 1 if is_root else department.wework_department_id
        if not department_id:
            department_id = -department.id
        return binding_model.create({
            "department_id": department.id,
            "wework_department_id": department_id,
            "wework_id": wework.id,
        })

    def _get_employee(self, user, company):
        employee_model = self.env["hr.employee"].sudo()
        employee_domain = [("company_id", "=", company.id), ("user_id", "=", user.id)]
        return employee_model.search(employee_domain, limit=1)

    def _get_user_binding(self, user, employee, wework):
        binding_model = self.env["wework.contact.binding"].sudo()
        binding_domain = [("user_id", "=", user.id), ("wework_id", "=", wework.id)]
        binding = binding_model.search(binding_domain, limit=1)
        if binding:
            if binding.employee_id != employee:
                binding.write({"employee_id": employee.id})
            return binding

        user_id = employee.wework_user_id or self._make_wework_user_id(user)
        binding = binding_model.create({
            "employee_id": employee.id,
            "user_id": user.id,
            "wework_id": wework.id,
            "wework_user_id": user_id,
        })
        if not employee.wework_user_id:
            employee.with_context(skip_wework_contact_sync=True).write({"wework_user_id": user_id})
        return binding

    def _is_managed_department(self, department, wework):
        top_department = wework.top_department_id
        if not department or not top_department:
            return False
        if department.company_id != top_department.company_id:
            return False
        department_model = self.env["hr.department"].sudo()
        domain = [("id", "=", department.id), ("id", "child_of", top_department.id)]
        return bool(department_model.search_count(domain))

    def _job_key_for_binding(self, job_type, wework, binding=False, department=False):
        target_id = binding.id if binding else department.id if department else 0
        return "%s:%s:%s" % (job_type, wework.id, target_id)

    def _make_wework_user_id(self, user):
        database_name = re.sub(r"[^0-9A-Za-z_-]", "_", self.env.cr.dbname)
        return "odoo_%s_%s" % (database_name[:48], user.id)

    def _mark_done(self, payload=False, result=False):
        self.ensure_one()
        values = {
            "finished_at": fields.Datetime.now(),
            "last_error": False,
            "result_summary": json.dumps(result or {}, sort_keys=True),
            "state": "done",
        }
        self.write(values)
        if not payload:
            return

        now = fields.Datetime.now()
        payload_hash = self._payload_hash(payload)
        if self.department_binding_id:
            self.department_binding_id.write({
                "last_error": False,
                "last_sync_at": now,
                "last_sync_hash": payload_hash,
                "state": "active",
            })
        if self.binding_id:
            self.binding_id.write({
                "last_error": False,
                "last_sync_at": now,
                "last_sync_hash": payload_hash,
                "state": "active",
            })

    def _mark_retry(self, error):
        self.ensure_one()
        attempts = self.attempts + 1
        delay = min(60, 2 ** attempts)
        state = "failed" if attempts >= 3 else "retry"
        self.write({
            "attempts": attempts,
            "finished_at": fields.Datetime.now() if state == "failed" else False,
            "last_error": str(error),
            "next_run": fields.Datetime.now() + timedelta(minutes=delay),
            "state": state,
        })

    def _mark_waiting_department(self, error=False):
        self.ensure_one()
        self.write({
            "last_error": error or False,
            "next_run": fields.Datetime.now() + timedelta(minutes=1),
            "state": "waiting_department",
        })

    def _payload_hash(self, payload):
        source = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _queue_department(self, department, wework):
        if not self._is_managed_department(department, wework):
            return self
        binding = self._get_department_binding(department, wework)
        if department != wework.top_department_id and department.parent_id:
            self._queue_department(department.parent_id, wework)
        company = self._config_company(wework)
        payload = self._build_department_payload(binding)
        return self._queue_job({
            "company_id": (company or department.company_id).id,
            "department_binding_id": binding.id,
            "job_key": self._job_key_for_binding("department", wework, binding=binding),
            "job_type": "department",
            "payload": json.dumps(payload, sort_keys=True),
            "state": "pending",
            "wework_id": wework.id,
        })

    def _queue_department_pull(self, wework, company):
        if not wework.top_department_id:
            raise UserError(_("Please configure a top department before synchronizing departments from WeWork."))
        return self._queue_job({
            "company_id": company.id,
            "job_key": self._job_key_for_binding("department_pull", wework),
            "job_type": "department_pull",
            "state": "pending",
            "wework_id": wework.id,
        })

    def _queue_job(self, values):
        job_model = self.sudo()
        self._advisory_lock(values["job_key"])
        domain = [("job_key", "=", values["job_key"]), ("state", "in", ACTIVE_JOB_STATES)]
        job = job_model.search(domain, limit=1)
        if job:
            if job.state != "running":
                job.write(values)
            return job
        values.setdefault("requested_by_id", self.env.user.id)
        return job_model.create(values)

    def _queue_user(self, user, wework):
        company = self._config_company(wework)
        if not company or user.share:
            return self

        employee = self._get_employee(user, company)
        if not employee or not self._is_managed_department(employee.department_id, wework):
            return self

        department_job = self._queue_department(employee.department_id, wework)
        binding = self._get_user_binding(user, employee, wework)
        payload = self._build_user_payload_safe(binding)
        state = "pending" if department_job.state == "done" else "waiting_department"
        return self._queue_job({
            "binding_id": binding.id,
            "company_id": company.id,
            "job_key": self._job_key_for_binding("user", wework, binding=binding),
            "job_type": "user",
            "next_run": fields.Datetime.now() if state == "pending" else fields.Datetime.now() + timedelta(minutes=1),
            "payload": json.dumps(payload, sort_keys=True),
            "state": state,
            "wework_id": wework.id,
        })

    def _queue_user_pull(self, department, wework, company):
        if not department.wework_department_id:
            return self
        return self._queue_job({
            "company_id": company.id,
            "department_id": department.id,
            "job_key": self._job_key_for_binding("user_pull", wework, department=department),
            "job_type": "user_pull",
            "state": "pending",
            "wework_id": wework.id,
        })

    def _reset_stale_jobs(self):
        stale_before = fields.Datetime.now() - timedelta(minutes=STALE_JOB_MINUTES)
        domain = [("state", "=", "running"), ("started_at", "<", stale_before)]
        stale_jobs = self.sudo().search(domain)
        stale_jobs.write({
            "last_error": _("The worker did not finish within %s minutes; the job was re-queued.") % STALE_JOB_MINUTES,
            "next_run": fields.Datetime.now(),
            "state": "retry",
        })

    def _run(self):
        self.ensure_one()
        if self.job_type == "department":
            self._run_department_job()
            return
        if self.job_type == "user":
            self._run_user_job()
            return
        if self.job_type == "department_pull":
            self._run_department_pull_job()
            return
        self._run_user_pull_job()

    def _run_department_job(self):
        binding = self.department_binding_id
        wework = self.wework_id
        department = binding.department_id
        if department == wework.top_department_id:
            if binding.wework_department_id != 1:
                binding.write({"wework_department_id": 1})
            if department.wework_department_id != 1:
                department.with_context(skip_wework_contact_sync=True).write({"wework_department_id": 1})
            self._mark_done({"id": 1, "root": True}, {"updated": 1})
            return

        parent_binding = self._get_department_binding(department.parent_id, wework)
        if not parent_binding or not parent_binding.last_sync_at:
            self._mark_waiting_department(_("The parent department has not been synchronized yet."))
            return

        payload = self._build_department_payload(binding)
        client = wework.get_contact_client()
        if binding.last_sync_at:
            client.department.update(
                binding.wework_department_id,
                name=payload["name"],
                parent_id=payload["parentid"],
            )
        else:
            result = client.department.create(payload["name"], parent_id=payload["parentid"])
            department_id = result.get("id")
            if not department_id:
                raise UserError(_("WeWork did not return a department ID."))
            binding.write({"wework_department_id": department_id})
            if department.wework_department_id != department_id:
                department.with_context(skip_wework_contact_sync=True).write({"wework_department_id": department_id})
            payload["id"] = department_id
        self._mark_done(payload, {"updated": 1})

    def _run_department_pull_job(self):
        self.ensure_one()
        company = self.company_id
        top_department = self.wework_id.top_department_id.with_company(company)
        if not company or not top_department:
            raise UserError(_("Please configure a company and top department before synchronizing departments."))

        remote_departments = self.wework_id.get_contact_client().department.list() or []
        remote_by_id = {int(item["id"]): item for item in remote_departments if item.get("id")}
        Department = self.env["hr.department"].sudo().with_company(company).with_context(skip_wework_contact_sync=True)
        existing_domain = [
            ("company_id", "=", company.id),
            ("wework_department_id", "in", list(remote_by_id)),
        ]
        local_departments = Department.search(existing_domain)
        local_by_wework_id = {department.wework_department_id: department for department in local_departments}
        local_by_wework_id[1] = top_department
        created_count = 0
        updated_count = 0

        root_data = remote_by_id.pop(1, False)
        if root_data:
            root_values = {"name": root_data.get("name") or top_department.name, "wework_department_id": 1}
            top_department.with_context(skip_wework_contact_sync=True).write(root_values)
            updated_count += 1

        pending_departments = list(remote_by_id.values())
        while pending_departments:
            remaining_departments = []
            processed_count = 0
            for remote_department in pending_departments:
                remote_id = int(remote_department["id"])
                parent_id = int(remote_department.get("parentid") or 1)
                parent = local_by_wework_id.get(parent_id)
                if parent_id in remote_by_id and not parent:
                    remaining_departments.append(remote_department)
                    continue
                parent = parent or top_department
                values = {
                    "company_id": company.id,
                    "name": remote_department.get("name") or str(remote_id),
                    "parent_id": parent.id,
                    "wework_department_id": remote_id,
                }
                department = local_by_wework_id.get(remote_id)
                if department:
                    department.write(values)
                    updated_count += 1
                else:
                    department = Department.create(values)
                    created_count += 1
                local_by_wework_id[remote_id] = department
                processed_count += 1
            if not processed_count:
                for remote_department in remaining_departments:
                    remote_id = int(remote_department["id"])
                    values = {
                        "company_id": company.id,
                        "name": remote_department.get("name") or str(remote_id),
                        "parent_id": top_department.id,
                        "wework_department_id": remote_id,
                    }
                    department = local_by_wework_id.get(remote_id)
                    if department:
                        department.write(values)
                        updated_count += 1
                    else:
                        department = Department.create(values)
                        created_count += 1
                    local_by_wework_id[remote_id] = department
                break
            pending_departments = remaining_departments
        self._mark_done(result={"created": created_count, "updated": updated_count})

    def _run_user_job(self):
        binding = self.binding_id
        payload = self._build_user_payload(binding)
        client = self.wework_id.get_contact_client()
        user_exists = False
        try:
            client.user.get(binding.wework_user_id)
            user_exists = True
        except WeChatClientException as error:
            if getattr(error, "errcode", False) not in (60111, "60111"):
                raise

        route = "user/update" if user_exists else "user/create"
        client._post(route, data=payload)
        self._mark_done(payload, {"updated": 1})

    def _run_user_pull_job(self):
        self.ensure_one()
        if not self.department_id:
            raise UserError(_("The source department no longer exists."))
        result = self.department_id.with_company(self.company_id).sync_employees_from_wework()
        self._mark_done(result=result)

    def _trigger_cron(self):
        cron = self.env.ref("qing_wework.ir_cron_wework_contact_sync", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    attempts = fields.Integer(default=0, readonly=True)
    binding_id = fields.Many2one("wework.contact.binding", ondelete="cascade", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    department_binding_id = fields.Many2one("wework.department.binding", ondelete="cascade", readonly=True)
    department_id = fields.Many2one("hr.department", ondelete="cascade", readonly=True)
    finished_at = fields.Datetime(readonly=True)
    job_key = fields.Char(readonly=True, index=True)
    job_type = fields.Selection(SYNC_JOB_TYPES, required=True, readonly=True)
    last_error = fields.Text(readonly=True)
    next_run = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    payload = fields.Text(readonly=True)
    requested_by_id = fields.Many2one("res.users", readonly=True)
    result_summary = fields.Text(readonly=True)
    started_at = fields.Datetime(readonly=True)
    state = fields.Selection(SYNC_JOB_STATES, default="pending", required=True, readonly=True, index=True)
    wework_id = fields.Many2one("qing.wework", required=True, ondelete="cascade", readonly=True)

    @api.model
    def action_queue_department_tree(self, wework):
        wework.ensure_one()
        if not wework.top_department_id:
            raise UserError(_("Please configure a top department before synchronizing the department tree."))
        department_model = self.env["hr.department"].sudo()
        domain = [("id", "child_of", wework.top_department_id.id)]
        if wework.top_department_id.company_id:
            domain.append(("company_id", "=", wework.top_department_id.company_id.id))
        departments = department_model.search(domain)
        for department in departments:
            self._queue_department(department, wework)
        self._trigger_cron()
        return len(departments)

    @api.model
    def action_queue_departments_from_wework(self, company=False):
        company = company or self.env.company
        wework = company.wework_id
        if not wework:
            raise UserError(_("Please configure WeWork for this company first."))
        job = self._queue_department_pull(wework, company)
        self._trigger_cron()
        return job

    @api.model
    def action_queue_users(self, users, wework=False):
        users = users.sudo()
        wework_model = self.env["qing.wework"].sudo()
        weworks = wework or wework_model.search([("contact_sync_enabled", "=", True)])
        jobs = self
        for config in weworks:
            for user in users:
                job = self._queue_user(user, config)
                if job:
                    jobs |= job
        if jobs:
            self._trigger_cron()
        return jobs

    @api.model
    def action_queue_users_from_wework(self, company=False):
        company = company or self.env.company
        wework = company.wework_id
        if not wework:
            raise UserError(_("Please configure WeWork for this company first."))
        department_model = self.env["hr.department"].sudo()
        department_domain = [
            ("company_id", "=", company.id),
            ("wework_department_id", "!=", False),
        ]
        departments = department_model.search(department_domain)
        jobs = self
        for department in departments:
            job = self._queue_user_pull(department, wework, company)
            if job:
                jobs |= job
        if jobs:
            self._trigger_cron()
        return jobs

    def action_process(self):
        runnable_jobs = self.filtered(lambda job: job.state in PENDING_JOB_STATES)
        runnable_jobs.write({"started_at": fields.Datetime.now(), "state": "running"})
        self._process_jobs(runnable_jobs)
        return True

    def _process_jobs(self, jobs):
        for job in jobs:
            try:
                job._run()
            except UserError as error:
                if job.job_type == "user" and "department" in str(error).lower():
                    job._mark_waiting_department(str(error))
                else:
                    job.write({
                        "finished_at": fields.Datetime.now(),
                        "last_error": str(error),
                        "state": "conflict",
                    })
            except WeChatClientException as error:
                _logger.warning("WeWork contact synchronization failed for job %s: %s", job.id, error)
                job._mark_retry(error)
            except Exception as error:
                _logger.exception("Unexpected WeWork contact synchronization failure for job %s", job.id)
                job._mark_retry(error)

    def action_retry(self):
        self.write({
            "attempts": 0,
            "finished_at": False,
            "last_error": False,
            "next_run": fields.Datetime.now(),
            "state": "pending",
        })
        self._trigger_cron()
        return True

    def action_cancel(self):
        self.filtered(lambda job: job.state in ACTIVE_JOB_STATES).write({
            "finished_at": fields.Datetime.now(),
            "state": "cancelled",
        })
        return True

    @api.model
    def cron_process_jobs(self):
        self._reset_stale_jobs()
        config_model = self.env["qing.wework"].sudo()
        configs = config_model.search([("contact_sync_enabled", "=", True)])
        batch_size = max(configs.mapped("contact_sync_batch_size") or [50])
        jobs = self._claim_jobs(batch_size)
        self._process_jobs(jobs)
        try:
            self.env["ir.cron"]._notify_progress(done=len(jobs), remaining=0)
        except Exception:
            _logger.debug("WeWork cron progress notification skipped.", exc_info=True)
        return bool(jobs)
