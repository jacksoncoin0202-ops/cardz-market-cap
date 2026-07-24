@echo off
REM Grade10 daily scraper — run via Windows Task Scheduler
REM Schedule: daily at 06:30 (after KAMI YouTube OS at 06:00)

cd /d "C:\Users\jackson0202\Documents\Playground\grade10-scraper"
set PY="C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
%PY% grade10_scraper.py --skip-images >> "data\_state\scraper.log" 2>&1
%PY% grade10_analytics.py >> "data\_state\scraper.log" 2>&1
%PY% grade10_kline.py >> "data\_state\scraper.log" 2>&1
