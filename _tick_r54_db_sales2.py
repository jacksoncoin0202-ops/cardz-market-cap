import os
import pymysql
conn = pymysql.connect(
    host=os.environ["CARDZ_DB_HOST"],
    port=int(os.environ["CARDZ_DB_PORT"]),
    user=os.environ["CARDZ_DB_USER"],
    password=os.environ["CARDZ_DB_PASSWORD"],
    database=os.environ["CARDZ_DB_NAME"],
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)
sales_vids = [653, 681, 693, 772, 784, 1034, 1455]
ph = ",".join(["%s"] * len(sales_vids))
with conn.cursor() as cur:
    cur.execute("SHOW COLUMNS FROM catalog_source_identity")
    print("identity cols", [r["Field"] for r in cur.fetchall()])
    cur.execute("SHOW COLUMNS FROM market_sale_observation")
    print("sale cols", [r["Field"] for r in cur.fetchall()])
    cur.execute(f"""
      SELECT variant_id, source_code, external_entity_id, match_status
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
    rows = cur.fetchall()
    if not rows:
        print("NONE")
    for r in rows:
        print(dict(r))
    # check grade columns existence
    cur.execute(f"""
      SELECT * FROM market_sale_observation
      WHERE variant_id IN ({ph})
      ORDER BY sold_at DESC LIMIT 5
    """, sales_vids)
    sample = cur.fetchall()
    print("\n=== sample sales ===")
    for r in sample:
        print({k:r[k] for k in list(r)[:20]})
