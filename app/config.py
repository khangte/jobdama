from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: 빈 환경변수(export NOTION_TOKEN=)가 .env 값을 가리지 않게
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    notion_token: str = ""
    job_data_source_id: str = ""  # 관심 기업 리스트 DB는 이 DB의 기업 relation에서 찾는다
    # 값을 넣을 열은 DB 스키마를 읽어 정한다(app/notion/schema.py의 JOB_ROLES)

    use_playwright: bool = True
    http_timeout_sec: float = 15.0
    max_jobs_shown: int = 50
    collect_result_path: str = "data/last_collect.json"  # 자동 수집 마지막 결과
    collect_config_path: str = "data/collect_settings.json"  # 자동 수집 조건


@lru_cache
def get_settings() -> Settings:
    return Settings()

