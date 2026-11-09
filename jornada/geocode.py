"""Endereço a partir de lat/lng (opcional) via OpenStreetMap Nominatim, com cache em disco.

Limite de uso do Nominatim: 1 requisição/segundo. Para volumes altos, troque por Google
Geocoding API ou outro serviço pago. Coordenadas são arredondadas (~100 m) para reaproveitar cache.
"""
import json
import os
import time

import requests

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "cache_enderecos.json")


class Geocoder:
    def __init__(self, user_agent="cobli-jornada-pdf/1.0", cache_file=_CACHE_FILE):
        self.ua, self.cache_file = user_agent, cache_file
        try:
            self.cache = json.load(open(cache_file, encoding="utf-8"))
        except Exception:
            self.cache = {}
        self._ultimo = 0.0

    def __call__(self, lat, lng):
        k = f"{lat:.3f},{lng:.3f}"
        if k in self.cache:
            return self.cache[k]
        time.sleep(max(0, 1.05 - (time.time() - self._ultimo)))
        try:
            r = requests.get("https://nominatim.openstreetmap.org/reverse",
                             params={"lat": lat, "lon": lng, "format": "jsonv2", "accept-language": "pt-BR",
                                     "zoom": 17},
                             headers={"User-Agent": self.ua}, timeout=20)
            self._ultimo = time.time()
            a = r.json().get("address", {})
            rua = " ".join(x for x in [a.get("road"), a.get("house_number")] if x)
            bairro = a.get("suburb") or a.get("neighbourhood")
            cidade = a.get("city") or a.get("town") or a.get("village") or a.get("municipality")
            partes = [rua, bairro, f"{cidade} - {a.get('ISO3166-2-lvl4', '').replace('BR-', '')}".strip(" -"),
                      a.get("postcode")]
            end = ", ".join(p for p in partes if p) or None
        except Exception:
            end = None
        self.cache[k] = end
        json.dump(self.cache, open(self.cache_file, "w", encoding="utf-8"), ensure_ascii=False)
        return end
