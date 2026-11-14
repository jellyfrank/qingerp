#!/usr/bin/python3
# @Time    : 2022-01-17
# @Author  : Kevin Kong (kfx2007@163.com)

import base64
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import BinaryValue


TEMP_MEDIA_URL = "/wework_server/cgi-bin/media/upload"
IMAGE_URL = "/wework_server/cgi-bin/media/uploadimg"

TYPES = [
    ('temp', 'Temp Media'),
    ('image', "Image")
]

MEDIA_TYPES = [
    ('image', 'Image'),
    ('voice', 'Voice'),
    ('video', 'Video'),
    ('file', 'File'),
]


class wework_media(models.Model):

    _name = "wework.media"
    _description = "Wework Media"

    def _get_source_file_content(self):
        self.ensure_one()
        if not self.source_file:
            raise UserError(_("Please select a file before uploading it to WeCom."))
        encoded_content = self.source_file
        if isinstance(encoded_content, BinaryValue):
            return encoded_content.content
        if isinstance(encoded_content, str):
            encoded_content = encoded_content.encode()
        try:
            return base64.b64decode(encoded_content, validate=True)
        except ValueError as error:
            raise UserError(_("The selected file is not valid Base64 data.")) from error

    def _upload_to_wework(self):
        """Upload the stored source file through the configured WeCom app."""
        self.ensure_one()
        media_type = self.media_type or "file"
        source_content = self._get_source_file_content()
        source_name = self.source_file_name or self.name or "file"
        try:
            result = self.env.company.weworkclient.media.upload(
                media_type,
                (source_name, source_content),
            )
        except Exception as error:
            raise UserError(_("Unable to upload the file to WeCom: %s") % error) from error
        media_id = result.get("media_id")
        if result.get("errcode") or not media_id:
            raise UserError(_("Unable to upload the file to WeCom: %s") % result.get("errmsg"))
        self.write({
            "file": media_id,
            "uploaded_at": fields.Datetime.now(),
        })
        return media_id

    def _get_media_id_for_send(self):
        """Refresh temporary media whenever its original file is available."""
        self.ensure_one()
        if self.source_file:
            return self._upload_to_wework()
        if self.file:
            return self.file
        raise UserError(_("Please upload a WeCom media file before sending it."))

    @api.onchange("media_type")
    def _onchange_media_type(self):
        """generate media upload params"""
        self.wework_type = self.media_type
        params = {
            "access_token": self.env.company.weworkclient.access_token
        }
        if self.type == "temp":
            url = TEMP_MEDIA_URL
            params['type'] = self.wework_type
        else:
            url = IMAGE_URL

        appendix = "&".join(
            [f"{key}={value}" for key, value in params.items()])
        self.wework_url = f"{url}?{appendix}"
        # self.wework_url = url

    file = fields.Char("WeCom Media ID", readonly=True)
    media_type = fields.Selection(MEDIA_TYPES, string="Media Type")
    name = fields.Char("Name")
    source_file = fields.Binary("Source File", attachment=True)
    source_file_name = fields.Char("Source File Name")
    type = fields.Selection(TYPES, string="Type", default="temp")
    uploaded_at = fields.Datetime("Uploaded At", readonly=True)
    wework_access_token = fields.Char("Access Token")
    wework_type = fields.Char("TYPE")
    wework_url = fields.Char("URL")
    video_description = fields.Text("Video Description")
    video_title = fields.Char("Video Title")

    def action_upload_to_wework(self):
        for media in self:
            media._upload_to_wework()
        return self._qing_notify(_("The media file was uploaded to WeCom."), notification_type="success")

    @api.model
    def get_access_token(self):
        """get accesst token"""
        return json.dumps({
            "result": 0,
            "access_token": self.env.company.weworkclient.access_token
        })
