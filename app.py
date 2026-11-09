"""Sistema de Jornada do Motorista (Streamlit) — API Cobli.

Abas:
  1. Relatório de jornada  -> PDF com os checklists marcados pelo motorista no app
  2. Calendário de rotas   -> em quais dias o motorista teve rota

Rodar:  py -m streamlit run app.py   (ou dois cliques em ABRIR_SISTEMA.bat)
"""
import calendar
import html
import io
import re
import tempfile
import time
import zipfile
from datetime import date, timedelta

import streamlit as st

from jornada.calculo import Config, hms, segmentos_do_motorista, totais
from jornada.checklist import eventos_do_motorista, montar
from jornada.cobli_api import CobliClient, CobliError
from jornada.consulta import resumo_diario
from jornada.pdf import gerar_pdf

st.set_page_config(page_title="Jornada do Motorista", page_icon="🚚", layout="wide",
                   initial_sidebar_state="collapsed")
CACHE_MIN = 10
MESES = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro",
         "Outubro", "Novembro", "Dezembro"]


# ------------------------------------------------------------------ configuração fixa
def _segredo(nome):
    try:
        return st.secrets.get(nome, "")
    except Exception:
        return ""


api_key = _segredo("COBLI_API_KEY")
cliente = _segredo("CLIENTE")

st.title("🚚 Jornada do Motorista")
if cliente:
    st.caption(f"Cliente: **{cliente}**")
if not api_key:
    api_key = st.text_input("Chave de API Cobli", type="password")
if not cliente:
    cliente = st.text_input("Nome do cliente (aparece no cabeçalho do PDF)")
if not api_key:
    st.info("Informe a chave de API para começar.")
    st.stop()

api = CobliClient(api_key)
cfg = Config()


# ------------------------------------------------------------------ dados (com cache de 10 min)
def cadastros():
    cad = st.session_state.get("cad")
    if cad and time.time() - cad["t"] < CACHE_MIN * 60:
        return cad
    try:
        motoristas = sorted(api.drivers(), key=lambda m: m["name"])
        veiculos = api.vehicles()
    except CobliError as e:
        st.error(str(e))
        st.stop()
    cad = {"t": time.time(), "motoristas": motoristas,
           "id_por_placa": {v["license_plate"]: v["id"] for v in veiculos}}
    st.session_state["cad"] = cad
    return cad


def carregar(ini: date, fim: date, com_rotas: bool = False, progresso=None) -> dict:
    """Trajetos, checklists (e rotas) do período. `progresso(msg, 0-1)` é opcional."""
    cache = st.session_state.setdefault("cache", {})
    chave = (ini, fim, com_rotas)
    if chave in cache and time.time() - cache[chave]["t"] < CACHE_MIN * 60:
        if progresso:
            progresso("Dados já carregados", 1.0)
        return cache[chave]
    a, b = ini - timedelta(days=1), fim + timedelta(days=1)  # margem p/ jornadas que viram o dia
    p = progresso or (lambda m, f: None)
    try:
        paths = api.paths(a, b, progresso=lambda m, f: p(m, 0.7 * f))
        checks = api.checklists(a, b, progresso=lambda m, f: p(m, 0.7 + 0.25 * f))
        rotas = api.routes(a, b) if com_rotas else []
    except CobliError as e:
        st.error(str(e))
        st.stop()
    p("Dados carregados", 1.0)
    dados = {"t": time.time(), "paths": paths, "checks": checks, "rotas": rotas}
    cache[chave] = dados
    return dados


def nome_arquivo(nome, ini, fim):
    return f"jornada_{re.sub(r'[^A-Za-z0-9]+', '_', nome).strip('_')}_{ini:%Y%m%d}_{fim:%Y%m%d}.pdf"


def periodo(valor):
    """date_input em modo intervalo devolve 1 ou 2 datas enquanto o usuário escolhe."""
    if isinstance(valor, (list, tuple)):
        if len(valor) == 2:
            return valor[0], valor[1]
        if len(valor) == 1:
            return valor[0], valor[0]
        return None, None
    return valor, valor


cad = cadastros()
motoristas = cad["motoristas"]
c_vazio, c_btn = st.columns([6, 1])
if c_btn.button("🔄 Atualizar dados", width="stretch"):
    st.session_state.pop("cache", None)
    st.session_state.pop("cad", None)
    st.rerun()

aba_rel, aba_cal = st.tabs(["📄 Relatório de jornada", "📅 Calendário de rotas"])

# ================================================================== ABA 1 — RELATÓRIO (PDF)
with aba_rel:
    c1, c2 = st.columns([2, 2])
    opcoes = [{"id": "*", "name": "Todos os motoristas (arquivo .zip)"}] + motoristas
    escolhido = c1.selectbox("Motorista", opcoes, index=1 if len(opcoes) > 1 else 0,
                             format_func=lambda m: m["name"], key="rel_mot")
    rel_per = c2.date_input("Período (clique na data inicial e depois na final)",
                            (date.today() - timedelta(days=7), date.today() - timedelta(days=1)),
                            format="DD/MM/YYYY", key="rel_per")
    ini, fim = periodo(rel_per)

    if st.button("📄 Gerar PDF", type="primary", disabled=ini is None):
        barra = st.progress(0.0, text="Buscando checklists na Cobli...")
        dados = carregar(ini, fim, progresso=lambda m, f: barra.progress(0.6 * f, text="Buscando dados na Cobli..."))
        alvo = motoristas if escolhido["id"] == "*" else [escolhido]
        arquivos, resumo = {}, []
        for i, m in enumerate(alvo):
            barra.progress(0.6 + 0.4 * i / len(alvo), text=f"Montando PDF: {m['name']}")
            segs = segmentos_do_motorista(dados["paths"], m["id"])
            evs = eventos_do_motorista(dados["checks"], m["name"])
            js = [j for j in montar(evs, segs, m["name"], cfg) if ini <= j.inicio.date() <= fim]
            if not js:
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                gerar_pdf(f.name, cliente, (ini, fim), js, m["name"])
                arquivos[nome_arquivo(m["name"], ini, fim)] = open(f.name, "rb").read()
            t = totais(js)
            resumo.append({"Motorista": m["name"], "Jornadas": len(js), "Trabalho": hms(t["Trabalho"]),
                           "Direção": hms(t["Direção"]), "Espera": hms(t["Espera"]),
                           "Carga/Descarga": hms(t["Carga/Descarga"]), "Hora extra": hms(t["Hora extra"])})
        barra.empty()

        if not arquivos:
            st.warning("Nenhum checklist de jornada preenchido no período.")
        else:
            st.success(f"PDF pronto: {sum(r['Jornadas'] for r in resumo)} jornada(s).")
            st.dataframe(resumo, hide_index=True, width="stretch")
            if len(arquivos) == 1:
                nome, dados_pdf = next(iter(arquivos.items()))
                st.download_button("⬇️ Baixar PDF", dados_pdf, file_name=nome, mime="application/pdf", type="primary")
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for nome, dados_pdf in arquivos.items():
                        z.writestr(nome, dados_pdf)
                st.download_button("⬇️ Baixar todos (.zip)", buf.getvalue(), type="primary",
                                   file_name=f"jornadas_{ini:%Y%m%d}_{fim:%Y%m%d}.zip", mime="application/zip")

# ================================================================== ABA 2 — CALENDÁRIO
# (cor do texto, fundo translúcido — funciona no tema claro e no escuro, rótulo)
CORES = {"rota": ("#34A853", "rgba(52,168,83,.22)", "Rota planejada"),
         "app": ("#4C8DF6", "rgba(76,141,246,.22)", "Jornada marcada no app"),
         "rodou": ("#E8A317", "rgba(232,163,23,.22)", "Rodou sem rota/checklist"),
         "nada": ("#9AA0A6", "rgba(154,160,166,.12)", "Sem rota")}

CSS = """
<style>
.cal-wrap{display:flex;flex-wrap:wrap;gap:24px}
.cal{border-collapse:separate !important;border-spacing:4px;font-family:inherit;border:none !important;margin:0}
.cal tr,.cal tr:nth-child(even){background:transparent !important}
.cal td,.cal th{border:none !important}
.cal caption{font-weight:600;font-size:1.05rem;text-align:left;padding:4px 2px 8px}
.cal th{font-size:.75rem;opacity:.7;font-weight:600;text-align:center;padding:2px}
.cal td{width:58px;height:52px;border-radius:8px;vertical-align:top;padding:4px 6px;font-size:.8rem;border:1px solid transparent}
.cal td .n{font-weight:600}
.cal td .t{font-size:.65rem;line-height:1.1;margin-top:2px}
.cal td.vazio{background:transparent}
.cal td.fora{opacity:.25}
.cal td.hoje{box-shadow:inset 0 0 0 2px currentColor}
.leg{display:flex;gap:16px;flex-wrap:wrap;margin:6px 0 14px;font-size:.85rem}
.leg span{display:inline-flex;align-items:center;gap:6px}
.leg i{width:14px;height:14px;border-radius:4px;display:inline-block}
</style>
"""


def html_calendario(ini: date, fim: date, por_dia: dict) -> str:
    cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
    partes = ['<div class="cal-wrap">']
    ano, mes = ini.year, ini.month
    while (ano, mes) <= (fim.year, fim.month):
        partes.append(f'<table class="cal"><caption>{MESES[mes]} {ano}</caption><tr>')
        partes += [f"<th>{d}</th>" for d in ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]]
        partes.append("</tr>")
        for semana in cal.monthdatescalendar(ano, mes):
            partes.append("<tr>")
            for d in semana:
                if d.month != mes:
                    partes.append("<td></td>")
                    continue
                classes = []
                if d == date.today():
                    classes.append("hoje")
                linha = por_dia.get(d)
                if linha is None:
                    classes.append("fora")
                    partes.append(f'<td class="{" ".join(classes)}"><div class="n">{d.day}</div></td>')
                    continue
                cor, fundo, rotulo = CORES[linha["_status"]]
                dica = html.escape(f"{d:%d/%m} — {rotulo}\nRota: {linha['Rota planejada']}\n"
                                   f"Rastreador: {linha['Rastreador']}\nApp: {linha['Jornada no app']}")
                curto = {"rota": "Rota", "app": "Jornada", "rodou": "Rodou", "nada": ""}[linha["_status"]]
                partes.append(f'<td class="{" ".join(classes)}" style="background:{fundo};color:{cor}" title="{dica}">'
                              f'<div class="n">{d.day}</div><div class="t">{curto}</div></td>')
            partes.append("</tr>")
        partes.append("</table>")
        mes += 1
        if mes == 13:
            ano, mes = ano + 1, 1
    partes.append("</div>")
    return "".join(partes)


with aba_cal:
    c1, c2 = st.columns([2, 2])
    m_cal = c1.selectbox("Motorista", motoristas, format_func=lambda m: m["name"], key="cal_mot")
    hoje = date.today()
    cal_per = c2.date_input("Datas (clique na data inicial e depois na final)",
                            (hoje.replace(day=1), hoje), format="DD/MM/YYYY", key="cal_per")
    c_ini, c_fim = periodo(cal_per)

    if c_ini is None:
        st.info("Escolha as datas no calendário.")
    else:
        dados = carregar(c_ini, c_fim, com_rotas=True)
        segs = segmentos_do_motorista(dados["paths"], m_cal["id"])
        evs = eventos_do_motorista(dados["checks"], m_cal["name"])
        linhas = resumo_diario(c_ini, c_fim, m_cal["id"], segs, evs, dados["rotas"], cad["id_por_placa"])
        por_dia = {l["_dia"]: l for l in linhas}

        com_rota = [l for l in linhas if l["_status"] != "nada"]
        k1, k2, k3 = st.columns(3)
        k1.metric("Dias no período", len(linhas))
        k2.metric("Dias com rota", len(com_rota))
        k3.metric("Dias sem rota", len(linhas) - len(com_rota))

        st.markdown(CSS + '<div class="leg">' + "".join(
            f'<span><i style="background:{f};border:1px solid {c}"></i>{r}</span>' for c, f, r in CORES.values())
            + "</div>" + html_calendario(c_ini, c_fim, por_dia), unsafe_allow_html=True)

        st.divider()
        dia = st.date_input("Ver detalhes do dia", min(c_fim, max(c_ini, hoje)), min_value=c_ini,
                            max_value=c_fim, format="DD/MM/YYYY", key=f"cal_dia_{c_ini}_{c_fim}")
        l = por_dia.get(dia)
        if l:
            cor, fundo, rotulo = CORES[l["_status"]]
            st.markdown(f'<div style="background:{fundo};color:{cor};padding:10px 14px;border-radius:8px;'
                        f'font-weight:600">{dia:%d/%m/%Y} ({l["Dia"]}) — {rotulo}</div>', unsafe_allow_html=True)
            d1, d2, d3 = st.columns(3)
            d1.markdown(f"**Rota planejada**  \n{l['Rota planejada']}")
            d2.markdown(f"**Rastreador**  \n{l['Rastreador']}")
            d3.markdown(f"**Jornada no app**  \n{l['Jornada no app']}")
            if l["_eventos"]:
                st.dataframe([{"Hora": f"{e.quando:%H:%M}", "Checklist": e.rotulo, "Placa": e.placa or "",
                               "Endereço": e.endereco or "", "Detalhe": e.detalhe or ""} for e in l["_eventos"]],
                             hide_index=True, width="stretch")
