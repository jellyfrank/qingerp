#!/usr/bin/python3
# @Time    : 2022-01-24
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _


ARTICLE_TYPES = [
    ('news', 'External Article'),
    ('mpnews', "Inside Article")
]


class wework_article(models.Model):

    _name = "wework.article"
    _description = "WeWork Article"

    type = fields.Selection(ARTICLE_TYPES, string="TYPE", default='news')
    appid = fields.Char("MiniProgram AppId")
    author = fields.Char("Author")
    content_source_url = fields.Char("Soure URL")
    content = fields.Html("Content")
    digest = fields.Text("Description", required=True)
    name = fields.Char("Title")
    url = fields.Char("URL")
    pic_url = fields.Char("Picture URL")
    page_path = fields.Char("Page Path of MiniProgram")
    thumb_media_id = fields.Many2one("wework.media", string="Thumb Meida Id")

    def to_json(self):
        """to dict"""
        if self.type == "news":
            data = {
                "title": self.name,
                "description": self.digest
            }
            if self.url:
                data['url'] = self.url
            if self.pic_url:
                data['picurl'] = self.pic_url
            if self.appid:
                data['appid'] = self.appid
            if self.page_path:
                data['pagepath'] = self.page_path
        else:
            data = {
                "title": self.name,
                "thumb_media_id": self.thumb_media_id.file if self.thumb_media_id else None,
                "author": self.author or None,
                "content_source_url": self.content_source_url or None,
                "content": self.content or None,
                "digest": self.digest,
            }
        return data
