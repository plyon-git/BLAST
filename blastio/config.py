"""Configuration comes from the process environment, never browser state."""
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Settings:
    db_path: str = os.getenv('BLASTIO_DB', 'data/blastio.sqlite3')
    encryption_key: str = os.getenv('BLASTIO_ENCRYPTION_KEY', '')
    mode: str = os.getenv('BLASTIO_MODE', 'simulation')
    public_url: str = os.getenv('BLASTIO_PUBLIC_URL', 'http://127.0.0.1:8000').rstrip('/')
    business_name: str = os.getenv('BLASTIO_BUSINESS_NAME', '101XVC')
    purpose: str = 'seller_outreach'
    allow_production: bool = os.getenv('BLASTIO_ALLOW_PRODUCTION', 'false').lower() == 'true'
    cookie_secure: bool = os.getenv('BLASTIO_COOKIE_SECURE', 'false').lower() == 'true'
    segment_cost: float = float(os.getenv('BLASTIO_SEGMENT_COST', '0.015'))
    health_ttl: int = 900
    verification_ttl: int = 86400

settings = Settings()
def get_settings():
    return settings
