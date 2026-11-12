# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import BinaryBytes


# Fake robot webhook; assembled so no literal key appears in source.
TEST_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?" + "key=" + "placeholder"


@tagged("post_install", "-at_install")
class TestWeWorkFileMessages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({"name": "WeWork File Recipient"})
        cls.partner = cls.env["res.partner"].create({"name": "WeWork File Record"})

    def test_message_template_builds_file_message(self):
        media = self.env["wework.media"].create({
            "file": "application-media-id",
            "media_type": "file",
            "name": "application.pdf",
        })
        template = self.env["wework.message.template"].create({
            "employee_ids": [self.employee.id],
            "media_id": media.id,
            "msg_type": "file",
            "name": "File Template",
        })

        message = template.build_message(self.partner)

        self.assertEqual(message.msg_type, "file")
        self.assertEqual(message.media_id, media)
        self.assertEqual(message.employee_ids, self.employee)

    def test_message_template_button_uses_the_current_company(self):
        media = self.env["wework.media"].create({
            "file": "button-media-id",
            "media_type": "file",
            "name": "button.pdf",
        })
        template = self.env["wework.message.template"].create({
            "employee_ids": [self.employee.id],
            "media_id": media.id,
            "msg_type": "file",
            "name": "Button Template",
        })

        message = template.build_message()

        self.assertEqual(message.company_id, self.env.company)

    def test_application_media_uploads_the_stored_file(self):
        media = self.env["wework.media"].create({
            "media_type": "file",
            "name": "application.pdf",
            "source_file": BinaryBytes(b"%PDF-test"),
            "source_file_name": "application.pdf",
        })
        client = SimpleNamespace(media=Mock())
        client.media.upload.return_value = {"errcode": 0, "media_id": "application-media-id"}
        company_model = type(self.env.company)

        with patch.object(
            company_model,
            "weworkclient",
            new_callable=PropertyMock,
            return_value=client,
        ):
            media.action_upload_to_wework()

        self.assertEqual(media.file, "application-media-id")
        client.media.upload.assert_called_once_with(
            "file",
            ("application.pdf", b"%PDF-test"),
        )

    def test_environment_uses_current_company_client(self):
        client = Mock()
        with patch.object(
            type(self.env.company), "weworkclient",
            new_callable=PropertyMock, return_value=client,
        ):
            self.assertIs(self.env.weworkclient, client)

    def test_text_template_exec_preserves_result(self):
        template = self.env["wework.message.template"].create({
            "name": "Text evaluation",
            "msg_type": "text",
            "content": "result = 'Hello ' + self.name",
            "employee_ids": [self.employee.id],
        })
        message = template.build_message(self.partner)
        self.assertEqual(message.content, "Hello " + self.partner.name)

    @patch("odoo.addons.qing_wework.models.message.wework_robot.requests.post")
    def test_robot_message_wraps_markdown_v2_content(self, mock_post):
        response = Mock()
        response.content = b'{"errcode": 0}'
        response.json.return_value = {"errcode": 0}
        mock_post.return_value = response
        robot = self.env["wework.robot"].create({
            "name": "Markdown V2 Robot",
            "webhook_url": TEST_WEBHOOK_URL,
        })
        message = self.env["wework.robot.message"].create({
            "content": "# Daily Report\n**Status**: complete",
            "name": "Markdown V2 Report",
            "robot_id": robot.id,
            "type": "markdown_v2",
        })

        message.send()

        self.assertEqual(message.state, "success")
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "msgtype": "markdown_v2",
                "markdown_v2": {"content": "# Daily Report\n**Status**: complete"},
            },
        )

    def test_robot_template_builds_markdown_v2_without_mentions(self):
        robot = self.env["wework.robot"].create({
            "name": "Markdown V2 Template Robot",
            "webhook_url": TEST_WEBHOOK_URL,
        })
        template = self.env["wework.robot.template"].create({
            "content_template": "| 状态 | 数量 |\n| --- | ---: |\n| 完成 | 1 |",
            "message_type": "markdown_v2",
            "model_id": self.env["ir.model"]._get("res.partner").id,
            "name": "Markdown V2 Template",
            "robot_id": robot.id,
        })

        values = template._prepare_robot_message_values(self.partner)

        self.assertEqual(values["type"], "markdown_v2")
        self.assertEqual(values["employee_ids"][0][2], [])

    @patch("odoo.addons.qing_wework.models.message.wework_robot.requests.post")
    def test_robot_template_uploads_and_sends_file(self, mock_post):
        upload_response = Mock()
        upload_response.json.return_value = {"errcode": 0, "media_id": "robot-media-id"}
        send_response = Mock()
        send_response.content = b'{"errcode": 0}'
        send_response.json.return_value = {"errcode": 0}
        mock_post.side_effect = [upload_response, send_response]
        robot = self.env["wework.robot"].create({
            "name": "File Robot",
            "webhook_url": TEST_WEBHOOK_URL,
        })
        template = self.env["wework.robot.template"].create({
            "file": BinaryBytes(b"%PDF-test"),
            "file_name": "report.pdf",
            "message_type": "file",
            "model_id": self.env["ir.model"]._get("res.partner").id,
            "name": "File Robot Template",
            "robot_id": robot.id,
        })

        message = template.send_for_records(self.partner)

        self.assertEqual(message.state, "success")
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(
            mock_post.call_args_list[0].args[0],
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key=placeholder&type=file",
        )
        self.assertEqual(
            mock_post.call_args_list[1].kwargs["json"],
            {"msgtype": "file", "file": {"media_id": "robot-media-id"}},
        )
