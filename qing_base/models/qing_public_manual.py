"""Local retrieval from the versioned public QingERP manual."""

import json
import re
import unicodedata
from functools import lru_cache

from markupsafe import Markup

from odoo import models
from odoo.tools import file_open, plaintext2html


WORDS = re.compile(r'[\u3400-\u9fff]+|[a-z0-9]+')
HAN = re.compile(r'[\u3400-\u9fff]')
ERP_CONTEXT = re.compile(
    r'青企|青云\s*ai|单据|审批|工作流|(?:销售|采购|出库|入库)订单|销售报价|报价单|'
    r'出入库|库存|库位|采购|供应商账单|客户资料|客户余额|会计|发票|对账|'
    r'\b(?:qing\s*erp|odoo|erp|sales order|purchase order|stock picking|invoice|'
    r'vendor bill|journal entry|record rule|inventory count)\b|'
    r'(?<![a-z0-9])(?:so|po|rfq|inv|bill|wh[/ -](?:in|out|int)|s|p)[/-]?[0-9]{3,}\b',
)
AMBIGUOUS_ENGLISH_ALIASES = frozenset({
    'bill', 'credit', 'customer', 'delivery', 'discount', 'location', 'lot',
    'note', 'order', 'receipt', 'return', 'sale', 'serial', 'transfer',
})
COMMON_TERMS = frozenset({
    '怎么', '如何', '什么', '哪里', '可以', '需要', '请问', '一下', '在哪', '设置',
    'how', 'what', 'where', 'can', 'the', 'when', 'with', 'you', 'and', 'qing erp', 'qingerp',
})


@lru_cache(maxsize=1)
def public_manual():
    with file_open('qing_base/data/guide_manual.json', 'r') as source:
        return json.load(source)


def _normalize(text):
    return unicodedata.normalize('NFKC', text).casefold()


def _terms(text):
    terms = set()
    for token in WORDS.findall(text):
        if HAN.match(token):
            terms.update(token[index:index + 2] for index in range(len(token) - 1))
        elif len(token) >= 2 and not token.isdigit():
            terms.add(token)
    return terms - COMMON_TERMS


@lru_cache(maxsize=1)
def _search_index():
    return [
        (
            section,
            _terms(_normalize(section['title'] + ' ' + section['chapter'])),
            [
                (alias, _terms(alias))
                for alias in map(_normalize, [*section['keywords'], section['topic'].replace('_', ' ')])
            ],
            [(question, _terms(_normalize(question['text']))) for question in section['questions']],
        )
        for section in public_manual()['sections']
    ]


class QingPublicManual(models.AbstractModel):
    _name = 'qing.public.manual'
    _description = 'QingERP Public Manual'

    def _installed_modules(self):
        modules = {section['module'] for section in public_manual()['sections']}
        domain = [('name', 'in', sorted(modules)), ('state', '=', 'installed')]
        return set(self.env['ir.module.module'].sudo().search_fetch(domain, ['name']).mapped('name'))

    def _version(self):
        version = self.env['ir.config_parameter'].sudo()._qing_product_metadata()['client_version']
        return '.'.join(version.split('.')[:2])

    def _looks_like_erp_question(self, text):
        return bool(ERP_CONTEXT.search(_normalize(text)))

    def _match_question(self, text):
        normalized = _normalize(text[:600])
        terms = _terms(normalized)
        if not terms or self._version() != public_manual()['version']:
            return None, None
        modules = self._installed_modules()
        best_score = 4
        matched = (None, None)
        for section, title, aliases, questions in _search_index():
            if section['module'] not in modules:
                continue
            alias_score = sum(
                1 if alias in AMBIGUOUS_ENGLISH_ALIASES else min(len(alias) + 3, 7)
                for alias, alias_terms in aliases
                if alias_terms and alias_terms <= terms and (not HAN.match(alias) or alias in normalized)
            )
            best_question, question_terms = max(
                questions, key=lambda item: len(terms & item[1]), default=(None, set()),
            )
            overlap = len(terms & question_terms)
            score = alias_score + 2 * len(terms & title) + 2 * overlap
            if score > best_score:
                best_score = score
                matched = (section, best_question if overlap >= 4 and overlap * 2 >= len(question_terms) else None)
        return matched

    def _format_reply(self, section, answer=None):
        if answer:
            content = Markup('<p>%s</p>') % Markup('<br/>').join(answer.splitlines())
        else:
            lines = []
            in_table = False
            for line in section['body'].splitlines():
                if line.startswith(':::'):
                    continue
                if line.startswith('|'):
                    cells = [cell.strip() for cell in line.strip('|').split('|')]
                    if in_table and any(cell.strip('-: ') for cell in cells):
                        lines.append(' · '.join(cells))
                    in_table = True
                    continue
                in_table = False
                lines.append(line.replace('**', ''))
            content = plaintext2html('\n'.join(lines))
        source = Markup('<p>%s <a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>') % (
            self.env._('Public manual [1]'),
            section['url'],
            section['chapter'],
        )
        return Markup(content) + source
