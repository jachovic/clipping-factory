"""Runtime configuration. All secrets come from the environment (or a private .env file);
nothing secret is ever written to the database, logs or source.

User-editable values are read from the environment on every access, so saving them from the
dashboard setup page takes effect immediately, without a restart."""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("FACTORY_ENV_FILE", ROOT / ".env"))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


_load_dotenv(ENV_FILE)


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


class Settings:
    """Every attribute is read live from the environment."""

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("FACTORY_DATA_DIR", ROOT / "data"))

    @property
    def database_url(self) -> str:
        return os.environ.get("DATABASE_URL", "")

    @property
    def daily_spend_limit_usd(self) -> float:
        return _f("DAILY_SPEND_LIMIT_USD", 5.0)

    @property
    def monthly_spend_limit_usd(self) -> float:
        return _f("MONTHLY_SPEND_LIMIT_USD", 60.0)

    @property
    def crayo_test_clip_cap(self) -> int:
        return int(_f("CRAYO_TEST_CLIP_CAP", 2))

    @property
    def whisper_model(self) -> str:
        return os.environ.get("WHISPER_MODEL", "base") or "base"

    @property
    def telegram_owner_id(self) -> str:
        return os.environ.get("TELEGRAM_OWNER_ID", "")

    @property
    def public_base_url(self) -> str:
        return os.environ.get("PUBLIC_BASE_URL", "")

    @property
    def dashboard_token(self) -> str:
        return os.environ.get("DASHBOARD_TOKEN", "")

    user_agent = "ClippingFactory/0.1 (+campaign-discovery)"

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


_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def update_env(values: dict[str, str], path: Path | None = None) -> list[str]:
    """Write/replace KEY=value lines in .env (keeping comments and other keys), restrict the file to the
    current user, and apply the values to this process immediately. Empty values are ignored
    (an empty password field means "keep the current value"). Returns the keys that changed."""
    path = path or ENV_FILE
    changed = {}
    for k, v in values.items():
        v = (v or "").strip()
        if not v or not _KEY.match(k) or "\n" in v or "\r" in v:
            continue
        changed[k] = v
    if not changed:
        return []
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in changed:
            lines[i] = f"{m.group(1)}={changed[m.group(1)]}"
            done.add(m.group(1))
    lines += [f"{k}={v}" for k, v in changed.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows uses the installer's ACL instead
        pass
    os.environ.update(changed)
    return sorted(changed)
