"""Pure metadata parser contracts, never simulated transport or research evidence."""
from pathlib import Path
import hashlib
from typing import Any

import pytest

from app.harness.tools.search import _parse_arxiv
from app.harness.tools.search.arxiv_html import fallback_urls, parse_abstract, parse_search, verify_cached


ABSTRACT = '''<html><head>
<meta property="og:url" content="https://arxiv.org/abs/1412.6553v2">
<meta name="citation_arxiv_id" content="1412.6553">
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/1412.6553v2">
<meta name="citation_title" content="Authored parser title">
<meta name="citation_abstract" content="Authored parser abstract.">
<meta name="citation_author" content="Parser Author">
<meta name="citation_date" content="2014/12/19">
</head></html>'''
SEARCH = '''<html><h1>Showing 1–1 of 1 results for all: parser</h1>
<li class="arxiv-result"><p class="list-title"><a href="https://arxiv.org/abs/1412.6553">arXiv</a>
<span><a href="https://arxiv.org/pdf/1412.6553">pdf</a></span></p>
<p class="title"><span>CP</span>-<span>Decomposition</span> parser title</p>
<p class="authors"><a>Parser Author</a></p>
<span class="abstract-full">Full abstract <span>with markup</span>.<a class="is-size-7">Less</a></span>
<p class="is-size-7">Submitted 24 April, 2015; v1 submitted 19 December, 2014;</p></li></html>'''


def test_search_preserves_inline_text_original_date_and_links() -> None:
    hit = parse_search(SEARCH)[0]
    assert hit['title'] == 'CP-Decomposition parser title'
    assert hit['summary'] == 'Full abstract with markup.'
    assert hit['published'] == '2014-12-19'
    assert hit['authors'] == ['Parser Author']
    assert hit['pdf_url'] == 'https://arxiv.org/pdf/1412.6553'


@pytest.mark.parametrize('text', ['<html>gateway error</html>', SEARCH[:-7], SEARCH.replace('arxiv.org/abs/', 'evil.example/abs/'), SEARCH.replace('/pdf/1412.6553', '/pdf/1412.6554')])
def test_search_rejects_failure_pages_and_mismatched_identity(text: str) -> None:
    with pytest.raises(ValueError):
        parse_search(text)


def test_exact_lookup_preserves_requested_version() -> None:
    hit = parse_abstract(ABSTRACT, '1412.6553v2')
    assert hit['id'] == '1412.6553v2'
    assert parse_abstract(ABSTRACT, '1412.6553') == hit
    for wrong in ['1412.6553v1', '1812.03655']:
        with pytest.raises(ValueError):
            parse_abstract(ABSTRACT, wrong)
    with pytest.raises(ValueError):
        parse_abstract(ABSTRACT.replace('1412.6553v2', '1412.6553'), '1412.6553v2')


@pytest.mark.parametrize('args,query', [({'categories': ['cs.LG']}, 'tensor'), ({'date_from': '2025-01-01'}, 'tensor'), ({'sort_by': 'submittedDate'}, 'tensor'), ({}, 'ti:tensor'), ({}, 'tensor AND convolution')])
def test_fallback_does_not_silently_drop_api_semantics(args: dict[str, Any], query: str) -> None:
    with pytest.raises(ValueError, match='filters were not relaxed'):
        fallback_urls(args, None, query)


def test_html_cache_reconstructs_metadata_and_rejects_tampering(tmp_path: Path) -> None:
    path = tmp_path / 'response.html'
    path.write_text(ABSTRACT)
    urls = fallback_urls({}, ['1412.6553v2'], '')
    payload: dict[str, Any] = {'metadata_responses': [{'path': str(path), 'url': urls[0], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}],
               'hits': [parse_abstract(ABSTRACT, '1412.6553v2')]}
    verify_cached(payload, ['1412.6553v2'], 5, urls)
    payload['hits'][0]['title'] = 'tampered'
    with pytest.raises(ValueError, match='differs'):
        verify_cached(payload, ['1412.6553v2'], 5, urls)
    path.write_text('changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_cached(payload, ['1412.6553v2'], 5, urls)


def test_atom_errors_and_html_are_not_empty_success() -> None:
    for text in ['<html/>', '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><summary>bad query</summary></entry></feed>']:
        for date_from in ['', '2026-01-01']:
            with pytest.raises(ValueError):
                _parse_arxiv(text, date_from=date_from)
