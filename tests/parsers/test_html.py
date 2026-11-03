"""HTML·JSON-LD 도우미."""

import pytest
from selectolax.lexbor import LexborHTMLParser

from app.parsers.html import dl_pairs, join_address


@pytest.mark.parametrize("region,locality,street,expected", [
    ("서울", "구로구", "디지털로26길 72", "서울 구로구 디지털로26길 72"),
    ("경기", "성남시", "분당구 대왕판교로645번길 16", "경기 성남시 분당구 대왕판교로645번길 16"),
    ("경기도", "성남시", "경기도 성남시 분당구 새나리로 25 (야탑동)", "경기도 성남시 분당구 새나리로 25 (야탑동)"),
    ("서울", "서초구", "서울 서초구 사임당로 32, 5층", "서울 서초구 사임당로 32, 5층"),
    ("서울특별시", "강남구", "서울 강남구 테헤란로 1", "서울 강남구 테헤란로 1"),
    ("경기", "성남시", None, "경기 성남시"),
    (None, None, "경기 성남시 분당구 판교로256번길 7", "경기 성남시 분당구 판교로256번길 7"),
])
def test_join_address(region, locality, street, expected):
    assert join_address(region, locality, street) == expected


def test_dl_pairs_drops_tooltip():
    tree = LexborHTMLParser('<dl><dt>근무형태</dt><dd>정규직<span class="toolTipWrap"><span class="toolTip">설명</span></span></dd></dl>')
    assert dl_pairs(tree) == {"근무형태": "정규직"}
