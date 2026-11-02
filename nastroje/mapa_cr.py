"""Mapa celé ČR přímo v aplikaci: z výřezu OpenStreetMap udělá dlaždice mapa/13/x/y.json.

Hra si pak stáhne jen 1–4 dlaždice kolem hráče z vlastního webu, bez mapových serverů.
V každé dlaždici (asi 4,8 × 4,8 km, souřadnice 0–4096 ≈ 1,2 m):
  w: cesty a ulice   [typ, jméno, příznaky, služba/chodník, souřadnice]
     příznaky: b most, t tunel, i uvnitř, a zákaz vstupu, c krytá, f pěší zákaz, F pěší výslovně smí
  b: domy            [souřadnice obrysu]
  a: vodní plochy    [druh, souřadnice obrysu]   (oříznuté na dlaždici)
  r: vodní toky      [druh, souřadnice]
  p: body pro úkoly  [druh, x, y]                 (lavička, lampa, hřiště, strom…)
Souřadnice jsou celá čísla v rámci dlaždice, zapsané rozdílově: x0,y0,dx1,dy1,…

Spuštění:
  pip install osmium
  curl -LO https://download.openstreetmap.fr/extracts/europe/czech_republic-latest.osm.pbf
  python3 nastroje/mapa_cr.py czech_republic-latest.osm.pbf
Data: © OpenStreetMap contributors (ODbL).
"""
import json, math, os, sys, time
from collections import defaultdict
import osmium

Z = 13
E = 4096                       # body na dlaždici
N = 2 ** Z
OUT = os.environ.get("MAPA_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapa", str(Z))

ROAD = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
        "service", "pedestrian", "track", "footway", "path", "cycleway", "bridleway", "steps", "road",
        "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
WATERWAY = {"river", "stream", "canal", "brook", "ditch", "drain"}
POI = {("amenity", "bench"): "bench", ("highway", "street_lamp"): "street_lamp", ("leisure", "playground"): "playground",
       ("leisure", "picnic_table"): "picnic_table", ("amenity", "waste_basket"): "waste_basket", ("amenity", "post_box"): "post_box",
       ("emergency", "fire_hydrant"): "fire_hydrant", ("amenity", "drinking_water"): "drinking_water",
       ("amenity", "bicycle_parking"): "bicycle_parking", ("amenity", "shelter"): "shelter", ("tourism", "information"): "information",
       ("barrier", "gate"): "gate", ("amenity", "fountain"): "fountain", ("natural", "tree"): "tree",
       ("historic", "wayside_cross"): "cross", ("historic", "wayside_shrine"): "shrine", ("amenity", "place_of_worship"): "church"}


def gxy(lon, lat):
    """zeměpisné souřadnice -> celé body v celém světě na úrovni Z"""
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    x = (lon + 180.0) / 360.0 * N * E
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * N * E
    return int(round(x)), int(round(y))


def dedup(pts):
    out = []
    for p in pts:
        if not out or out[-1] != p: out.append(p)
    return out


def simplify(pts, tol):
    """Douglas-Peucker na celých bodech"""
    if len(pts) < 3: return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop()
        ax, ay = pts[a]; bx, by = pts[b]; dx, dy = bx - ax, by - ay; L = math.hypot(dx, dy) or 1e-9
        best, bi = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > best: best, bi = d, i
        if best > tol: keep[bi] = True; st.append((a, bi)); st.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def enc(pts, tx, ty):
    """lokální souřadnice dlaždice, rozdílově"""
    out = []; px = py = 0
    for i, (x, y) in enumerate(pts):
        lx, ly = x - tx * E, y - ty * E
        if i == 0: out += [lx, ly]
        else: out += [lx - px, ly - py]
        px, py = lx, ly
    return out


def line_runs(pts):
    """rozdělí čáru na kusy podle dlaždic, kterými prochází (úsek patří do všech dlaždic, kterých se dotkne)"""
    runs = defaultdict(list)
    for i in range(1, len(pts)):
        (x1, y1), (x2, y2) = pts[i - 1], pts[i]
        for tx in range(min(x1, x2) // E, max(x1, x2) // E + 1):
            for ty in range(min(y1, y2) // E, max(y1, y2) // E + 1):
                rl = runs[(tx, ty)]
                if rl and rl[-1][-1] == (x1, y1): rl[-1].append((x2, y2))
                else: rl.append([(x1, y1), (x2, y2)])
    return runs


def clip_poly(pts, x0, y0, x1, y1):
    """Sutherland-Hodgman: obrys oříznutý na obdélník dlaždice"""
    def clip(poly, inside, inter):
        out = []
        for i in range(len(poly)):
            cur, prev = poly[i], poly[i - 1]
            if inside(cur):
                if not inside(prev): out.append(inter(prev, cur))
                out.append(cur)
            elif inside(prev): out.append(inter(prev, cur))
        return out
    def ix(a, b, x): t = (x - a[0]) / ((b[0] - a[0]) or 1e-9); return (x, int(round(a[1] + (b[1] - a[1]) * t)))
    def iy(a, b, y): t = (y - a[1]) / ((b[1] - a[1]) or 1e-9); return (int(round(a[0] + (b[0] - a[0]) * t)), y)
    p = pts
    for f in (lambda q: (lambda c: c[0] >= x0, lambda a, b: ix(a, b, x0)), lambda q: (lambda c: c[0] <= x1, lambda a, b: ix(a, b, x1)),
              lambda q: (lambda c: c[1] >= y0, lambda a, b: iy(a, b, y0)), lambda q: (lambda c: c[1] <= y1, lambda a, b: iy(a, b, y1))):
        if not p: break
        ins, inter = f(None); p = clip(p, ins, inter)
    return dedup(p)


class Tiles:
    def __init__(self):
        self.t = defaultdict(lambda: {"w": [], "b": [], "a": [], "r": [], "p": []})
        self.count = defaultdict(int)

    def road(self, tags, pts):
        hw = tags.get("highway")
        fl = ""
        if tags.get("bridge", "no") != "no": fl += "b"
        if tags.get("tunnel", "no") != "no": fl += "t"
        if tags.get("indoor", "no") != "no": fl += "i"
        if tags.get("access") in ("private", "no", "customers", "delivery", "agricultural", "forestry"): fl += "a"
        if tags.get("covered", "no") != "no": fl += "c"
        if tags.get("foot") == "no": fl += "f"
        if tags.get("foot") in ("yes", "designated", "permissive"): fl += "F"
        nm = tags.get("ref") or tags.get("name") or ""
        svc = tags.get("footway") if hw == "footway" else (tags.get("service") or "")
        pts = simplify(dedup(pts), 0.6)
        for (tx, ty), rl in line_runs(pts).items():
            for r in rl: self.t[(tx, ty)]["w"].append([hw, nm, fl, svc or "", enc(r, tx, ty)])
        self.count["w"] += 1

    def waterway(self, kind, pts):
        pts = simplify(dedup(pts), 1.2)
        for (tx, ty), rl in line_runs(pts).items():
            for r in rl: self.t[(tx, ty)]["r"].append([kind, enc(r, tx, ty)])
        self.count["r"] += 1

    def building(self, pts):
        pts = simplify(dedup(pts), 0.8)
        if len(pts) < 4: return
        cx = sum(p[0] for p in pts) // len(pts); cy = sum(p[1] for p in pts) // len(pts)
        tx, ty = cx // E, cy // E
        self.t[(tx, ty)]["b"].append(enc(pts[:-1], tx, ty))   # bez opakovaného posledního bodu
        self.count["b"] += 1

    def water(self, kind, pts):
        pts = simplify(dedup(pts), 1.5)
        if len(pts) < 4: return
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        for tx in range(min(xs) // E, max(xs) // E + 1):
            for ty in range(min(ys) // E, max(ys) // E + 1):
                c = clip_poly(pts[:-1], tx * E - 8, ty * E - 8, (tx + 1) * E + 8, (ty + 1) * E + 8)
                if len(c) >= 3: self.t[(tx, ty)]["a"].append([kind, enc(c, tx, ty)])
        self.count["a"] += 1

    def poi(self, kind, lon, lat):
        x, y = gxy(lon, lat); tx, ty = x // E, y // E
        self.t[(tx, ty)]["p"].append([kind, x - tx * E, y - ty * E])
        self.count["p"] += 1


def main(src):
    T = Tiles(); t0 = time.time(); seen = 0
    keys = ("highway", "building", "waterway", "natural", "landuse", "water", "amenity", "leisure", "emergency", "tourism", "barrier", "historic")
    fp = osmium.FileProcessor(src).with_locations("flex_mem").with_areas().with_filter(osmium.filter.KeyFilter(*keys))
    for o in fp:
        seen += 1
        if seen % 500000 == 0: print(seen, "objektů", int(time.time() - t0), "s", dict(T.count), flush=True)
        tags = o.tags
        try:
            if o.is_node():
                for (k, v), kind in POI.items():
                    if tags.get(k) == v: T.poi(kind, o.location.lon, o.location.lat); break
            elif o.is_way():
                hw = tags.get("highway")
                if hw in ROAD and tags.get("area") != "yes":
                    T.road(tags, [gxy(n.lon, n.lat) for n in o.nodes])
                elif tags.get("waterway") in WATERWAY and tags.get("tunnel", "no") == "no":
                    T.waterway(tags.get("waterway"), [gxy(n.lon, n.lat) for n in o.nodes])
                if tags.get("leisure") == "playground" and len(o.nodes) > 2:
                    n0 = o.nodes[0]; T.poi("playground", n0.lon, n0.lat)
            elif o.is_area():
                b = tags.get("building")
                isw = (tags.get("natural") == "water" or tags.get("landuse") in ("reservoir", "basin") or tags.get("waterway") == "riverbank")
                if not (b and b != "no") and not isw: continue
                for ring in o.outer_rings():
                    pts = [gxy(n.lon, n.lat) for n in ring]
                    if b and b != "no": T.building(pts)
                    else: T.water(tags.get("water") or tags.get("natural") or tags.get("landuse") or "water", pts)
        except osmium.InvalidLocationError:
            continue
    print("zpracováno", seen, "objektů za", int(time.time() - t0), "s", dict(T.count), flush=True)
    total = 0
    for (tx, ty), d in T.t.items():
        os.makedirs(os.path.join(OUT, str(tx)), exist_ok=True)
        body = {"z": Z, "x": tx, "y": ty, "e": E}
        body.update({k: v for k, v in d.items() if v})
        s = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(OUT, str(tx), "%d.json" % ty), "w") as fh: fh.write(s)
        total += len(s.encode())
    with open(os.path.join(OUT, "..", "info.json"), "w") as fh:
        json.dump({"z": Z, "e": E, "dlazdic": len(T.t), "velikost_mb": round(total / 1e6, 1), "zdroj": os.path.basename(src),
                   "zpracovano": time.strftime("%Y-%m-%d"), "licence": "© OpenStreetMap contributors (ODbL)"}, fh, ensure_ascii=False)
    print("hotovo:", len(T.t), "dlaždic,", round(total / 1e6, 1), "MB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
