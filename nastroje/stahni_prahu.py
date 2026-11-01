"""Stáhne ulice a cesty Prahy z OpenFreeMap (dlaždice z14) a uloží je do mapa/14/x/y.json.

Hra je čte jako první zdroj, takže v Praze nepotřebuje žádný mapový server.
Spuštění: pip install mapbox-vector-tile; python3 nastroje/stahni_prahu.py
Data: © OpenStreetMap contributors (ODbL), schéma © OpenMapTiles, dlaždice OpenFreeMap.
"""
import json, math, os, sys, time, urllib.request, concurrent.futures as cf
import mapbox_vector_tile

S, W, N, E = 49.94, 14.22, 50.18, 14.71  # Praha s rezervou
Z = 14
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapa", str(Z))
LAYERS = ("transportation_name", "transportation")


def tx(lon): return int((lon + 180) / 360 * 2 ** Z)
def ty(lat): r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** Z)


def hw_of(cls, sub):
    if cls == "minor": return "residential"
    if cls == "path": return sub if sub in ("footway", "pedestrian", "path", "cycleway", "steps", "bridleway") else "path"
    if cls in ("tertiary", "secondary", "primary", "trunk", "motorway", "service", "track"): return cls
    return None


def get(url, tries=4):
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lovci-duchu-mapa/1"}), timeout=30).read()
        except Exception as e:
            if k == tries - 1: raise
            time.sleep(2 + 3 * k)


def one(tpl, x, y):
    data = get(tpl.replace("{z}", str(Z)).replace("{x}", str(x)).replace("{y}", str(y)))
    t = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    ways = []
    for ln in LAYERS:
        lay = t.get(ln)
        if not lay: continue
        for f in lay["features"]:
            p = f["properties"]; hw = hw_of(p.get("class"), p.get("subclass"))
            g = f["geometry"]
            if not hw or g["type"] not in ("LineString", "MultiLineString"): continue
            fl = ""
            if p.get("brunnel") == "bridge" or (p.get("layer") or 0) > 0: fl += "b"
            if p.get("brunnel") == "tunnel" or (p.get("layer") or 0) < 0: fl += "t"
            if p.get("indoor"): fl += "i"
            if p.get("access"): fl += "a"
            svc = p.get("service") or ""
            name = p.get("name") if ln == "transportation_name" else ""
            if ln == "transportation" and hw in ("residential", "tertiary") and not fl and not svc:
                continue  # nepojmenované kusy ulic: jména jsou v transportation_name, tohle by jen zvětšilo soubor
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for line in lines:
                if len(line) < 2: continue
                flat = [int(v) for pt in line for v in pt]
                ways.append([hw, name or "", fl, svc, flat])
    os.makedirs(os.path.join(OUT, str(x)), exist_ok=True)
    with open(os.path.join(OUT, str(x), "%d.json" % y), "w") as fh:
        json.dump({"z": Z, "x": x, "y": y, "e": lay["extent"] if lay else 4096, "w": ways}, fh, ensure_ascii=False, separators=(",", ":"))
    return len(ways)


def main():
    tj = json.loads(get("https://tiles.openfreemap.org/planet"))
    tpl = tj["tiles"][0]
    jobs = [(x, y) for x in range(tx(W), tx(E) + 1) for y in range(ty(N), ty(S) + 1)]
    print("dlaždic:", len(jobs), "zdroj:", tpl, flush=True)
    done = 0
    with cf.ThreadPoolExecutor(4) as ex:  # šetrně: 4 naráz
        for fut in cf.as_completed([ex.submit(one, tpl, x, y) for x, y in jobs]):
            fut.result(); done += 1
            if done % 50 == 0: print(done, "/", len(jobs), flush=True)
    with open(os.path.join(OUT, "..", "info.json"), "w") as fh:
        json.dump({"z": Z, "bbox": [S, W, N, E], "zdroj": tpl, "stazeno": time.strftime("%Y-%m-%d"),
                   "licence": "© OpenStreetMap contributors (ODbL), © OpenMapTiles, OpenFreeMap"}, fh, ensure_ascii=False)
    print("hotovo", done, flush=True)


if __name__ == "__main__":
    main()
