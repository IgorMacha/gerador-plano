@echo off
cd /d "%~dp0"
echo Instalando/verificando dependencias...
py -m pip install -q -r requirements.txt
echo Abrindo o sistema no navegador (feche esta janela para encerrar)...
py -m streamlit run app.py --server.headless false
pause
