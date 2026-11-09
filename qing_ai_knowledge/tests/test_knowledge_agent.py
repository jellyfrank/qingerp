from uuid import uuid4

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiKnowledge(TransactionCase):
    def test_only_currently_accessible_published_revisions_are_retrieved(self):
        manager = new_test_user(self.env, login='qing-ai-kb-manager', groups='base.group_user')
        reader = new_test_user(self.env, login='qing-ai-kb-reader', groups='base.group_user')
        outsider = new_test_user(self.env, login='qing-ai-kb-outsider', groups='base.group_user')
        space = self.env['qing.knowledge.space'].with_user(manager).create({'name': 'AI SOP'})
        space.with_user(manager).set_member(reader.id, 'reader')
        created = space.with_user(manager).create_article('Receiving SOP')
        draft = self.env['qing.knowledge.draft'].sudo().search([('uuid', '=', created['draft_uuid'])])
        saved = draft.with_user(manager).save_draft(1, 'Receiving SOP', '', '# Receiving\n\nCheck SKU A-100.')
        submitted = draft.with_user(manager).submit_draft(saved['draft_version'])
        draft.with_user(manager).publish_draft(submitted['draft_version'], False,
                                               submitted['content_hash'], str(uuid4()))
        agent = self.env['qing.ai.agent'].create({
            'name': 'Warehouse SOP', 'knowledge_space_ids': [Command.link(space.id)],
        })
        conversation = self.env['qing.ai.conversation'].with_user(reader).create({'agent_id': agent.id})
        with self.assertRaises(AccessError):
            agent.with_user(reader).read(['knowledge_space_ids'])
        sources, refs = conversation._knowledge_payload('Receiving SOP')
        self.assertEqual(len(sources), 1)
        self.assertIn('A-100', sources[0]['excerpt'])
        self.assertEqual(refs[0]['title'], 'Receiving SOP')
        outsider_conversation = self.env['qing.ai.conversation'].with_user(outsider).create({'agent_id': agent.id})
        self.assertEqual(outsider_conversation._knowledge_payload('Receiving SOP'), ([], []))
        space.with_user(manager).set_member(reader.id, 'reader', active=False)
        self.assertEqual(conversation._knowledge_payload('Receiving SOP'), ([], []))
