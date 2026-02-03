from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache
from utils.crypto_utils import decrypt


class Settings(BaseSettings):
    # Feishu App
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verification_token: str
    feishu_signing_secret: str = ""

    # SMTP
    smtp_host: str
    smtp_port: int = 465
    smtp_user: str
    smtp_password: str
    smtp_from_email: str

    # Target email addresses for different approval types
    # Key format: EMAIL_审批名称 (spaces replaced with underscores)
    email_费用报销test: str = ""
    email_付款test: str = ""
    email_费用报销: str = ""
    email_付款_瑞典对公_shic: str = ""

    # Auto-decrypt encrypted values (starting with "ENC:")
    @field_validator("feishu_app_secret", "smtp_password", mode="before")
    @classmethod
    def decrypt_secret(cls, v):
        if isinstance(v, str) and v.startswith("ENC:"):
            return decrypt(v)
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
