#!/usr/bin/env python3
"""Re-harvest failed GemRate sets after the unicode_escape fix, then rebuild combined files."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
spec = importlib.util.spec_from_file_location("gh", str(Path(__file__).parent / "gemrate_brute_harvest.py"))
gh = importlib.util.module_from_spec(spec); spec.loader.exec_module(gh)
from playwright.sync_api import sync_playwright

failed = [json.loads(l) for l in open(gh.DATA_DIR/"failed_sets.jsonl", encoding="utf-8")]
print(f"retrying {len(failed)} failed sets")
with sync_playwright() as p:
    b = p.chromium.launch(headless=False, executable_path=gh.CHROME_EXE,
        args=["--disable-blink-features=AutomationControlled"])
    page = b.new_context(user_agent=gh.USER_AGENT, viewport={"width":1920,"height":1080}).new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    sets_data = gh.extract_sets_data(page)
    by_id = {s["set_id"]: s for s in sets_data}
    still_failed = []
    for i, fs in enumerate(failed, 1):
        sid = fs["set_id"]; s = by_id.get(sid)
        if not s:
            print(f"[{i}] {sid[:12]} not in setsData"); still_failed.append(fs); continue
        try:
            cards = gh.extract_row_data(page, s["set_link"])
            for c in cards:
                c["_set_id"]=sid; c["_set_name"]=s.get("set_name"); c["_set_link"]=s.get("set_link")
            safe = re.sub(r"[^\w\-]+","_", s.get("set_name","unknown"))[:60]
            gh.save_jsonl(cards, gh.DATA_DIR/f"set_{sid}_{safe}.jsonl")
            print(f"[{i}/{len(failed)}] OK {s.get('set_name')} -> {len(cards)}")
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"[{i}/{len(failed)}] STILL FAIL {s.get('set_name')}: {e}"); still_failed.append(fs)
            page.wait_for_timeout(5000)
    b.close()

# rebuild combined from all set_*.jsonl
all_cards=[]
for f in sorted(gh.DATA_DIR.glob("set_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        all_cards.append(json.loads(line))
gh.save_jsonl(all_cards, gh.DATA_DIR/"all_cards.jsonl")
psa=[c for c in all_cards if int(c.get("psa_10") or 0)>=1000]
gh.save_jsonl(psa, gh.DATA_DIR/"psa10_1000_plus.jsonl")
gh.save_jsonl(still_failed, gh.DATA_DIR/"failed_sets.jsonl")
print(f"REBUILT total={len(all_cards)} psa10_1000={len(psa)} still_failed={len(still_failed)}")
