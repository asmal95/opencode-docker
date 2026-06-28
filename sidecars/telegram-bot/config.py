#!/usr/bin/env python3
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENCODE_API_URL: str = "http://opencode:4096"
    OPENCODE_SERVER_PASSWORD: str = "opencode"
    ALLOWED_CHAT_IDS: str = ""
    PROJECT_DIR: str = ""

    @property
    def allowed_chat_ids_set(self) -> set:
        if not self.ALLOWED_CHAT_IDS:
            return set()
        return set(int(cid.strip()) for cid in self.ALLOWED_CHAT_IDS.split(",") if cid.strip())

    class Config:
        env_file = ".env"

settings = Settings()
