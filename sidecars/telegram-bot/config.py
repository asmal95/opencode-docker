#!/usr/bin/env python3
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENCODE_API_URL: str = "http://opencode:4096"
    
    class Config:
        env_file = ".env"

settings = Settings()
