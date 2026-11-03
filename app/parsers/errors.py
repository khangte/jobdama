class ParseError(Exception):
    """공고 제목을 확인할 수 없음(차단 화면, 구조 변경, 공고가 아닌 페이지)."""


class PostingMismatch(ParseError):
    """요청한 공고와 다른 공고 페이지."""
