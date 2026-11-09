"""Bounded retrieval that respects the current user's live knowledge rights."""

from odoo import models


class QingAiConversation(models.Model):
    _inherit = 'qing.ai.conversation'

    def _knowledge_payload(self, text):
        self.ensure_one()
        if not self.agent_id:
            return [], []
        self.agent_id.require_available()
        selected_space_ids = self.agent_id.sudo().knowledge_space_ids.ids
        if not selected_space_ids:
            return [], []
        domain = [
            ('id', 'in', selected_space_ids),
            ('accessible', '=', True),
        ]
        spaces = self.env['qing.knowledge.space'].search(domain, limit=5)
        Article = self.env['qing.knowledge.article']
        snippets = []
        references = []
        # The search and get_article methods both enforce current access and publication.
        for space in spaces[:5]:
            for result in Article.search_articles(text[:100], space_uuid=space.uuid, limit=3):
                if any(item['uuid'] == result['uuid'] for item in references):
                    continue
                article = Article.browse(result['id']).get_article()
                if not article.get('published'):
                    continue
                number = len(references) + 1
                references.append({
                    'number': number, 'title': article['title'],
                    'uuid': article['uuid'], 'revision_uuid': article['revision_uuid'],
                })
                snippets.append({
                    'number': number, 'title': article['title'],
                    'revision_uuid': article['revision_uuid'],
                    'excerpt': article['markdown'][:2000],
                })
                if len(snippets) >= 3:
                    return snippets, references
        return snippets, references
