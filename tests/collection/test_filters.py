"""자동 수집 선별 규칙 검증 (네트워크 없음)."""

from datetime import date

from app.collection.collect_config import CollectConfig
from app.collection.filters import evaluate as _evaluate
from app.collection.filters import screen as _screen
from app.models import Posting

TODAY = date(2026, 11, 8)
CFG = CollectConfig()


def evaluate(p, today, cfg=CFG):
    """기본 설정으로 판정하는 단축 함수."""
    return _evaluate(p, today, cfg)


def screen(p, today, cfg=CFG):
    """기본 설정으로 선별하는 단축 함수."""
    return _screen(p, today, cfg)


def make(**kw) -> Posting:
    """기본값을 통과하는 공고를 만들고 kw로 덮어쓴다."""
    base = {"source": "saramin", "url": "u", "title": "[A] Python 백엔드", "raw_title": "Python 백엔드", "company": "A",
            "location": "서울 강남구", "career_select": "신입", "sections": {"주요 업무": ["Python API 개발"]}}
    return Posting(**{**base, **kw})


def test_passes_baseline(): assert evaluate(make(), TODAY) is None
def test_expired_by_date(): assert evaluate(make(deadline=date(2026, 11, 7)), TODAY) == "마감됨"
def test_expired_flag(): assert evaluate(make(expired=True), TODAY) == "마감됨"
def test_deadline_today_passes(): assert evaluate(make(deadline=TODAY), TODAY) is None
def test_location_missing(): assert evaluate(make(location=None), TODAY) == "근무지 확인 불가"
def test_location_outside(): assert evaluate(make(location="부산 해운대구"), TODAY) == "근무지 제외: 부산 해운대구"
def test_gyeonggi_passes(): assert evaluate(make(location="경기 성남시 분당구"), TODAY) is None
def test_location_district_only_passes(): assert evaluate(make(location="서초구 강남대로89길 30"), TODAY) is None
def test_location_multi_with_seoul_passes(): assert evaluate(make(location="서울 강남구, 부산 해운대구"), TODAY) is None
def test_three_years_excluded(): assert evaluate(make(career_select="3년 이상"), TODAY) == "경력 3년 이상"


def test_career_only_passes_and_stays_empty():
    p, reason = screen(make(career_select=None), TODAY)
    assert reason is None and p.career_select is None


def test_body_requirement_stricter():
    p, reason = screen(make(sections={"주요 업무": ["Python"], "자격 요건": ["백엔드 개발 경력 2년 이상"]}), TODAY)
    assert reason is None and p.career_select == "2년 이상"


def test_body_requirement_three_years():
    _, reason = screen(make(sections={"주요 업무": ["Python"], "자격 요건": ["경력 3년 이상"]}), TODAY)
    assert reason == "경력 3년 이상"


def test_body_requirement_range_and_arrow_forms():
    for line in ("경력 3~5년", "5년차 이상", "5년↑", "경력 3년~5년", "3년 - 5년", "3년차~5년차"):
        p, _ = screen(make(sections={"주요 업무": ["Python"], "자격 요건": [line]}), TODAY)
        assert p.career_select == "3년 이상", line


def test_body_requirement_ignores_non_years():
    for line in ("3-4명 팀 경험", "2020-2024년 프로젝트", "2020년-2024년 프로젝트"):
        p, _ = screen(make(sections={"주요 업무": ["Python"], "자격 요건": [line]}), TODAY)
        assert p.career_select == "신입", line


def test_preference_line_in_requirements_ignored():
    p, reason = screen(make(sections={"주요 업무": ["Python"], "자격 요건": ["Docker 3년 이상 사용 경험 우대"]}), TODAY)
    assert reason is None and p.career_select == "신입"


def test_preferred_section_ignored():
    _, reason = screen(make(sections={"주요 업무": ["Python"], "우대 사항": ["5년 이상"]}), TODAY)
    assert reason is None


def test_title_word(): assert evaluate(make(raw_title="시니어 Python 개발자"), TODAY) == "공고명 제외 단어: 시니어"
def test_title_word_case_insensitive(): assert evaluate(make(raw_title="Senior Backend"), TODAY) == "공고명 제외 단어: senior"


def test_title_word_not_inside_other_word():
    for raw in ("Linux 서버 개발자", "Data Analytics Engineer, Logistics 백엔드 개발자", "Build 엔지니어", "Kiosk 서버 개발",
                "데이터 인사이트 플랫폼 개발자", "스마트그리드 백엔드 개발자"):
        assert evaluate(make(raw_title=raw), TODAY) is None, raw


def test_title_word_ignores_company_name():
    assert evaluate(make(title="[유진로봇] Python 개발자", raw_title="Python 개발자"), TODAY) is None


def test_title_word_still_excluded():
    for raw, word in (("UX 디자이너", "ux"), ("QA 엔지니어", "qa"), ("Tech Lead", "lead"), ("Team Leader", "leader"), ("인사담당 Python", "인사담당")):
        assert evaluate(make(raw_title=raw), TODAY) == f"공고명 제외 단어: {word}", raw


def test_no_body_signal():
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["Java Spring"]}), TODAY) == "본문에 필수 신호 단어 없음"


def test_no_signal_check_when_body_missing():
    assert evaluate(make(title="[A] 백엔드 개발자", sections={}), TODAY) is None


def test_pipeline_signal():
    assert evaluate(make(title="[A] 데이터 엔지니어", sections={"주요 업무": ["데이터 파이프라인 구축"]}), TODAY) is None


def test_signal_word_boundary():
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["settlement 시스템"]}), TODAY) is not None


def test_signal_followed_by_korean_particle():
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["Python으로 API 개발"]}), TODAY) is None


def test_order_deadline_first(): assert evaluate(make(expired=True, location=None, career_select="3년 이상"), TODAY) == "마감됨"


def cfg(**kw) -> CollectConfig:
    """기본값에 kw를 덮어쓴 설정."""
    return CollectConfig(**kw)


def test_region_choice_changes_rule():
    c = cfg(region="busan")
    assert evaluate(make(location="부산 해운대구"), TODAY, c) is None
    assert evaluate(make(location="해운대구 우동"), TODAY, c) is None
    assert evaluate(make(location="서울 강남구"), TODAY, c) == "근무지 제외: 서울 강남구"


def test_region_alias():
    c = cfg(region="chungnam")
    assert evaluate(make(location="충청남도 천안시"), TODAY, c) is None
    assert evaluate(make(location="충청북도 청주시"), TODAY, c) == "근무지 제외: 충청북도 청주시"


def test_career_reason_uses_setting():
    assert evaluate(make(career_select="1년 이상"), TODAY, cfg(career="new")) == "경력 1년 이상"
    assert evaluate(make(career_select="2년 이상"), TODAY, cfg(career="upto1")) == "경력 2년 이상"
    assert evaluate(make(career_select="3년 이상"), TODAY, cfg(career="upto2")) == "경력 3년 이상"


def test_employment_post_filter():
    c = cfg(employment="fulltime")
    assert evaluate(make(employment_type="계약직"), TODAY, c) == "고용형태 제외: 계약직"
    assert evaluate(make(employment_type="정규직(수습 3개월)"), TODAY, c) is None
    assert evaluate(make(employment_type=None), TODAY, c) is None


def test_any_skips_post_filters():
    c = cfg(region="any", career="any", employment="fulltime")
    p = make(location=None, career_select="3년 이상", employment_type="정규직")
    assert evaluate(p, TODAY, c) is None
    assert evaluate(make(location=None, career_select="3년 이상", employment_type="계약직"), TODAY, cfg(region="any", career="any")) is None


def test_title_words_custom(): assert evaluate(make(raw_title="C++ 개발자"), TODAY, cfg(title_exclude_words=["c++"])) == "공고명 제외 단어: c++"


def test_body_words_escaped():
    c = cfg(body_signal_words=["node.js"])
    assert evaluate(make(sections={"주요 업무": ["Node.js 서버"]}), TODAY, c) is None
    assert evaluate(make(sections={"주요 업무": ["nodexjs"]}), TODAY, c) == "본문에 필수 신호 단어 없음"


def test_body_words_ascii_boundary():
    c = cfg(body_signal_words=["python"])
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["Python으로 개발"]}), TODAY, c) is None
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["pythonic"]}), TODAY, c) is not None


def test_body_words_space_flexible():
    c = cfg(body_signal_words=["데이터 파이프라인"])
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["데이터파이프라인 구축"]}), TODAY, c) is None


def test_body_words_empty_skips():
    assert evaluate(make(title="[A] 백엔드", sections={"주요 업무": ["Java"]}), TODAY, cfg(body_signal_words=[])) is None
