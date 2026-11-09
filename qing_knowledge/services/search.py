"""Deterministic Chinese and SKU terms for the rebuildable search projection."""

import re

import jieba

from .markdown import normalize_markdown

_TERM = re.compile(r'[\u3400-\u9fff]+|[a-zA-Z0-9]+')
_SKU = re.compile(r'(?<![a-zA-Z0-9])[a-zA-Z0-9]+(?:[-_./][a-zA-Z0-9]+)+(?![a-zA-Z0-9])')


def search_terms(source):
    """Keep Chinese search-mode words and a separator-free exact SKU key."""
    source = (source or '')[:120_000]
    words = []
    for item in jieba.cut_for_search(source):
        words.extend(match.group().lower() for match in _TERM.finditer(item))
    for match in _SKU.finditer(source):
        words.append(re.sub(r'[^a-zA-Z0-9]', '', match.group()).lower())
    return [word for word in words if word][:20_000]


def projection(title, summary, markdown, tags=()):
    # Markdown is source of truth; the projection may be dropped and rebuilt.
    source = normalize_markdown(markdown)
    return ' '.join(search_terms((title + ' ') * 3 + (' '.join(tags) + ' ') * 2 + (summary or '') + ' ' + source))


def query_expression(query):
    terms = list(dict.fromkeys(search_terms(query)))[:20]
    return ' | '.join(terms)
