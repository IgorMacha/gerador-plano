"""Jornada a partir dos CHECKLISTS que o motorista preenche no app Cobli (fonte principal).

Cada checklist preenchido é um evento (Início da jornada, Espera, Descarga, Refeição, Direção,
Descanso, Término...). Cada evento dura até o próximo. O rastreador entra para:
  - estimar início/fim quando o motorista esquece de marcar;
  - quebrar um evento parado (Espera, Descarga, Refeição, Descanso) quando o veículo sai dirigindo
    e o motorista não marcou "Direção" — o trecho vira "Direção (rastreador)";
  - completar endereço/coordenadas, km e motor ocioso.
Eventos que vieram do rastreador aparecem com * no PDF.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .calculo import Config, Evento, Jornada, Segmento, fechar_totais, ligar_interjornada

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:  # Windows sem tzdata: Brasil sem horário de verão desde 2019
    TZ = timezone(timedelta(hours=-3))

# (trecho do nome normalizado, tipo, rótulo no relatório, categoria de tempo)
# A ordem importa: "DESCARGA" antes de "CARGA".
TIPOS = [
    ("INICIO DA JORNADA", "inicio", "Início de jornada", "direcao"),
    ("INICIO JORNADA", "inicio", "Início de jornada", "direcao"),
    ("TERMINO DA JORNADA", "fim", "Fim de jornada", "marco"),
    ("FIM DA JORNADA", "fim", "Fim de jornada", "marco"),
    ("DESCANSO INTERJORNADA", "descanso_inter", "Fim de jornada (descanso interjornada)", "marco"),
    ("DESCANSO", "descanso", "Descanso", "descanso"),
    ("ESPERA", "espera", "Espera", "espera"),
    ("DESCARGA", "descarga", "Descarga", "carga"),
    ("CARGA", "carga", "Carga", "carga"),
    ("REFEICAO", "refeicao", "Refeição", "refeicao"),
    ("ALMOCO", "refeicao", "Refeição", "refeicao"),
    ("JANTA", "refeicao", "Refeição", "refeicao"),
    ("DIRECAO", "direcao", "Direção", "direcao"),
    ("MANOBRA", "manobra", "Manobra", "direcao"),
]
PARADOS = {"espera", "carga", "refeicao", "descanso", "parada"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"^\s*\d+\s*-\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _nome_limpo(s: str) -> str:
    return re.sub(r"^\s*\d+\s*-\s*", "", (s or "").strip()).strip().capitalize()


@dataclass
class EventoApp:
    quando: datetime
    tipo: str
    rotulo: str
    categoria: str
    placa: str | None
    endereco: str | None
    detalhe: str | None


def _detalhe(fields: list[dict]) -> str | None:
    partes = []
    for f in fields or []:
        c = f.get("content") or {}
        titulo = (f.get("title") or "").strip()
        if c.get("options"):
            marc = [o["label"].strip() for o in c["options"] if o.get("checked")]
            marc = [m for m in marc if _norm(m) not in ("INICIO", "FIM")]
            if marc:
                partes.append(", ".join(marc).title() if _norm(titulo) == "AJUDANTES" else ", ".join(marc))
        elif c.get("value") not in (None, ""):
            partes.append(f"{titulo.title()} {c['value']}")
    return " · ".join(partes) or None


def _check(fields: list[dict], titulo: str) -> bool:
    for f in fields or []:
        if _norm(f.get("title")) == titulo and (f.get("content") or {}).get("checked"):
            return True
    return False


def eventos_do_motorista(checklists: list[dict], nome_motorista: str) -> list[EventoApp]:
    alvo = _norm(nome_motorista)
    out: list[EventoApp] = []
    vistos = set()
    for c in checklists:
        if _norm(c.get("created_by")) != alvo or c.get("template"):
            continue
        nome = _norm(c.get("name"))
        tipo, rotulo, cat = "outro", _nome_limpo(c.get("name")), "parada"
        for chave, t, r, k in TIPOS:
            if chave in nome:
                tipo, rotulo, cat = t, r, k
                break
        so_fim = _check(c.get("fields"), "FIM") and not _check(c.get("fields"), "INICIO")
        if tipo == "descanso" and so_fim:
            tipo, rotulo, cat = "fim_descanso", "Fim do descanso", "direcao"
        elif tipo == "descanso_inter" and so_fim:  # fim do descanso interjornada = começa nova jornada
            tipo, rotulo, cat = "inicio", "Início de jornada (fim do descanso)", "direcao"
        if "CHECKLIST" in nome and tipo == "outro":
            continue  # checklist de inspeção do veículo não é evento de jornada
        quando = datetime.fromtimestamp(c["created_at"] / 1000, TZ).replace(tzinfo=None, microsecond=0)
        if (quando, tipo) in vistos:
            continue
        vistos.add((quando, tipo))
        ctx = c.get("context") or {}
        out.append(EventoApp(quando, tipo, rotulo, cat, (ctx.get("vehicle") or {}).get("license_plate"),
                             ctx.get("address"), _detalhe(c.get("fields")) if not tipo.startswith(("descanso", "fim_desc")) and "descanso" not in rotulo else None))
    out.sort(key=lambda e: e.quando)
    return out


# --------------------------------------------------------------------------- rastreador auxiliar
def _trajetos(segs):
    return [s for s in segs if s.tipo == "trajeto"]


def _posicao(segs: list[Segmento], t: datetime):
    """Segmento do rastreador mais próximo de t (para coordenada/local)."""
    melhor, dist = None, None
    for s in segs:
        d = timedelta() if s.inicio <= t <= s.fim else min(abs(s.inicio - t), abs(s.fim - t))
        if dist is None or d < dist:
            melhor, dist = s, d
    if melhor is None or dist > timedelta(minutes=30):
        return None, None, None
    if melhor.tipo == "trajeto" and abs(melhor.fim - t) < abs(melhor.inicio - t):
        return melhor.lat_fim, melhor.lng_fim, None
    return melhor.lat, melhor.lng, melhor.local


def _relevante(s: Segmento, cfg: Config) -> bool:
    return s.tipo == "trajeto" and (s.distancia_m >= cfg.trajeto_relevante_km * 1000
                                    or s.duracao >= timedelta(minutes=cfg.trajeto_relevante_min))


def _inicio_estimado(t: datetime, segs, cfg: Config) -> datetime:
    """Volta no rastreador a partir de t enquanto não houver um intervalo >= interjornada."""
    limite = timedelta(hours=cfg.descanso_interjornada_min_h)
    ini = t
    for s in sorted(_trajetos(segs), key=lambda s: s.fim, reverse=True):
        if s.inicio >= t:
            continue
        if ini - s.fim >= limite:
            break
        ini = min(ini, s.inicio)
    return ini


def _fim_estimado(ultimo: datetime, proximo_inicio: datetime | None, segs, cfg: Config) -> datetime:
    """Fim provável quando o motorista não marcou o término."""
    trajs = [s for s in _trajetos(segs) if s.inicio >= ultimo - timedelta(minutes=5)]
    if proximo_inicio and proximo_inicio - ultimo <= timedelta(hours=24):
        antes = [s for s in trajs if s.fim <= proximo_inicio]
        return max([ultimo] + [s.fim for s in antes])
    limite = timedelta(hours=cfg.descanso_interjornada_min_h)
    fim = ultimo
    for s in trajs:
        if proximo_inicio and s.inicio >= proximo_inicio:
            break
        if s.inicio - fim >= limite:
            break
        fim = max(fim, s.fim)
    return fim


def _trecho_rastreador(t0: datetime, t1: datetime, segs, cfg: Config) -> list[Evento]:
    """Converte o intervalo [t0, t1) em eventos do rastreador (Direção / Parada não informada)."""
    lim_parada = timedelta(minutes=cfg.parada_nao_informada_min)
    evs: list[Evento] = []
    for s in segs:
        a, b = max(s.inicio, t0), min(s.fim, t1)
        if b <= a:
            continue
        if s.tipo == "parada" and (b - a) >= lim_parada:
            evs.append(Evento("Parada não informada", a, b, s.lat, s.lng, s.local, estimado=True, categoria="parada"))
        elif evs and evs[-1].categoria == "direcao":
            evs[-1].fim = b
        else:
            evs.append(Evento("Direção", a, b, s.lat, s.lng, s.local, estimado=True, categoria="direcao"))
    if not evs:
        return [Evento("Direção", t0, t1, None, None, estimado=True, categoria="direcao")]
    evs[0].inicio = t0  # cola as pontas
    evs[-1].fim = t1
    for a, b in zip(evs, evs[1:]):
        a.fim = b.inicio
    return evs


# --------------------------------------------------------------------------- montagem
def montar_jornadas_checklist(eventos: list[EventoApp], segs: list[Segmento], motorista: str,
                              cfg: Config = Config(), somente_app: bool = True) -> list[Jornada]:
    """somente_app=True: mostra exatamente o que o motorista marcou, sem completar nada pelo rastreador."""
    grupos: list[dict] = []
    atual = None
    for ev in eventos:
        if ev.tipo == "inicio":
            if atual:
                grupos.append(atual)
            atual = {"eventos": [ev], "fim": None}
        elif ev.tipo in ("fim", "descanso_inter"):
            if atual is None:
                atual = {"eventos": [], "fim": None}
            atual["fim"] = ev
            grupos.append(atual)
            atual = None
        else:
            if atual is None:
                atual = {"eventos": [], "fim": None}
            atual["eventos"].append(ev)
    if atual:
        grupos.append(atual)

    jornadas: list[Jornada] = []
    for gi, g in enumerate(grupos):
        evs: list[EventoApp] = g["eventos"]
        primeiro = evs[0].quando if evs else g["fim"].quando
        # ---- início
        if evs and evs[0].tipo == "inicio":
            ini, ini_est = evs[0].quando, False
            if not somente_app:
                fim_anterior = jornadas[-1].fim if jornadas else datetime.min
                antes = [x for x in segs if _relevante(x, cfg) and max(fim_anterior, ini - timedelta(hours=3)) < x.inicio
                         and x.inicio < ini - timedelta(minutes=10) and x.fim > ini - timedelta(minutes=30)]
                if antes:  # motorista marcou o início com o caminhão já rodando
                    ini, ini_est = antes[0].inicio, True
        elif somente_app:
            ini, ini_est = primeiro, True
        else:
            ini, ini_est = _inicio_estimado(primeiro, segs, cfg), True
        # ---- fim
        if g["fim"]:
            fim, fim_est = g["fim"].quando, False
        else:
            prox = next((x["eventos"][0].quando for x in grupos[gi + 1:]
                         if x["eventos"] and x["eventos"][0].tipo == "inicio"), None)
            ultimo = evs[-1].quando if evs else ini
            fim = ultimo if somente_app else _fim_estimado(ultimo, prox, segs, cfg)
            fim_est = True
        if fim <= ini:
            fim = max(fim, ini)

        placas = [e.placa for e in evs + ([g["fim"]] if g["fim"] else []) if e.placa]
        placas += [s.placa for s in segs if ini <= s.inicio < fim]
        j = Jornada(motorista=motorista, inicio=ini, fim=fim, placas=list(dict.fromkeys(placas)),
                    fonte="checklist", inicio_estimado=ini_est, fim_estimado=fim_est)

        # linha do tempo
        marcos = list(evs)
        if ini_est and somente_app:
            j.eventos.append(Evento("Início não marcado no app", ini, None, None, None, categoria="marco"))
        elif ini_est:
            lat, lng, loc = _posicao(segs, ini)
            j.eventos.append(Evento("Início de jornada", ini, None, lat, lng, loc, estimado=True, categoria="marco"))
            # do início estimado até o 1º evento marcado: rastreador
            if marcos and marcos[0].quando > ini:
                j.eventos.extend(_trecho_rastreador(ini, marcos[0].quando, segs, cfg))
        for i, ev in enumerate(marcos):
            t0 = ev.quando
            t1 = marcos[i + 1].quando if i + 1 < len(marcos) else fim
            t1 = max(t1, t0)
            lat, lng, loc = _posicao(segs, t0)
            rotulo = "Início marcado no app" if (ini_est and not somente_app and i == 0 and ev.tipo == "inicio") else ev.rotulo
            base = Evento(rotulo, t0, t1, lat, lng, loc, endereco=ev.endereco, detalhe=ev.detalhe,
                          categoria=ev.categoria)
            if ev.categoria in PARADOS and not somente_app:
                saida = next((s for s in segs if _relevante(s, cfg) and t0 + timedelta(minutes=1) < s.inicio < t1), None)
                if saida and t1 - saida.inicio >= timedelta(minutes=5):
                    base.fim = saida.inicio
                    j.eventos.append(base)
                    j.eventos.extend(_trecho_rastreador(saida.inicio, t1, segs, cfg))
                    continue
            j.eventos.append(base)
        if not marcos and not ini_est:
            pass
        # fim
        if g["fim"]:
            lat, lng, loc = _posicao(segs, fim)
            j.eventos.append(Evento(g["fim"].rotulo, fim, None, lat, lng, loc, endereco=g["fim"].endereco,
                                    categoria="marco"))
        else:
            lat, lng, loc = _posicao(segs, fim)
            if somente_app:
                j.eventos.append(Evento("Término não marcado no app", fim, None, None, None, categoria="marco"))
            else:
                j.eventos.append(Evento("Fim de jornada", fim, None, lat, lng, loc, estimado=True, categoria="marco"))

        # somatórios por categoria
        for e in j.eventos:
            if not e.fim or e.categoria == "marco":
                continue
            d = e.fim - e.inicio
            if e.categoria == "direcao":
                j.direcao += d
            elif e.categoria == "espera":
                j.espera += d
            elif e.categoria == "carga":
                j.carga += d
            elif e.categoria == "refeicao":
                j.refeicao += d
            elif e.categoria == "descanso":
                j.descanso += d
            else:
                j.parada += d
        fechar_totais(j, segs, cfg)
        jornadas.append(j)
    return ligar_interjornada(jornadas)


def montar(eventos_app, segs, motorista, cfg: Config = Config(), somente_app: bool = True):
    """Padrão: só os checklists marcados pelo motorista. somente_app=False completa pelo rastreador."""
    from .calculo import montar_jornadas
    if eventos_app:
        return montar_jornadas_checklist(eventos_app, segs, motorista, cfg, somente_app)
    return [] if somente_app else montar_jornadas(segs, motorista, cfg)
