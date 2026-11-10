"""수집 조건 설정 모델과 파일 저장·불러오기."""

import json

import pytest
from pydantic import ValidationError

from app.collection.collect_config import (
    CHOICES, CONFIG_PROBLEM, SITE_KEYS, CollectConfig, load_config, save_config,
)
from app.models import REGION_NAMES


def first_error(**kw):
    """잘못된 값으로 모델을 만들어 첫 오류를 돌려준다."""
    with pytest.raises(ValidationError) as e:
        CollectConfig(**kw)
    return e.value.errors()[0]


def test_defaults():
    c = CollectConfig()
    assert c.keyword == "python" and c.sites == list(SITE_KEYS) and c.max_per_site == 20
    assert c.job == "any" and c.region == "seoul_gyeonggi" and c.career == "upto2"
    assert c.education == c.employment == c.salary == "any" and c.sort == "latest"
    for words in (c.title_exclude_words, c.body_signal_words):
        assert words and all(w == w.lower() for w in words)
    assert {"ui", "qa"} <= set(c.title_exclude_words)


def test_choices_start_with_any():
    assert set(CHOICES) == {"job", "region", "career", "education", "employment", "salary", "sort"}
    for v in CHOICES.values():
        assert next(iter(v)) == "any" and v["any"] == "무관"
    assert set(CHOICES["region"]) - {"any"} == set(REGION_NAMES)


def test_keyword_rules():
    assert CollectConfig(keyword="  데이터 엔지니어 ").keyword == "데이터 엔지니어"
    assert CollectConfig(keyword="").keyword == ""
    e = first_error(keyword="a" * 31)
    assert e["type"] == "keyword" and e["msg"] == "검색어는 30자 이내로 입력하세요."
    e = first_error(keyword="😀")
    assert e["type"] == "keyword" and e["msg"] == "인크루트 검색에 쓸 수 없는 글자가 있어요."


@pytest.mark.parametrize("field, value", [("job", "python"), ("region", "평양"), ("sort", ""), ("career", None)])
def test_choice_rejects_unknown(field, value):
    e = first_error(**{field: value})
    assert e["type"] == field and e["msg"] == "목록에서 고르세요."


def test_lists_normalized():
    assert CollectConfig(title_exclude_words=[" Senior ", "senior", "", "C++"]).title_exclude_words == ["senior", "c++"]


def test_word_limits():
    word = "a" * 31
    e = first_error(body_signal_words=[word])
    assert e["type"] == "body_signal_words" and e["msg"] == "단어는 30자 이내로 입력하세요: " + word
    e = first_error(title_exclude_words=[f"w{i}" for i in range(101)])
    assert e["type"] == "title_exclude_words" and e["msg"] == "단어는 100개까지 넣을 수 있어요."


def test_sites_rules():
    e = first_error(sites=[])
    assert e["type"] == "sites" and e["msg"] == "사이트를 하나 이상 고르세요."
    e = first_error(sites=["nope"])
    assert e["type"] == "sites" and e["msg"] == "알 수 없는 사이트예요: nope"
    assert CollectConfig(sites=["jumpit", "saramin", "saramin"]).sites == ["saramin", "jumpit"]


@pytest.mark.parametrize("value", [0, 41, "abc", None, True, 2.5, "2.5", "2e1", ""])
def test_max_per_site_range(value):
    e = first_error(max_per_site=value)
    assert e["type"] == "max_per_site" and e["msg"] == "1~40 사이 숫자를 입력하세요."


def test_max_per_site_accepts_bounds():
    assert CollectConfig(max_per_site=40).max_per_site == 40 and CollectConfig(max_per_site="1").max_per_site == 1


@pytest.mark.parametrize(("field", "value"), [
    ("keyword", None), ("keyword", 5), ("sites", "saramin"), ("sites", None), ("sites", [1]),
    ("title_exclude_words", [1]), ("title_exclude_words", "abc"), ("body_signal_words", None),
])
def test_type_errors_use_field_name(field, value):
    assert first_error(**{field: value})["type"] == field


def test_load_non_utf8_returns_defaults_with_problem(tmp_path):
    path = tmp_path / "s.json"
    path.write_bytes("{\"keyword\": \"가\"}".encode("cp949"))
    assert load_config(path) == (CollectConfig(), CONFIG_PROBLEM)


def test_load_missing_returns_defaults(tmp_path):
    assert load_config(tmp_path / "x.json") == (CollectConfig(), None)


def test_load_corrupt_returns_defaults_with_problem(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{", encoding="utf-8")
    assert load_config(p) == (CollectConfig(), CONFIG_PROBLEM)


def test_load_invalid_values_returns_defaults(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"region": "mars"}), encoding="utf-8")
    assert load_config(p) == (CollectConfig(), CONFIG_PROBLEM)


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "data" / "x.json"
    cfg = CollectConfig(keyword="데이터 엔지니어", region="busan", sites=["jumpit"])
    save_config(p, cfg)
    assert load_config(p) == (cfg, None)
    assert not p.with_name("x.json.tmp").exists() and "데이터 엔지니어" in p.read_text(encoding="utf-8")
