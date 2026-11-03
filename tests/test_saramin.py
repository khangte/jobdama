"""2026-11 실제 사람인 공고 HTML 샘플로 파서 검증."""

from datetime import date
from pathlib import Path

import pytest

from app.notion_store import build_children
from app.parsers import saramin

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def parse(name: str, rec_idx: str):
    return saramin.parse(load(name), f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={rec_idx}", rec_idx)


def test_live_posting_full_fields():
    p = parse("saramin_55434315.html", "55434315")
    assert p.title == "[(주)로브로스] Robotics Data Solution Engineer"
    assert p.company == "(주)로브로스"
    assert p.career_select == "신입/경력"
    assert p.deadline == date(2026, 12, 1)
    assert p.location and p.location.startswith("서울")
    assert p.employment_type and "정규직" in p.employment_type
    assert not p.expired
    assert p.body_found
    assert any("Python" in x for x in p.sections["자격 요건"])
    assert len(p.sections["우대 사항"]) == 7


def test_dispatch_posting_career_and_sections():
    p = parse("saramin_55440317.html", "55440317")
    assert p.title.startswith("[(주)맨파워그룹코리아] ")
    assert p.career_select == "경력무관"
    assert p.deadline == date(2026, 11, 28)
    assert set(p.sections) >= {"주요 업무", "자격 요건", "우대 사항"}


def test_expired_posting_title_dedup_and_multi_part():
    p = parse("saramin_54220010_expired.html", "54220010")
    assert p.title == "[(주)디비투이] 신입,경력 (웹개발자,DBA/DBE) 채용"
    assert p.expired
    assert p.career_select == "신입/경력"
    # 모집 부문이 두 개라 같은 자격 요건이 반복되지만 한 번만 남긴다
    assert len(p.sections["자격 요건"]) == len(set(p.sections["자격 요건"]))
    assert p.sections["전형 절차"][0].startswith("서류전형")


def test_subheadings_inside_preferred_section():
    p = parse("saramin_55100285_expired.html", "55100285")
    pref = p.sections["우대 사항"]
    assert "[실무 경험 및 기술 스택]" in pref
    assert not any(x.endswith("]") and not x.startswith("[") for x in pref)


def test_other_posting_is_rejected():
    with pytest.raises(saramin.PostingMismatch):
        saramin.parse(load("saramin_55434315.html"), "u", "11111111")


def test_block_page_without_title_fails():
    with pytest.raises(saramin.ParseError):
        saramin.parse("<html><head><title>접근 제한</title></head><body>잠시 후 다시</body></html>", "u", "1")


def test_real_image_only_posting():
    p = parse("saramin_54324540_image.html", "54324540")
    assert p.title == "[(주)오픈메이트] 데이터 분석 및 모델 개발 신입(인턴)"
    assert p.career_select == "신입"
    assert not p.body_found


def test_image_only_body_is_partial():
    html = load("saramin_55434315.html")
    start = html.index('<div class="user_content')
    end = html.index("</div>", start)
    html = html[:start] + '<div class="user_content jobsViewDetail_55434315"><img src="x.png">' + html[end:]
    p = saramin.parse(html, "u", "55434315")
    assert p.title and not p.body_found
    blocks = build_children(p)
    assert blocks[0]["type"] == "callout"


def test_build_children_has_template_sections():
    p = parse("saramin_55434315.html", "55434315")
    blocks = build_children(p)
    headings = [b["heading_2"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "heading_2"]
    assert headings == ["공고 요약", "주요 업무", "자격 요건", "우대 사항", "전형 절차", "원문 링크"]
    assert len(blocks) <= 100
    texts = [
        "".join(r["text"]["content"] for r in b["bulleted_list_item"]["rich_text"])
        for b in blocks if b["type"] == "bulleted_list_item"
    ]
    assert "전형 절차" not in p.sections and "공고에서 확인 불가" in texts
