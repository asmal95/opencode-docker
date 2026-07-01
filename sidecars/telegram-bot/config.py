#!/usr/bin/env python3
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict()

    TELEGRAM_BOT_TOKEN: str
    OPENCODE_API_URL: str = "http://opencode:4096"
    OPENCODE_SERVER_PASSWORD: str
    ALLOWED_CHAT_IDS: str = ""
    PROJECT_DIR: str = ""
    MCP_SERVER_PORT: int = 8765
    MCP_SERVER_TOKEN: str
    MCP_SERVER_DB: str = "/opt/bot/cron.db"

    @property
    def allowed_chat_ids_set(self) -> set:
        if not self.ALLOWED_CHAT_IDS:
            return set()
        return set(int(cid.strip()) for cid in self.ALLOWED_CHAT_IDS.split(",") if cid.strip())

settings = Settings()
