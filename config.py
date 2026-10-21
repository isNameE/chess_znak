from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")
    database_url: str = "postgresql://arm_demo@127.0.0.1:55432/arm_demo"
    arm_host: str = "127.0.0.1"
    arm_port: int = 8765
    true_api_enabled: bool = False
    true_api_token: str = ""
    true_api_inn: str = ""
    true_api_timeout: float = 15
    true_api_document_type: str = ""
    true_api_product_group: str = ""
    true_api_profile_reviewed: bool = False
    arm_access_user: str = ""
    arm_access_password: str = ""
    arm_demo_mode: bool = True
    true_api_document_path: str = "/api/v3/lk/documents/create"
    true_api_status_path: str = "/api/v4/true-api/doc/{document_id}/info"


@lru_cache
def settings():
    return Settings()
