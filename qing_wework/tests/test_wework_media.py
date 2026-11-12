# -*- coding: utf-8 -*-

import json
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWeWorkMedia(TransactionCase):

    def test_media_id_is_stored_as_a_character_field(self):
        field = self.env["wework.media"]._fields["file"]

        self.assertEqual(field.type, "char")

    def test_access_token_can_be_requested_without_a_media_record(self):
        method = type(self.env["wework.media"]).get_access_token

        self.assertTrue(method._api_model)

    def test_access_token_uses_the_current_company_client(self):
        company_model = type(self.env.company)
        client = SimpleNamespace(access_token="test-token")
        with patch.object(
            company_model,
            "weworkclient",
            new_callable=PropertyMock,
            return_value=client,
        ):
            response = self.env["wework.media"].get_access_token()

        payload = json.loads(response)
        self.assertEqual(payload["access_token"], "test-token")
