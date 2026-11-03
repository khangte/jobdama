"""여러 테스트 파일이 함께 쓰는 fixture 읽기 도우미."""

from pathlib import Path

from app.collection.fetcher import decode_html
from app.parsers.sites import saramin

FIX = Path(__file__).parent / "fixtures"


def rd(name: str, enc: str | None = None) -> str:
    return decode_html((FIX / name).read_bytes(), enc)


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def parse_saramin(name: str, rec_idx: str):
    return saramin.parse(load(name), f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={rec_idx}", rec_idx)
