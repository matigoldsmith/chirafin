#!/bin/bash
cd "$HOME/Scripts Claude AI"
source venv/bin/activate
PROMPT_TOOLKIT_NO_CPR=1 python3 fraccional_menu.py
echo ""
read -p "Presiona Enter para cerrar..."
