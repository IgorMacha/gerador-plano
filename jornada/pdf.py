"""Gera o PDF de jornada no layout do relatório de referência do cliente."""
from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .calculo import Jornada, hms, totais

CINZA = colors.HexColor("#D9D9D9")
BORDA = colors.HexColor("#555555")
LINK = colors.HexColor("#1F3FA8")
ESTIMADO = colors.HexColor("#8A5A00")

st_cel = ParagraphStyle("cel", fontName="Helvetica", fontSize=7, leading=8.5)
st_est = ParagraphStyle("est", parent=st_cel, textColor=ESTIMADO)
st_link = ParagraphStyle("link", parent=st_cel, textColor=LINK)
st_titulo = ParagraphStyle("tit", fontName="Helvetica-Bold", fontSize=11, leading=14)
st_nota = ParagraphStyle("nota", parent=st_cel, fontSize=6.5, textColor=colors.HexColor("#555555"))


def _p(txt, st=st_cel):
    return Paragraph(escape(str(txt)), st)


def _tabela(linhas, larguras):
    t = Table([[c if isinstance(c, Paragraph) else _p(c) for c in l] for l in linhas], colWidths=larguras)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDA),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("BACKGROUND", (0, 0), (-1, 0), CINZA),
    ]))
    return t


def _recuo(tabela, recuo, largura):
    return Table([["", tabela]], colWidths=[recuo, largura],
                 style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])


def _endereco(ev, geocoder=None):
    texto = ev.endereco or ev.local
    if not texto and geocoder and ev.lat is not None:
        texto = geocoder(ev.lat, ev.lng)
    if not texto and ev.lat is not None:
        texto = f"{ev.lat:.5f}, {ev.lng:.5f}"
    if not texto:
        return _p("-")
    if ev.lat is None:
        return _p(texto)
    url = f"https://www.google.com/maps?q={ev.lat},{ev.lng}"
    return Paragraph(f'<link href="{url}">{escape(texto)}</link>', st_link)


def _descricao(ev):
    txt = escape(ev.descricao + (" *" if ev.estimado else ""))
    if ev.detalhe:
        txt += f'<br/><font size="6" color="#555555">{escape(ev.detalhe)}</font>'
    return Paragraph(txt, st_est if ev.estimado else st_cel)


COLS_JORNADA = ["Motorista", "Trabalho", "Direção", "Espera", "Carga/ Descarga", "Refeição", "Descanso",
                "Descanso interjornada", "Hora extra", "Tempo noturno", "Km"]


def gerar_pdf(caminho: str, cliente: str, periodo: tuple[date, date], jornadas: list[Jornada],
              motorista: str, placa: str | None = None, geocoder=None) -> str:
    W = landscape(A4)[0] - 20 * mm
    doc = SimpleDocTemplate(caminho, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=12 * mm, title=f"Jornada - {motorista}", author="Cobli")
    els = [Paragraph("Relatório de Jornada do Motorista", st_titulo), Spacer(1, 4)]

    if placa is None:
        placa = ", ".join(dict.fromkeys(p for j in jornadas for p in j.placas)) or "-"
    ini, fim = periodo
    els.append(_tabela(
        [["Cliente", "Período", "Placa", "Ativo", "Motorista"],
         [cliente, f"{ini:%d/%m/%Y} 00:00:00 - {fim:%d/%m/%Y} 23:59:59", placa, "Todos", motorista]],
        [W * .34, W * .27, W * .14, W * .07, W * .18]))
    els.append(Spacer(1, 8))

    t = totais(jornadas)
    els.append(_tabela([list(t.keys()), [hms(v) if not isinstance(v, float) else f"{v:.1f}" for v in t.values()]],
                       [W / len(t)] * len(t)))
    els.append(Spacer(1, 14))

    if not jornadas:
        els.append(_p("Nenhuma jornada encontrada para o motorista no período."))

    for j in jornadas:
        bloco = []
        ini_txt = f"{j.inicio:%d/%m/%Y %H:%M:%S}" + (" *" if j.inicio_estimado else "")
        fim_txt = f"{j.fim:%d/%m/%Y %H:%M:%S}" + (" *" if j.fim_estimado else "")
        bloco.append(_tabela(
            [["Início", "Fim", "Placa", "Motorista", "Duração"],
             [ini_txt, fim_txt, ", ".join(j.placas), j.motorista, hms(j.duracao)]],
            [W * .2, W * .2, W * .16, W * .32, W * .12]))
        vals = [j.motorista, hms(j.trabalho), hms(j.direcao), hms(j.espera), hms(j.carga), hms(j.refeicao),
                hms(j.descanso), hms(j.descanso_inter), hms(j.hora_extra), hms(j.noturno), f"{j.distancia_km:.1f}"]
        w2 = W - 6 * mm
        bloco.append(_recuo(_tabela([COLS_JORNADA, vals], [w2 * .17] + [w2 * .083] * 10), 6 * mm, w2))
        linhas = [["Descrição", "Início", "Fim", "Dia da semana", "Endereço / Local", "Duração"]]
        for e in j.eventos:
            linhas.append([_descricao(e), f"{e.inicio:%d/%m/%Y %H:%M:%S}",
                           f"{e.fim:%d/%m/%Y %H:%M:%S}" if e.fim else "-", e.dia_semana,
                           _endereco(e, geocoder), hms(e.duracao)])
        w3 = W - 12 * mm
        bloco.append(_recuo(_tabela(linhas, [w3 * .19, w3 * .12, w3 * .12, w3 * .08, w3 * .41, w3 * .08]), 12 * mm, w3))
        els.append(KeepTogether(bloco))
        els.append(Spacer(1, 12))

    els.append(_p("Eventos conforme os checklists preenchidos pelo motorista no app Cobli; cada evento dura até o "
                  "próximo. Horários com * indicam início ou término que o motorista não marcou. "
                  "Trabalho = duração - refeição - descanso. Hora extra = trabalho acima de 8 h. Km pelo rastreador.",
                  st_nota))

    def rodape(canvas, doc_):
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(landscape(A4)[0] - 10 * mm, 6 * mm, f"Página {doc_.page}")

    doc.build(els, onFirstPage=rodape, onLaterPages=rodape)
    return caminho
