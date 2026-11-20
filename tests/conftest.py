import os
import tempfile

# Isolated data dir + SQLite DB for the whole test session (must be set before importing factory).
_TMP = tempfile.mkdtemp(prefix="factory-test-")
os.environ["FACTORY_DATA_DIR"] = _TMP
os.environ.pop("DATABASE_URL", None)
os.environ["TELEGRAM_OWNER_ID"] = "4242"
for k in ("TELEGRAM_BOT_TOKEN", "CRAYO_API_KEY", "WHOP_USER_TOKEN", "DASHBOARD_TOKEN"):
    os.environ.pop(k, None)

import pytest  # noqa: E402

from factory.models import Base, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def s():
    from factory.ops import session
    with session() as sess:
        yield sess
