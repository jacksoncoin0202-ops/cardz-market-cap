import json, os
from pathlib import Path
from collections import Counter
import pymysql
rep = json.loads(Path("data/runtime/private-reports/canonical-db-qc/qc_20260730_fill_r53/report.json").read_text(encoding="utf-8"))
cards = rep["cards"]
board = [c for c in cards if c.get("marketRank") is not None and 1 <= int(c["marketRank"]) <= 300]
price_only = [c for c in board if set(c.get("blockers") or []) == {"exact_psa10_price_missing"}]
print("price_only", len(price_only))
# top ranks
for c in sorted(price_only, key=lambda x: x["marketRank"])[:15]:
    print(c["variantId"], "r", c["marketRank"], c.get("tcg"), (c.get("facts") or {}).get("identity",{}).get("collectorNumber"), (c.get("facts") or {}).get("identity",{}).get("set"))

# check sale residual post-write live 30d
conn = pymysql.connect(host=os.environ["CARDZ_DB_HOST"], port=int(os.environ["CARDZ_DB_PORT"]),
    user=os.environ["CARDZ_DB_USER"], password=os.environ["CARDZ_DB_PASSWORD"],
    database=os.environ["CARDZ_DB_NAME"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
vids=[653,681,693,772,784]
with conn.cursor() as cur:
    cur.execute("""
      SELECT variant_id, COUNT(*) n FROM market_sale_observation
      WHERE variant_id IN (%s,%s,%s,%s,%s)
        AND source_code='ebay' AND grader_code='psa' AND grade_label='10'
        AND sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
      GROUP BY variant_id ORDER BY variant_id
    """ % tuple(vids))
    print("live30", cur.fetchall())
