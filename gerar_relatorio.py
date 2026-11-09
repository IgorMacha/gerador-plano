"""Gera o PDF de jornada pela linha de comando (checklists do app + rastreador).

Exemplo (PowerShell):
  $env:COBLI_API_KEY="sua-chave"
  py gerar_relatorio.py --inicio 2026-10-17 --fim 2026-10-26 --motorista "JOAO" --cliente "MINHA EMPRESA"
  py gerar_relatorio.py --inicio 2026-10-01 --fim 2026-10-31 --motorista todos
"""
import argparse
import os
import re
from datetime import date, timedelta

from jornada.calculo import Config, segmentos_do_motorista
from jornada.checklist import eventos_do_motorista, montar
from jornada.cobli_api import CobliClient
from jornada.geocode import Geocoder
from jornada.pdf import gerar_pdf


def _progresso(msg, f):
    print(f"\r[{int(f * 100):3d}%] {msg:<60}", end="", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.getenv("COBLI_API_KEY"))
    ap.add_argument("--inicio", required=True, type=date.fromisoformat)
    ap.add_argument("--fim", required=True, type=date.fromisoformat)
    ap.add_argument("--motorista", required=True, help="Nome (ou parte); 'todos' = um PDF por motorista")
    ap.add_argument("--cliente", default=os.getenv("COBLI_CLIENTE", ""))
    ap.add_argument("--saida", default="relatorios")
    ap.add_argument("--jornada-padrao-h", type=float, default=8.0)
    ap.add_argument("--enderecos", action="store_true", help="converte lat/lng em endereço (OpenStreetMap)")
    a = ap.parse_args()
    if not a.api_key:
        ap.error("informe --api-key ou a variável COBLI_API_KEY")

    api = CobliClient(a.api_key)
    motoristas = api.drivers()
    alvo = motoristas if a.motorista.lower() == "todos" else \
        [m for m in motoristas if a.motorista.lower() in m["name"].lower()]
    if not alvo:
        raise SystemExit(f"Motorista '{a.motorista}' não encontrado.")

    x, y = a.inicio - timedelta(days=1), a.fim + timedelta(days=1)
    paths = api.paths(x, y, progresso=_progresso)
    checks = api.checklists(x, y, progresso=_progresso)
    print()
    cfg = Config(jornada_padrao_h=a.jornada_padrao_h)
    geo = Geocoder() if a.enderecos else None
    os.makedirs(a.saida, exist_ok=True)
    for m in alvo:
        segs = segmentos_do_motorista(paths, m["id"])
        evs = eventos_do_motorista(checks, m["name"])
        js = [j for j in montar(evs, segs, m["name"], cfg) if a.inicio <= j.inicio.date() <= a.fim]
        if not js:
            continue
        nome = re.sub(r"[^A-Za-z0-9]+", "_", m["name"]).strip("_")
        arq = os.path.join(a.saida, f"jornada_{nome}_{a.inicio:%Y%m%d}_{a.fim:%Y%m%d}.pdf")
        gerar_pdf(arq, a.cliente, (a.inicio, a.fim), js, m["name"], geocoder=geo)
        print(f"OK  {arq}  ({len(js)} jornadas, {sum(j.fonte == 'checklist' for j in js)} com checklist)")


if __name__ == "__main__":
    main()
