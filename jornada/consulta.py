"""Consulta: o motorista tem rota/atividade em cada dia?

Cruza três fontes por dia:
  - Rota planejada  (Serviços e Entregas: /public/v2/routes) — pelo motorista ou pelo veículo que ele usou
  - Rastreador      (trajetos com o motorista identificado)
  - App             (checklists de jornada preenchidos)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .calculo import DIAS, Segmento
from .checklist import TZ, EventoApp

STATUS_ROTA = {"EXECUTED": "Executada", "UNEXECUTED": "Não executada", "IN_PROGRESS": "Em andamento",
               "PLANNED": "Planejada", "CREATED": "Planejada", "CANCELED": "Cancelada", "CANCELLED": "Cancelada"}


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).replace(tzinfo=None) if ms else None


def _destinos(rota: dict) -> str:
    nomes = []
    for a in rota.get("activities") or []:
        d = a.get("destination") or {}
        if a.get("type") == "START":
            continue
        nomes.append(a.get("name") or d.get("city") or d.get("street_address") or "?")
    return ", ".join(nomes[:4]) + ("…" if len(nomes) > 4 else "")


def rotas_do_motorista(rotas: list[dict], motorista_id: str, placas_por_dia: dict[date, set[str]],
                       id_por_placa: dict[str, str]) -> dict[date, list[dict]]:
    """Rotas por dia: atribuídas ao motorista, ou ao veículo que ele dirigiu naquele dia."""
    out: dict[date, list[dict]] = {}
    for r in rotas:
        ini = _dt(r.get("start_time") or (r.get("activities") or [{}])[0].get("start_time"))
        if not ini:
            continue
        dia = ini.date()
        ids_motorista = {r.get("driver_id")} | {a.get("driver_id") for a in r.get("activities") or []}
        veic = r.get("vehicle_id") or next((a.get("vehicle_id") for a in r.get("activities") or [] if a.get("vehicle_id")), None)
        veics_do_dia = {id_por_placa.get(p) for p in placas_por_dia.get(dia, set())}
        if motorista_id in ids_motorista or (veic and veic in veics_do_dia):
            out.setdefault(dia, []).append(r)
    return out


def resumo_diario(inicio: date, fim: date, motorista_id: str, segs: list[Segmento], eventos: list[EventoApp],
                  rotas: list[dict], id_por_placa: dict[str, str]) -> list[dict]:
    placas_por_dia: dict[date, set[str]] = {}
    for s in segs:
        placas_por_dia.setdefault(s.inicio.date(), set()).add(s.placa)
    rotas_dia = rotas_do_motorista(rotas, motorista_id, placas_por_dia, id_por_placa)

    linhas = []
    d = inicio
    while d <= fim:
        trajs = [s for s in segs if s.tipo == "trajeto" and s.inicio.date() == d]
        evs = [e for e in eventos if e.quando.date() == d]
        rs = rotas_dia.get(d, [])
        km = sum(s.distancia_m for s in trajs) / 1000

        if rs:
            r = rs[0]
            txt_rota = f"{r.get('name') or 'Rota'} · {STATUS_ROTA.get(r.get('status'), r.get('status') or '')}"
            ini_r = _dt(r.get("start_time"))
            if ini_r:
                txt_rota += f" · {ini_r:%H:%M}"
            dest = _destinos(r)
            if dest:
                txt_rota += f" · {dest}"
            if len(rs) > 1:
                txt_rota += f" (+{len(rs) - 1})"
        else:
            txt_rota = "—"

        if trajs:
            txt_rastr = f"{trajs[0].inicio:%H:%M}–{max(s.fim for s in trajs):%H:%M} · {km:.0f} km · " \
                        f"{', '.join(sorted({s.placa for s in trajs}))}"
        else:
            txt_rastr = "—"

        if evs:
            tipos = {e.tipo for e in evs}
            marc = []
            if "inicio" in tipos:
                marc.append("início ✓")
            if tipos & {"fim", "descanso_inter"}:
                marc.append("término ✓")
            txt_app = f"{len(evs)} checklist(s)" + (f" · {' · '.join(marc)}" if marc else "")
        else:
            txt_app = "—"

        if rs:
            situacao, status = "🟢 Rota planejada", "rota"
        elif evs:
            situacao, status = "🔵 Jornada marcada no app", "app"
        elif trajs and km >= 1:
            situacao, status = "🟡 Rodou (sem rota/checklist)", "rodou"
        else:
            situacao, status = "⚪ Sem rota", "nada"
        linhas.append({"Data": f"{d:%d/%m/%Y}", "Dia": DIAS[d.weekday()], "Situação": situacao,
                       "Rota planejada": txt_rota, "Rastreador": txt_rastr, "Jornada no app": txt_app,
                       "_dia": d, "_status": status, "_eventos": evs, "_rotas": rs})
        d += timedelta(days=1)
    return linhas
