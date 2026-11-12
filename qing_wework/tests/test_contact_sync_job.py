# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWeWorkContactSyncJob(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.top_department = cls.env["hr.department"].with_context(
            skip_wework_contact_sync=True,
        ).create({
            "company_id": cls.company.id,
            "name": "WeWork Root",
            "wework_department_id": 1,
        })
        cls.wework = cls.env["qing.wework"].create({
            "company_id": "test-corp-id",
            "contact_sync_enabled": True,
            "contact_sync_secret": "test-contact-secret",
            "emp_user_create": True,
            "name": "Test WeWork",
            "top_department_id": cls.top_department.id,
        })
        cls.company.wework_id = cls.wework
        cls.Job = cls.env["wework.contact.sync.job"]

    def _queue_user_pull_job(self, department):
        return self.Job._queue_user_pull(department, self.wework, self.company)

    def test_department_pull_queue_deduplicates_active_job(self):
        with patch.object(type(self.Job), "_trigger_cron"):
            first_job = self.Job.action_queue_departments_from_wework(self.company)
            second_job = self.Job.action_queue_departments_from_wework(self.company)

        self.assertEqual(first_job, second_job)
        self.assertEqual(first_job.job_type, "department_pull")
        self.assertEqual(first_job.state, "pending")

    def test_action_process_marks_job_running(self):
        job = self.Job._queue_department_pull(self.wework, self.company)
        with patch.object(type(self.Job), "_process_jobs") as process_jobs:
            job.action_process()

        job.invalidate_recordset(["state", "started_at"])
        self.assertEqual(job.state, "running")
        self.assertTrue(job.started_at)
        process_jobs.assert_called_once()

    def test_claim_marks_job_running(self):
        job = self.Job._queue_department_pull(self.wework, self.company)
        claimed_jobs = self.Job._claim_jobs(1)

        self.assertIn(job, claimed_jobs)
        job.invalidate_recordset(["state", "started_at"])
        self.assertEqual(job.state, "running")
        self.assertTrue(job.started_at)

    def test_contact_sync_job_action_is_scoped_to_wework(self):
        action = self.wework.action_open_contact_sync_jobs()

        self.assertEqual(action["res_model"], "wework.contact.sync.job")
        self.assertEqual(action["domain"], [("wework_id", "=", self.wework.id)])

    def test_confirmation_callback_closes_dialog_after_queuing(self):
        with patch.object(
            type(self.Job),
            "action_queue_departments_from_wework",
        ):
            action = self.top_department._action_pops_up_confirm()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"], {"type": "ir.actions.act_window_close"})

    def test_department_pull_creates_hierarchy_without_outbound_jobs(self):
        job = self.Job._queue_department_pull(self.wework, self.company)
        client = SimpleNamespace(department=SimpleNamespace(list=lambda: [
            {"id": 1, "name": "WeWork Root", "parentid": 0},
            {"id": 2, "name": "Sales", "parentid": 1},
            {"id": 3, "name": "North", "parentid": 2},
        ]))
        with patch.object(type(self.wework), "get_contact_client", return_value=client):
            job.action_process()

        sales = self.env["hr.department"].search([
            ("company_id", "=", self.company.id),
            ("wework_department_id", "=", 2),
        ])
        north = self.env["hr.department"].search([
            ("company_id", "=", self.company.id),
            ("wework_department_id", "=", 3),
        ])
        outbound_jobs = self.Job.search([
            ("job_type", "in", ["department", "user"]),
            ("wework_id", "=", self.wework.id),
        ])
        self.assertEqual(job.state, "done")
        self.assertEqual(sales.parent_id, self.top_department)
        self.assertEqual(north.parent_id, sales)
        self.assertFalse(outbound_jobs)

    def test_user_pull_does_not_enqueue_outbound_user_job(self):
        department = self.env["hr.department"].with_context(
            skip_wework_contact_sync=True,
        ).create({
            "company_id": self.company.id,
            "name": "Engineering",
            "parent_id": self.top_department.id,
            "wework_department_id": 10,
        })
        job = self._queue_user_pull_job(department)
        client = SimpleNamespace(user=SimpleNamespace(list=lambda *args, **kwargs: [{
            "department": [10],
            "gender": "1",
            "name": "Job Sync User",
            "status": 1,
            "userid": "job-sync-user",
        }]))
        with patch.object(type(self.wework), "get_contact_client", return_value=client):
            job.action_process()

        employee = self.env["hr.employee"].search([
            ("company_id", "=", self.company.id),
            ("wework_user_id", "=", "job-sync-user"),
        ])
        outbound_jobs = self.Job.search([
            ("job_type", "in", ["department", "user"]),
            ("binding_id", "!=", False),
            ("wework_id", "=", self.wework.id),
        ])
        self.assertEqual(job.state, "done")
        self.assertTrue(employee)
        self.assertTrue(employee.user_id)
        self.assertFalse(outbound_jobs)
