"""Apply supabase/migrations/*.sql in filename order, tracking in _migrations."""
import pathlib

from lib import db_conn

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"


def main():
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """create table if not exists _migrations (
                 filename text primary key,
                 applied_at timestamptz not null default now())"""
        )
        cur.execute("select filename from _migrations")
        applied = {row[0] for row in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("insert into _migrations (filename) values (%s)", (path.name,))
        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
