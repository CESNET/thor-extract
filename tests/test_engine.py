"""Behaviour of the local patches in `src/extractor` (no network, no dataset)."""

import pandas as pd
import pytest
from bs4 import BeautifulSoup
from extractor.compat import CompatibilityTransformation
from extractor.transformations.html import HTMLTransformation

HTML_WITH_CSS = (
    '<html><head><link rel="stylesheet" href="http://cdn.example/x.css">'
    '<link rel="shortcut icon" href="/favicon.ico"></head>'
    '<body><a href="#">top</a><input type="password" name="p"></body></html>'
)


def _frame(html=HTML_WITH_CSS):
    return pd.DataFrame({"html": [html], "domain_name": ["example.com"]})


def _extract(selectors, html=HTML_WITH_CSS):
    return HTMLTransformation({"html_selectors": selectors}).transform(_frame(html))


def test_upstream_selectors_reproduce_the_always_empty_features():
    # Upstream passes 'rel="stylesheet"' as an attribute *name*, so nothing matches.
    df = _extract("upstream")
    assert df["html_num_of_css_external"][0] == 0
    assert df["html_num_of_css_internal"][0] == 0
    assert df["html_num_of_icon"][0] == 0
    assert df["html_num_of_input_password"][0] == 0
    assert df["html_num_of_anchors_to_hash"][0] == 0


def test_fixed_selectors_find_the_elements():
    df = _extract("fixed")
    assert df["html_num_of_css_external"][0] == 1     # http:// → external
    assert df["html_num_of_css_internal"][0] == 0     # /favicon.ico is not a stylesheet
    assert df["html_num_of_icon"][0] == 1
    assert df["html_num_of_input_password"][0] == 1
    assert df["html_num_of_anchors_to_hash"][0] == 1


def test_unknown_selector_mode_is_rejected():
    with pytest.raises(ValueError, match="html_selectors"):
        HTMLTransformation({"html_selectors": "guess"})


def test_single_pass_tag_index_counts_like_upstream():
    # The patched index replaces find_all(name) per tag name; per-name counts must not
    # move. Note html_num_of_tags is len(distinct tag names), not the number of tags.
    html = "<div><b>1</b><i>2</i><b>3</b><p><a href='/x'>4</a></p></div>"
    soup = BeautifulSoup(html, "lxml")
    df = _extract("upstream", html)
    assert df["html_num_of_tags"][0] == len({tag.name for tag in soup.find_all()})
    assert df["html_num_of_anchors"][0] == len(soup.find_all("a"))


def test_dnskey_source_picks_the_block():
    compat = CompatibilityTransformation.__new__(CompatibilityTransformation)
    data = {"zone": {"hasDNSKEY": True}}

    compat.dnskey_source = "dnsdata"   # upstream: reads dnsResult.dnsData, never set
    assert compat._has_dnskey(data, {}) is False

    compat.dnskey_source = "zone"      # local patch: the block the collector writes
    assert compat._has_dnskey(data, {}) is True
