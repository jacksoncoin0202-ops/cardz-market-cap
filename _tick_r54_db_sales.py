import os, json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pymysql

conn = pymysql.connect(
    host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
    user=os.environ.get("CARDZ_DB_USER", "cardz"),
    password=os.environ.get("CARDZ_DB_PASSWORD", ""),
    database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)
sales_vids = [653, 681, 693, 772, 784, 1034, 1455]
ph = ",".join(["%s"] * len(sales_vids))
with conn.cursor() as cur:
    # password empty? try
    cur.execute(f"""
      SELECT variant_id, source_code, external_entity_id, match_status, confidence
      FROM catalog_source_identity
      WHERE variant_id IN ({ph})
      ORDER BY variant_id, source_code
    """, sales_vids)
    print("=== identities ===")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute(f"""
      SELECT variant_id, source_code, COUNT(*) n, MAX(sold_at) maxs, MIN(sold_at) mins
      FROM market_sale_observation
      WHERE variant_id IN ({ph})
      GROUP BY variant_id, source_code
      ORDER BY variant_id, source_code
    """, sales_vids)
    print("\n=== all sales by source ===")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute(f"""
      SELECT variant_id, source_code, COUNT(*) n, MAX(sold_at) maxs
      FROM market_sale_observation
      WHERE variant_id IN ({ph}) AND sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 45 DAY)
      GROUP BY variant_id, source_code
    """, sales_vids)
    print("\n=== sales last 45d ===")
    for r in cur.fetchall():
        print(dict(r))

    # grade filters
    cur.execute(f"""
      SELECT variant_id, source_code, grade_company, grade_value, COUNT(*) n, MAX(sold_at) maxs
      FROM market_sale_observation
      WHERE variant_id IN ({ph})
      GROUP BY variant_id, source_code, grade_company, grade_value
      ORDER BY variant_id, n DESC
    """, sales_vids)
    print("\n=== sales by grade ===")
    for r in cur.fetchall()[:80]:
        print(dict(r))
