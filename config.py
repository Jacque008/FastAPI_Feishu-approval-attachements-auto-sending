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

    # Dropbox
    dropbox_refresh_token: str
    dropbox_app_key: str
    dropbox_app_secret: str

    # Resend Email
    resend_api_key: str
    resend_from_email: str = "onboarding@resend.dev"

    # Target email addresses for different approval types (Fortnox)
    email_expense: str = ""       # 费用报销 - SHiC
    email_payment_shic: str = ""  # 对公支付申请 - SHiC(298)

    # Auto-decrypt encrypted values (starting with "ENC:")
    @field_validator("feishu_app_secret", mode="before")
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
