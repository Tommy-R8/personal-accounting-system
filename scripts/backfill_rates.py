"""One-off: load full Blue-rate history from Bluelytics into fx_rates."""
import requests

from lib import db_conn

EVOLUTION_URL = "https://api.bluelytics.com.ar/v2/evolution.json"


def main():
    data = requests.get(EVOLUTION_URL, timeout=120).json()
    rows = [
        (d["date"], d["value_buy"], d["value_sell"], (d["value_buy"] + d["value_sell"]) / 2)
        for d in data
        if d["source"] == "Blue"
    ]
    with db_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """insert into fx_rates (rate_date, blue_buy, blue_sell, blue_avg)
               values (%s, %s, %s, %s)
               on conflict (rate_date) do update set
                 blue_buy = excluded.blue_buy,
                 blue_sell = excluded.blue_sell,
                 blue_avg = excluded.blue_avg""",
            rows,
        )
        conn.commit()
    print(f"{len(rows)} Blue rate days upserted")


if __name__ == "__main__":
    main()
