import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    db_path: str = "data/boxes.db"    # a file path (SQLite) or a postgres:// URL (Neon). DATABASE_URL wins.
    catalog_path: str = "data/catalog.csv"
    llm_mode: str = "auto"            # auto | deepseek | mock   (auto = deepseek when a key is set)
    deepseek_key: str = ""
    deepseek_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    default_count: int = 21           # Box A1 = rows 4-24 = 21 objects
    max_qty: int = 3                  # hard cap on quantity per object
    draft_ttl_hours: int = 12         # abandoned drafts release their objects after this long
    max_parallel_llm: int = 1         # boxes picking at once; 1 = no wasted LLM calls on clashing picks
    access_code: str = ""             # optional shared passcode for the whole app

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
            max_qty=_int("MAX_QTY", cls.max_qty),
            draft_ttl_hours=_int("DRAFT_TTL_HOURS", cls.draft_ttl_hours),
            max_parallel_llm=_int("MAX_PARALLEL_LLM", cls.max_parallel_llm),
            access_code=os.getenv("ACCESS_CODE", ""),
        )
