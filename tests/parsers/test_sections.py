"""본문 구간 발췌."""

from app.parsers.sections import extract_sections


def test_extract_sections_patterns():
    lines = [
        "회사 소개 문단",
        "[업무내용]",
        "- 파이프라인 구축",
        "📋 자격요건",
        "ㆍPython 기반 백엔드 개발",
        "역량",  # 줄바꿈된 이어지는 줄
        "📋 우대사항",
        "[실무 경험 및 기술 스택]",  # 구간 안 소제목은 종료가 아님
        "• Airflow 경험",
        "•",
        "Kafka 경험",
        "🏠 근무조건",
        "• 근무지 : 서울",
        "전형절차 : 서류 → 면접",
        "🛎️ 유의사항",
        "• 허위 기재 시 취소",
    ]
    s = extract_sections(lines)
    assert s["주요 업무"] == ["파이프라인 구축"]
    assert s["자격 요건"] == ["Python 기반 백엔드 개발 역량"]
    assert s["우대 사항"] == ["[실무 경험 및 기술 스택]", "Airflow 경험", "Kafka 경험"]
    assert s["전형 절차"] == ["서류 → 면접"]
