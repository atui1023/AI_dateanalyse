"""Create a local snapshot database for the configured MySQL database."""

from pathlib import Path
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from sqlalchemy import create_engine
from migrate_sqlite_to_mysql import backup_mysql, mysql_urls


def main() -> None:
    config = dotenv_values(ROOT / ".env")
    server_url, _, database = mysql_urls(config)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = backup_mysql(create_engine(server_url), database, stamp)
    if not name:
        raise SystemExit("No tables found in the configured database")
    print(f"MySQL backup database created: {name}")


if __name__ == "__main__":
    main()
