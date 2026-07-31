import os, json
from pathlib import Path
import pymysql
conn = pymysql.connect(host=os.environ["CARDZ_DB_HOST"], port=int(os.environ["CARDZ_DB_PORT"]),
    user=os.environ["CARDZ_DB_USER"], password=os.environ["CARDZ_DB_PASSWORD"],
    database=os.environ["CARDZ_DB_NAME"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

# list tables related to g10/ebay/pc sold
with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    tables = [list(r.values())[0] for r in cur.fetchall()]
    for t in sorted(tables):
        if any(x in t.lower() for x in ["g10","ebay","sale","sold","pricechart","snk","kline","trade","cache"]):
            print(t)

print("---")
# pc ids for sales residual
pc = {
653: "5809563", 681: "4637112", 693: "4637096", 772: "7800269",
784: "11069056", 1034: "5809440", 1455: "5809387",
}
snk = {653:845619,681:486971,693:486955,772:484848,784:841947,1034:127875,1455:127775}

# check g10 related tables if any
for t in tables:
    if "g10" in t.lower() or "grade10" in t.lower():
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{t}`")
            cols = [r["Field"] for r in cur.fetchall()]
            print(t, cols[:15])
