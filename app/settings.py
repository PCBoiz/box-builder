import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DEFAULT_STAGE_COUNTS = {"A1": 12, "B2": 21, "B3": 21}   # A1 is for the next morning (10 to 14 objects); B2 and B3 hold many


def parse_stage_counts(value: str | None) -> dict[str, int]:
    """'A1=12,B2=21' -> {'A1': 12, 'B2': 21}. Bad parts are ignored; sizes are kept between 1 and 60."""
    out = dict(DEFAULT_STAGE_COUNTS)
    for part in (value or "").split(","):
        name, _, num = part.partition("=")
        name = name.strip().upper()
        if name and num.strip().isdigit():
            out[name] = max(1, min(60, int(num)))
    return out


@dataclass
class Settings:
    db_path: str = "data/boxes.db"    # a file path (SQLite) or a postgres:// URL (Neon). DATABASE_URL wins.
    catalog_path: str = "data/catalog.csv"
    llm_mode: str = "auto"            # auto | deepseek | mock   (auto = deepseek when a key is set)
    deepseek_key: str = ""
    deepseek_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    default_count: int = 21           # a block in the sheet has 21 rows (4-24); used for stages without their own size
    stage_counts: dict = field(default_factory=lambda: dict(DEFAULT_STAGE_COUNTS))   # objects per stage when no count is given
    max_qty: int = 3                  # hard cap on quantity per object
    draft_ttl_hours: int = 12         # abandoned drafts release their objects after this long
    max_parallel_llm: int = 1         # boxes picking at once; 1 = no wasted LLM calls on clashing picks
    sectors: str = "Hanoi-GPI"        # only objects from these SM sectors are ever used (comma list; empty = all)
    sync_token: str = ""              # secret the userscript sends to push the live SM catalog (empty = sync disabled)
    sheet_token: str = ""             # secret the sheet's own script sends with the supporters' check (empty = that route is off). Not the sync token: it can only record checks
    sm_push_mode: str = "off"         # off | mock. 'mock' pushes ready boxes to a fake SM held in memory. There is no real mode.
    image_hosts: str = ""             # hosts an object picture may come from (comma list; empty = the default host set in images.py)
    image_ttl_minutes: int = 55       # how long a signed SM picture link is trusted when the link does not say (SM's last about an hour)
    admin_ids: str = ""               # company IDs of the admins (comma list). Empty = login is OFF and the app stays open as before
    box_done_days: int = 1            # an approved box is finished this many days after the day it was made (Vietnam days); 0 = never
    admin_code: str = ""              # optional second secret an admin types with the ID (empty = ID only)
    user_ids: str = ""                # company IDs of ordinary users who may sign in without being in the pasted list (comma list)
    user_password: str = ""           # optional password every ordinary user types with the ID (empty = ID only). Set in Render, never in the repo
    allowed_app_hosts: str = ""       # hosts besides this one that the tool's ?app= address may name (comma list); localhost and 127.0.0.1 are always allowed
    enable_docs: bool = False         # FastAPI's /docs, /redoc and /openapi.json: off unless ENABLE_DOCS=1 (they list every route to a stranger)

    @property
    def sector_list(self) -> tuple[str, ...]:
        return tuple(x.strip() for x in self.sectors.split(",") if x.strip())

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("DATABASE_URL") or os.getenv("DB_PATH", cls.db_path),
            catalog_path=os.getenv("CATALOG_PATH", cls.catalog_path),
            llm_mode=os.getenv("LLM_MODE", cls.llm_mode).lower(),
            deepseek_key=os.getenv("DEEPSEEK_API_KEY", ""),
            deepseek_base=os.getenv("DEEPSEEK_BASE_URL", cls.deepseek_base).rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", cls.deepseek_model),
            default_count=_int("DEFAULT_COUNT", cls.default_count),
            stage_counts=parse_stage_counts(os.getenv("STAGE_COUNTS")),
            max_qty=_int("MAX_QTY", cls.max_qty),
            draft_ttl_hours=_int("DRAFT_TTL_HOURS", cls.draft_ttl_hours),
            max_parallel_llm=_int("MAX_PARALLEL_LLM", cls.max_parallel_llm),
            sync_token=os.getenv("SYNC_TOKEN", ""),
            sheet_token=os.getenv("SHEET_TOKEN", "").strip(),
            sectors=os.getenv("SECTORS", cls.sectors),
            sm_push_mode="mock" if os.getenv("SM_PUSH_MODE", "").strip().lower() == "mock" else "off",
            image_hosts=os.getenv("IMAGE_HOSTS", ""),
            image_ttl_minutes=min(1440, max(1, _int("IMAGE_TTL_MINUTES", cls.image_ttl_minutes))),
            admin_ids=os.getenv("ADMIN_IDS", ""),
            box_done_days=min(30, max(0, _int("BOX_DONE_DAYS", cls.box_done_days))),
            admin_code=os.getenv("ADMIN_CODE", "").strip(),
            user_ids=os.getenv("USER_IDS", ""),
            user_password=os.getenv("USER_PASSWORD", "").strip(),
            allowed_app_hosts=os.getenv("ALLOWED_APP_HOSTS", ""),
            enable_docs=os.getenv("ENABLE_DOCS", "").strip() == "1",
        )
