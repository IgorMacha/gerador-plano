"""Cliente mínimo da API pública da Cobli (https://docs.cobli.co).

Endpoints usados:
  GET /public/v1/drivers                    -> motoristas
  GET /public/v1/vehicles                   -> veículos (placa <-> id)
  GET /public/v1/paths                      -> trajetos e paradas (rastreador)
  GET /checklists/completed-checklists      -> checklists preenchidos no app (eventos da jornada)
  GET /public/v2/routes                     -> rotas planejadas (Serviços e Entregas)

Todos os métodos aceitam `progresso(mensagem, fração 0-1)` para a barra de carregamento.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable

import requests

from .checklist import TZ

BASE_URL = "https://api.cobli.co"
Progresso = Callable[[str, float], None] | None


class CobliError(RuntimeError):
    pass


def _ms(d: date, fim_do_dia=False) -> int:
    t = datetime.combine(d, datetime.max.time() if fim_do_dia else datetime.min.time()).replace(tzinfo=TZ)
    return int(t.timestamp() * 1000)


class CobliClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"cobli-api-key": api_key.strip(), "accept": "application/json"})

    # ------------------------------------------------------------------ util
    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        r = None
        for tentativa in range(5):
            try:
                r = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            except requests.RequestException as e:
                if tentativa == 4:
                    raise CobliError(f"Sem conexão com a API Cobli: {e}") from e
                time.sleep(1 + tentativa)
                continue
            if r.status_code in (429, 502, 503, 504):  # limite 50 req/s por IP / instabilidade
                time.sleep(1 + tentativa)
                continue
            if r.status_code == 401:
                raise CobliError("Chave de API inválida ou revogada (401).")
            return r
        return r

    def _paginate_v1(self, path: str, params: dict) -> list[dict]:
        out, page = [], 1
        while True:
            r = self._get(path, {**params, "page": page})
            if r.status_code == 404:  # "No match results." = acabou / sem dados
                break
            if r.status_code != 200:
                raise CobliError(f"{path} -> HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            out.extend(body.get("data", []))
            if not (body.get("pagination") or {}).get("next"):
                break
            page += 1
        return out

    # ------------------------------------------------------------ cadastros
    def drivers(self) -> list[dict]:
        return self._paginate_v1("/public/v1/drivers", {"limit": 500})

    def vehicles(self) -> list[dict]:
        return self._paginate_v1("/public/v1/vehicles", {"limit": 500})

    # ------------------------------------------------------------ rastreador
    def paths(self, inicio: date, fim: date, placas: list[str] | None = None,
              timezone: str = "America/Sao_Paulo", janela_dias: int = 7, progresso: Progresso = None) -> list[dict]:
        """Trajetos e paradas entre `inicio` e `fim` (inclusive), em janelas de N dias."""
        out: list[dict] = []
        janelas, d = [], inicio
        while d <= fim:
            d2 = min(d + timedelta(days=janela_dias - 1), fim)
            janelas.append((d, d2))
            d = d2 + timedelta(days=1)
        for i, (a, b) in enumerate(janelas):
            if progresso:
                progresso(f"Trajetos e paradas: {a:%d/%m} a {b:%d/%m}", i / len(janelas))
            params = {
                "startDate": datetime.combine(a, datetime.min.time()).strftime("%Y-%m-%dT%H:%M:%S"),
                "endDate": datetime.combine(b, datetime.max.time()).strftime("%Y-%m-%dT%H:%M:%S"),
                "limit": 2000, "timezone": timezone,
            }
            if placas:
                params["licensePlates"] = ",".join(placas)
            out.extend(self._paginate_v1("/public/v1/paths", params))
        if progresso:
            progresso("Trajetos e paradas carregados", 1.0)
        return out

    # ------------------------------------------------------------ app do motorista
    def checklists(self, inicio: date, fim: date, progresso: Progresso = None) -> list[dict]:
        """Checklists preenchidos (eventos de jornada) entre `inicio` e `fim`."""
        out, page, total = [], 0, None
        while True:
            r = self._get("/checklists/completed-checklists",
                          {"start_in_millis": _ms(inicio), "end_in_millis": _ms(fim, True),
                           "size": 500, "page": page})
            if r.status_code == 404:
                break
            if r.status_code != 200:
                raise CobliError(f"checklists -> HTTP {r.status_code}: {r.text[:300]}")
            pg = r.json().get("page", {})
            out.extend(pg.get("content", []))
            total = pg.get("total_pages") or 1
            if progresso:
                progresso(f"Checklists: página {page + 1} de {total}", min(1.0, (page + 1) / total))
            if pg.get("last", True) or not pg.get("content"):
                break
            page += 1
        return out

    def routes(self, inicio: date, fim: date, progresso: Progresso = None) -> list[dict]:
        """Rotas planejadas no módulo Serviços e Entregas."""
        out, page = [], 0
        while True:
            r = self._get("/public/v2/routes", {"start_in_millis": _ms(inicio), "end_in_millis": _ms(fim, True),
                                                "page": page, "size": 100})
            if r.status_code in (403, 404):  # cliente sem o módulo / sem rotas
                break
            if r.status_code != 200:
                raise CobliError(f"rotas -> HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            out.extend(body.get("content", []))
            if progresso:
                progresso(f"Rotas planejadas: página {page + 1}", 1.0)
            if body.get("last", True) or not body.get("content"):
                break
            page += 1
        return out
