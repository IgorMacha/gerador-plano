"""Modelos de jornada e cálculo a partir do RASTREADOR (trajetos e paradas da API Cobli).

É o modo de reserva: usado quando o motorista não preencheu os checklists de jornada no app.
O modo principal (checklists) está em `checklist.py` e reaproveita os modelos daqui.

Regras do modo rastreador (todas em `Config`):
- A jornada começa no primeiro trajeto após um descanso longo e termina no último trajeto
  antes de uma parada >= `descanso_interjornada_min_h`.
- Parada >= `descanso_intrajornada_min_min` fora de Local de Interesse = "Descanso".
- Demais paradas = "Parada" (contam como trabalho).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


@dataclass
class Config:
    descanso_interjornada_min_h: float = 4.0
    descanso_intrajornada_min_min: float = 60.0
    jornada_padrao_h: float = 8.0
    noturno_inicio_h: int = 22
    noturno_fim_h: int = 5
    local_interesse_e_trabalho: bool = True
    # modo checklist: o que conta como "saiu dirigindo" quando o motorista esquece de marcar
    trajeto_relevante_km: float = 2.0
    trajeto_relevante_min: float = 10.0
    parada_nao_informada_min: float = 30.0


@dataclass
class Segmento:
    tipo: str  # "trajeto" | "parada"
    inicio: datetime
    fim: datetime
    placa: str
    lat: float
    lng: float
    lat_fim: float
    lng_fim: float
    local: str | None = None
    distancia_m: float = 0.0
    ocioso_s: float = 0.0

    @property
    def duracao(self) -> timedelta:
        return self.fim - self.inicio


@dataclass
class Evento:
    descricao: str
    inicio: datetime
    fim: datetime | None
    lat: float | None
    lng: float | None
    local: str | None = None
    endereco: str | None = None
    estimado: bool = False      # True = veio do rastreador, não de um checklist
    detalhe: str | None = None  # ex.: romaneio, horário do almoço
    categoria: str = "parada"   # direcao | espera | carga | refeicao | descanso | parada | marco

    @property
    def duracao(self) -> timedelta | None:
        return (self.fim - self.inicio) if self.fim else None

    @property
    def dia_semana(self) -> str:
        return DIAS[self.inicio.weekday()]


@dataclass
class Jornada:
    motorista: str
    inicio: datetime
    fim: datetime
    placas: list[str]
    fonte: str = "rastreador"  # "checklist" | "rastreador"
    inicio_estimado: bool = False
    fim_estimado: bool = False
    eventos: list[Evento] = field(default_factory=list)
    direcao: timedelta = timedelta()
    espera: timedelta = timedelta()
    carga: timedelta = timedelta()      # carga + descarga
    refeicao: timedelta = timedelta()
    descanso: timedelta = timedelta()   # descanso dentro da jornada (não conta como trabalho)
    parada: timedelta = timedelta()     # paradas sem motivo informado (contam como trabalho)
    descanso_inter: timedelta | None = None
    trabalho: timedelta = timedelta()
    hora_extra: timedelta = timedelta()
    noturno: timedelta = timedelta()
    distancia_km: float = 0.0
    ocioso: timedelta = timedelta()

    @property
    def duracao(self) -> timedelta:
        return self.fim - self.inicio


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s[:19])


def _local(nome: str | None) -> str | None:
    if not nome:
        return None
    nome = nome.strip().strip('"').strip()
    return nome or None


def segmentos_do_motorista(paths: list[dict], motorista_id: str) -> list[Segmento]:
    segs, vistos = [], set()
    for p in paths:
        if (p.get("driver") or {}).get("id") != motorista_id:
            continue
        sa, ea = p["start_address"], p["end_address"]
        chave = (sa["date"], ea["date"], p["vehicle"]["license_plate"])
        if chave in vistos:
            continue
        vistos.add(chave)
        segs.append(Segmento(
            tipo="trajeto" if p.get("distance_in_meters") is not None else "parada",
            inicio=_parse(sa["date"]), fim=_parse(ea["date"]),
            placa=p["vehicle"]["license_plate"],
            lat=sa["latitude"], lng=sa["longitude"], lat_fim=ea["latitude"], lng_fim=ea["longitude"],
            local=_local(p.get("geofence_name")),
            distancia_m=float(p.get("distance_in_meters") or 0),
            ocioso_s=float(p.get("idle_time_duration") or 0),
        ))
    segs.sort(key=lambda s: s.inicio)
    return segs


def sobreposicao_noturna(ini: datetime, fim: datetime, cfg: Config) -> timedelta:
    total = timedelta()
    dia = (ini - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    while dia <= fim:
        n_ini = dia.replace(hour=cfg.noturno_inicio_h)
        n_fim = (dia + timedelta(days=1)).replace(hour=cfg.noturno_fim_h)
        a, b = max(ini, n_ini), min(fim, n_fim)
        if b > a:
            total += b - a
        dia += timedelta(days=1)
    return total


def fechar_totais(j: Jornada, segs: list[Segmento], cfg: Config) -> None:
    """Trabalho, hora extra, noturno, km e ocioso de uma jornada já com eventos."""
    j.trabalho = j.duracao - j.refeicao - j.descanso
    j.hora_extra = max(timedelta(), j.trabalho - timedelta(hours=cfg.jornada_padrao_h))
    noturno = sobreposicao_noturna(j.inicio, j.fim, cfg)
    for e in j.eventos:
        if e.categoria in ("refeicao", "descanso") and e.fim:
            noturno -= sobreposicao_noturna(e.inicio, e.fim, cfg)
    j.noturno = max(timedelta(), noturno)
    dentro = [s for s in segs if s.fim > j.inicio and s.inicio < j.fim]
    j.distancia_km = sum(s.distancia_m for s in dentro if s.tipo == "trajeto") / 1000
    j.ocioso = timedelta(seconds=sum(s.ocioso_s for s in dentro))
    if j.fonte == "rastreador":
        j.direcao = sum((s.duracao for s in dentro if s.tipo == "trajeto"), timedelta())


def ligar_interjornada(jornadas: list[Jornada]) -> list[Jornada]:
    jornadas.sort(key=lambda j: j.inicio)
    for a, b in zip(jornadas, jornadas[1:]):
        a.descanso_inter = b.inicio - a.fim
    return jornadas


def montar_jornadas(segs: list[Segmento], motorista: str, cfg: Config = Config()) -> list[Jornada]:
    """Modo rastreador: jornadas só a partir de trajetos e paradas."""
    limite_inter = timedelta(hours=cfg.descanso_interjornada_min_h)
    limite_intra = timedelta(minutes=cfg.descanso_intrajornada_min_min)

    blocos: list[list[Segmento]] = []
    atual: list[Segmento] = []
    for s in segs:
        if s.tipo == "parada" and s.duracao >= limite_inter:
            if atual:
                blocos.append(atual)
            atual = []
            continue
        if atual and s.inicio - atual[-1].fim >= limite_inter:
            blocos.append(atual)
            atual = []
        if not atual and s.tipo == "parada":
            continue
        atual.append(s)
    if atual:
        blocos.append(atual)

    jornadas: list[Jornada] = []
    for b in blocos:
        while b and b[-1].tipo == "parada":
            b = b[:-1]
        if not b:
            continue
        j = Jornada(motorista=motorista, inicio=b[0].inicio, fim=b[-1].fim, fonte="rastreador",
                    placas=list(dict.fromkeys(s.placa for s in b)))
        j.eventos.append(Evento("Início de jornada", b[0].inicio, None, b[0].lat, b[0].lng, b[0].local,
                                estimado=True, categoria="marco"))
        cursor = b[0].inicio
        for s in b:
            if s.inicio > cursor:
                gap = s.inicio - cursor
                if gap >= limite_intra:
                    j.descanso += gap
                else:
                    j.parada += gap
            if s.tipo == "trajeto":
                desc, cat = "Direção", "direcao"
            elif s.duracao >= limite_intra and not (cfg.local_interesse_e_trabalho and s.local):
                j.descanso += s.duracao
                desc, cat = "Descanso", "descanso"
            else:
                j.parada += s.duracao
                desc, cat = ("Parada em local" if s.local else "Parada"), "parada"
            ultimo = j.eventos[-1]
            if ultimo.categoria == cat == "direcao":
                ultimo.fim = s.fim
            else:
                j.eventos.append(Evento(desc, s.inicio, s.fim, s.lat, s.lng, s.local, estimado=True, categoria=cat))
            cursor = max(cursor, s.fim)
        last = b[-1]
        j.eventos.append(Evento("Fim de jornada", last.fim, None, last.lat_fim, last.lng_fim,
                                estimado=True, categoria="marco"))
        fechar_totais(j, segs, cfg)
        jornadas.append(j)
    return ligar_interjornada(jornadas)


COLUNAS_TOTAIS = ["Duração total", "Trabalho", "Direção", "Espera", "Carga/Descarga", "Refeição", "Descanso",
                  "Descanso interjornada", "Hora extra", "Tempo noturno", "Distância (km)"]


def totais(jornadas: list[Jornada]) -> dict[str, timedelta | float]:
    soma = lambda attr: sum((getattr(j, attr) for j in jornadas), timedelta())
    return {
        "Duração total": soma("duracao"),
        "Trabalho": soma("trabalho"),
        "Direção": soma("direcao"),
        "Espera": soma("espera"),
        "Carga/Descarga": soma("carga"),
        "Refeição": soma("refeicao"),
        "Descanso": soma("descanso"),
        "Descanso interjornada": sum((j.descanso_inter for j in jornadas if j.descanso_inter), timedelta()),
        "Hora extra": soma("hora_extra"),
        "Tempo noturno": soma("noturno"),
        "Distância (km)": float(round(sum(j.distancia_km for j in jornadas), 1)),
    }


def hms(td) -> str:
    if td is None:
        return "-"
    if not isinstance(td, timedelta):
        return str(td)
    s = int(td.total_seconds())
    sinal = "-" if s < 0 else ""
    s = abs(s)
    return f"{sinal}{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
