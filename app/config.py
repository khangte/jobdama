from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    notion_token: str = ""
    job_data_source_id: str = ""
    company_data_source_id: str = ""

    # 채용 지원 관리 DB 속성 이름
    prop_title: str = "공고명"
    prop_company: str = "기업명"
    prop_deadline: str = "마감일"
    prop_career: str = "경력"
    prop_url: str = "URL"
    prop_stage: str = "지원 단계"
    stage_default: str = "📌 시작 전"

    # 관심 기업 리스트 DB 속성 이름
    company_prop_title: str = "기업명"
    company_prop_status: str = "상태"
    company_status_default: str = "⚪️ 리서치 중"

    use_playwright: bool = True
    http_timeout_sec: float = 15.0
    max_jobs_shown: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
