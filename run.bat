@echo off
cd /d %~dp0
python -c "import impacket, customtkinter" 2>nul || (
  echo Installing dependencies...
  python -m pip install -r requirements.txt
)
python stellavita_browser.py
pause
