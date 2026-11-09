"""Regression tests for the §87/§124 constellation-headline detector (lane X4b).

The company/person analytics template renders at most 8 family headlines and
keeps the remaining families fully reachable inside the "Vis flere
konstellations-familier" disclosure (UX consolidation R1, §33 — nothing is
lost). The old detector used `html.count("constellation-family")`, so on build
15 /companies/9667 the 6 folded cards inflated the count to 14 and the
headline cap check failed even though exactly 8 headlines were rendered.
"""
from acceptance.frontend_quality import constellation_family_counts


def _card():
    return ('<li class="actor constellation-card constellation-family">'
            '<h4 class="constellation-title">Familie</h4></li>')


def test_folded_families_do_not_count_as_headlines():
    html = ('<ul class="actors">' + _card() * 8 + '</ul>'
            '<details class="analytics-more"><summary>Vis flere</summary>'
            '<ul class="actors">' + _card() * 6 + '</ul></details>')
    assert constellation_family_counts(html) == (8, 6)


def test_nine_top_level_headlines_still_violate_the_cap():
    html = '<ul class="actors">' + _card() * 9 + '</ul>'
    assert constellation_family_counts(html) == (9, 0)


def test_only_exact_family_elements_are_counted():
    html = ('<div class="constellation-family-note">note</div>'
            '<li class="constellation-family">substring, not a card</li>'
            + _card())
    assert constellation_family_counts(html) == (1, 0)
