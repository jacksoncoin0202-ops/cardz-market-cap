import os
from datetime import datetime, timedelta
import pymysql
conn = pymysql.connect(host=os.environ["CARDZ_DB_HOST"], port=int(os.environ["CARDZ_DB_PORT"]),
    user=os.environ["CARDZ_DB_USER"], password=os.environ["CARDZ_DB_PASSWORD"],
    database=os.environ["CARDZ_DB_NAME"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
vids = [653,681,693,772,784,1034,1455]
ph = ",".join(["%s"]*len(vids))
with conn.cursor() as cur:
    cur.execute(f"""
      SELECT variant_id, source_code, external_entity_id, COUNT(*) n, MAX(sold_at) maxs,
        SUM(CASE WHEN sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY) THEN 1 ELSE 0 END) n30,
        SUM(CASE WHEN sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY) AND grader_code='psa' AND grade_label='10' THEN 1 ELSE 0 END) n30_psa10
      FROM market_sale_observation
      WHERE variant_id IN ({ph})
      GROUP BY variant_id, source_code, external_entity_id
      ORDER BY variant_id, source_code
    """, vids)
    for r in cur.fetchall():
        print(dict(r))
