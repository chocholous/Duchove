"""Mapa celé ČR přímo v aplikaci: z výřezu OpenStreetMap udělá dlaždice mapa/14/x/y.bin.

Hra si stáhne jen 1–4 dlaždice kolem hráče (asi 2,4 × 2,4 km) z vlastního webu, bez mapových serverů.
Dlaždice je binární a zkomprimovaná gzipem přes zopfli (o ~6 % menší, hra ji rozbalí jako běžný gzip),
souřadnice 0–1023 (asi 2,4 m). V datech je jen to, co hra používá.

Zjednodušení a výběr (změřeno na vzorku dlaždic, viz komentáře u funkcí):
  cesty 0,5 bodu (1,2 m) · domy do bloků, zjednodušení 0,8 bodu (1,9 m), bez staveb pod 25 m²
  vodní plochy 2 body (4,7 m), bez druhu · toky 2 body, jen řeky/kanály a potoky (strouhy a stoky ne) · body pro úkoly všechny

Obsah dlaždice (vše čísla varint, souřadnice rozdílově a zigzag):
  hlavička "LD1", z, x, y, rozměr
  jména:   počet, pak každé jméno jako délka + UTF-8
  cesty:   typ, příznaky (varint), jméno (0 = žádné, jinak pořadí v tabulce + 1), body
           příznaky: 1 má jméno/číslo, 2 most, 4 tunel, 8 uvnitř, 16 zákaz vstupu, 32 krytá, 64 pěší zákaz,
                     128 pěší výslovně smí, 256 přechod
  domy:    obrysy bloků (sousední domy sloučené, kůlny pod 12 m² vynechané)
  voda:    obrys (oříznutý na dlaždici)
  toky:    druh (0 řeka nebo kanál, 1 potok), body
  body:    druh, x, y   (lavička, lampa, hřiště, strom, kříž…)
Číselníky (HW, SVC, WATER, RIVER, POI) musí sedět s index.html.

Spuštění:
  pip install osmium shapely
  curl -LO https://download.openstreetmap.fr/extracts/europe/czech_republic-latest.osm.pbf
  python3 nastroje/mapa_cr.py czech_republic-latest.osm.pbf
Data: © OpenStreetMap contributors (ODbL).
"""
import gzip, json, math, os, sys, time
import zopfli.gzip as zgz
from collections import defaultdict
import osmium
from shapely.geometry import Polygon
from shapely.ops import unary_union

Z = 14
E = 1024                        # body na dlaždici (asi 2,4 m)
N = 2 ** Z
OUT = os.environ.get("MAPA_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapa", str(Z))

HW = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service",
      "pedestrian", "track", "footway", "path", "cycleway", "bridleway", "steps", "road",
      "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"]
RIVER = {"river": 0, "canal": 0, "stream": 1, "brook": 1}   # strouhy a stoky (ditch, drain) hra nekreslí
POIK = ["bench", "street_lamp", "playground", "picnic_table", "waste_basket", "post_box", "fire_hydrant", "drinking_water",
        "bicycle_parking", "shelter", "information", "gate", "fountain", "tree", "cross", "shrine", "church"]
POI = {("amenity", "bench"): "bench", ("highway", "street_lamp"): "street_lamp", ("leisure", "playground"): "playground",
       ("leisure", "picnic_table"): "picnic_table", ("amenity", "waste_basket"): "waste_basket", ("amenity", "post_box"): "post_box",
       ("emergency", "fire_hydrant"): "fire_hydrant", ("amenity", "drinking_water"): "drinking_water",
       ("amenity", "bicycle_parking"): "bicycle_parking", ("amenity", "shelter"): "shelter", ("tourism", "information"): "information",
       ("barrier", "gate"): "gate", ("amenity", "fountain"): "fountain", ("natural", "tree"): "tree",
       ("historic", "wayside_cross"): "cross", ("historic", "wayside_shrine"): "shrine", ("amenity", "place_of_worship"): "church"}
HWI = {k: i for i, k in enumerate(HW)}; RI = RIVER; PI = {k: i for i, k in enumerate(POIK)}


def gxy(lon, lat):
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    return (int(round((lon + 180.0) / 360.0 * N * E)), int(round((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * N * E)))


def dedup(pts):
    out = []
    for p in pts:
        if not out or out[-1] != p: out.append(p)
    return out


def simplify(pts, tol):
    if len(pts) < 3: return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True; st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop(); ax, ay = pts[a]; bx, by = pts[b]; dx, dy = bx - ax, by - ay; L = math.hypot(dx, dy) or 1e-9
        best, bi = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > best: best, bi = d, i
        if best > tol: keep[bi] = True; st.append((a, bi)); st.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def line_runs(pts):
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
    for ins, inter in ((lambda c: c[0] >= x0, lambda a, b: ix(a, b, x0)), (lambda c: c[0] <= x1, lambda a, b: ix(a, b, x1)),
                       (lambda c: c[1] >= y0, lambda a, b: iy(a, b, y0)), (lambda c: c[1] <= y1, lambda a, b: iy(a, b, y1))):
        if not p: break
        p = clip(p, ins, inter)
    return dedup(p)


class Tiles:
    def __init__(self):
        self.t = defaultdict(lambda: {"w": [], "b": [], "a": [], "r": [], "p": []})
        self.count = defaultdict(int)

    def road(self, tags, pts):
        hw = tags.get("highway"); fl = 0
        if tags.get("ref") or tags.get("name"): fl |= 1
        if tags.get("bridge", "no") != "no": fl |= 2
        if tags.get("tunnel", "no") != "no": fl |= 4
        if tags.get("indoor", "no") != "no": fl |= 8
        if tags.get("access") in ("private", "no", "customers", "delivery", "agricultural", "forestry"): fl |= 16
        if tags.get("covered", "no") != "no": fl |= 32
        if tags.get("foot") == "no": fl |= 64
        if tags.get("foot") in ("yes", "designated", "permissive"): fl |= 128
        if tags.get("footway") == "crossing": fl |= 256   # z druhů chodníku hra používá jen přechod
        name = tags.get("name") or ""
        pts = simplify(dedup(pts), 0.5)
        for key, rl in line_runs(pts).items():
            for r in rl: self.t[key]["w"].append((HWI[hw], fl, name, r))
        self.count["w"] += 1

    def waterway(self, kind, pts):
        pts = simplify(dedup(pts), 2.0)
        for key, rl in line_runs(pts).items():
            for r in rl: self.t[key]["r"].append((RI[kind], r))
        self.count["r"] += 1

    def building(self, pts):
        pts = dedup(pts)
        if len(pts) < 4: return
        cx = sum(p[0] for p in pts) // len(pts); cy = sum(p[1] for p in pts) // len(pts)
        self.t[(cx // E, cy // E)]["b"].append(pts[:-1])
        self.count["b"] += 1

    def water(self, kind, pts):
        pts = simplify(dedup(pts), 2.0)
        if len(pts) < 4: return
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        for tx in range(min(xs) // E, max(xs) // E + 1):
            for ty in range(min(ys) // E, max(ys) // E + 1):
                c = clip_poly(pts[:-1], tx * E - 4, ty * E - 4, (tx + 1) * E + 4, (ty + 1) * E + 4)
                if len(c) >= 3: self.t[(tx, ty)]["a"].append(c)
        self.count["a"] += 1

    def poi(self, kind, lon, lat):
        x, y = gxy(lon, lat)
        self.t[(x // E, y // E)]["p"].append((PI[kind], x, y))
        self.count["p"] += 1


def blocks(polys, unit_m2):
    """sousední domy do bloků: rozšířit o 0,4 bodu, sloučit, zase zúžit, zjednodušit o 0,8 bodu; stavby pod 25 m² pryč"""
    ps = []
    for pts in polys:
        if len(pts) < 3: continue
        try:
            g = Polygon(pts)
            if not g.is_valid: g = g.buffer(0)
            if g.area * unit_m2 < 25: continue
            ps.append(g.buffer(0.4, join_style=2))
        except Exception:
            continue
    if not ps: return []
    u = unary_union(ps).buffer(-0.4, join_style=2).simplify(0.8)
    out = []
    for g in getattr(u, "geoms", [u]):
        if g.is_empty or g.geom_type != "Polygon": continue
        ring = dedup([(int(round(x)), int(round(y))) for x, y in list(g.exterior.coords)[:-1]])
        if len(ring) >= 3: out.append(ring)
    return out


def vi(n, out):
    while True:
        b = n & 127; n >>= 7
        if n: out.append(b | 128)
        else: out.append(b); return


def pts_enc(pts, ox, oy, out):
    vi(len(pts), out); px, py = ox, oy
    for x, y in pts:
        dx, dy = x - px, y - py
        vi((dx << 1) ^ (dx >> 63), out); vi((dy << 1) ^ (dy >> 63), out)
        px, py = x, y


def tile_bytes(tx, ty, d, unit_m2):
    out = bytearray(b"LD1"); vi(Z, out); vi(tx, out); vi(ty, out); vi(E, out)
    ox, oy = tx * E, ty * E
    names = sorted({w[3] for w in d["w"] if w[3]}); ni = {n: i + 1 for i, n in enumerate(names)}
    vi(len(names), out)
    for n in names: b = n.encode("utf-8"); vi(len(b), out); out += b
    vi(len(d["w"]), out)
    for hw, fl, name, r in d["w"]: out.append(hw); vi(fl, out); vi(ni.get(name, 0), out); pts_enc(r, ox, oy, out)
    bl = blocks(d["b"], unit_m2); d["b"] = None
    vi(len(bl), out)
    for r in bl: pts_enc(r, ox, oy, out)
    vi(len(d["a"]), out)
    for r in d["a"]: pts_enc(r, ox, oy, out)
    vi(len(d["r"]), out)
    for k, r in d["r"]: out.append(k); pts_enc(r, ox, oy, out)
    vi(len(d["p"]), out)
    for k, x, y in d["p"]: out.append(k); vi(x - ox, out); vi(y - oy, out)
    return bytes(out)


def main(src):
    T = Tiles(); t0 = time.time(); seen = 0
    keys = ("highway", "building", "waterway", "natural", "landuse", "water", "amenity", "leisure", "emergency", "tourism", "barrier", "historic")
    fp = osmium.FileProcessor(src).with_locations("flex_mem").with_areas().with_filter(osmium.filter.KeyFilter(*keys))
    for o in fp:
        seen += 1
        if seen % 2000000 == 0: print(seen, "objektů", int(time.time() - t0), "s", dict(T.count), flush=True)
        tags = o.tags
        try:
            if o.is_node():
                for (k, v), kind in POI.items():
                    if tags.get(k) == v: T.poi(kind, o.location.lon, o.location.lat); break
            elif o.is_way():
                hw = tags.get("highway")
                if hw in HWI and tags.get("area") != "yes":
                    T.road(tags, [gxy(n.lon, n.lat) for n in o.nodes])
                elif tags.get("waterway") in RI and tags.get("tunnel", "no") == "no":
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
    print("načteno", seen, "objektů za", int(time.time() - t0), "s", dict(T.count), flush=True)
    total = 0; k = 0
    for (tx, ty) in list(T.t.keys()):
        d = T.t.pop((tx, ty))
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + 0.5) / N))))
        m = 40075016.686 * math.cos(math.radians(lat)) / (N * E)   # metrů na bod
        data = zgz.compress(tile_bytes(tx, ty, d, m * m), numiterations=8)
        os.makedirs(os.path.join(OUT, str(tx)), exist_ok=True)
        with open(os.path.join(OUT, str(tx), "%d.bin" % ty), "wb") as fh: fh.write(data)
        total += len(data); k += 1
        if k % 5000 == 0: print("zapsáno", k, "dlaždic,", round(total / 1e6, 1), "MB", int(time.time() - t0), "s", flush=True)
    with open(os.path.join(OUT, "..", "info.json"), "w") as fh:
        json.dump({"z": Z, "e": E, "format": "LD1 gzip (zopfli)", "dlazdic": k, "velikost_mb": round(total / 1e6, 1), "zdroj": os.path.basename(src),
                   "zpracovano": time.strftime("%Y-%m-%d"), "licence": "© OpenStreetMap contributors (ODbL)"}, fh, ensure_ascii=False)
    print("hotovo:", k, "dlaždic,", round(total / 1e6, 1), "MB", int(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
