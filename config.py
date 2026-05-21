from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Database
    database_url: str = "postgresql+asyncpg://twocare:twocare@localhost:5432/twocare"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Backend
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_url: str = "http://localhost:8000"

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_sip_trunk_id: str = ""

    # Voice providers
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
