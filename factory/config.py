"""Runtime configuration. All secrets come from the environment (or a chmod-600 .env file);
nothing secret is ever written to the database, logs or source."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FACTORY_DATA_DIR", ROOT / "data")))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    # Spending limits (USD). Hard stops: the factory refuses paid operations beyond these.
    daily_spend_limit_usd: float = field(default_factory=lambda: _f("DAILY_SPEND_LIMIT_USD", 5.0))
    monthly_spend_limit_usd: float = field(default_factory=lambda: _f("MONTHLY_SPEND_LIMIT_USD", 60.0))
    # Crayo: clips allowed before the operator confirms the first test batch worked.
    crayo_test_clip_cap: int = field(default_factory=lambda: int(_f("CRAYO_TEST_CLIP_CAP", 2)))
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", "base"))
    telegram_owner_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_OWNER_ID", ""))
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", ""))
    dashboard_token: str = field(default_factory=lambda: os.environ.get("DASHBOARD_TOKEN", ""))
    user_agent: str = "ClippingFactory/0.1 (+campaign-discovery)"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'factory.db'}"


settings = Settings()


def secret(name: str) -> str | None:
    """Read a secret from the environment. Never log the return value."""
    v = os.environ.get(name)
    return v or None


def secret_present(name: str) -> bool:
    return bool(os.environ.get(name))
