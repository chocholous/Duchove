"""Zabalí dlaždice mapa/14/x/y.bin (výstup mapa_cr.py) do PMTiles po oblastech.

Oblast = dlaždice z9 (asi 50 × 50 km v ČR), tedy 32 × 32 dlaždic z14. Výsledek:
  mapa/cr/9-X-Y.pmtiles   jeden soubor na oblast (PMTiles v3, adresář gzip, dlaždice gzip, typ "jiný")
  mapa/info.json          seznam oblastí s velikostí a rozsahem, aby hra nemusela hádat, které soubory existují

Hra čte ze souboru jen potřebné kousky přes HTTP Range (GitHub Pages je podporuje). Pro hraní bez signálu
si celý soubor oblasti uloží do paměti prohlížeče.

Použití: python3 pmtiles_cr.py VSTUP/14 VYSTUP_MAPA   (VYSTUP_MAPA je složka mapa/)
Potřebuje: pip install pmtiles
"""
import json, math, os, sys, time
from collections import defaultdict
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

Z, RZ = 14, 9
SH = Z - RZ


def lon(x, z): return x / 2 ** z * 360 - 180
def lat(y, z): return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def main(src, out):
    groups = defaultdict(list)
    for xs in os.listdir(src):
        for f in os.listdir(os.path.join(src, xs)):
            x, y = int(xs), int(f.split(".")[0])
            groups[(x >> SH, y >> SH)].append((x, y))
    os.makedirs(os.path.join(out, "cr"), exist_ok=True)
    oblasti = {}; total = 0
    for (rx, ry), tiles in sorted(groups.items()):
        tiles.sort(key=lambda t: zxy_to_tileid(Z, *t))
        name = "%d-%d-%d" % (RZ, rx, ry); path = os.path.join(out, "cr", name + ".pmtiles")
        with open(path, "wb") as fh:
            w = Writer(fh)
            for x, y in tiles:
                with open(os.path.join(src, str(x), "%d.bin" % y), "rb") as t: w.write_tile(zxy_to_tileid(Z, x, y), t.read())
            x0, y0, x1, y1 = rx << SH, ry << SH, (rx + 1) << SH, (ry + 1) << SH
            w.finalize({
                "tile_type": TileType.UNKNOWN, "tile_compression": Compression.GZIP,
                "min_lon_e7": int(lon(x0, Z) * 1e7), "max_lon_e7": int(lon(x1, Z) * 1e7),
                "min_lat_e7": int(lat(y1, Z) * 1e7), "max_lat_e7": int(lat(y0, Z) * 1e7),
                "center_zoom": Z, "center_lon_e7": int(lon((x0 + x1) / 2, Z) * 1e7), "center_lat_e7": int(lat((y0 + y1) / 2, Z) * 1e7),
            }, {"format": "LD1", "attribution": "© OpenStreetMap contributors", "license": "ODbL"})
        n = os.path.getsize(path); total += n
        oblasti[name] = {"b": n, "d": len(tiles)}
    info_p = os.path.join(out, "info.json")
    info = json.load(open(info_p)) if os.path.exists(info_p) else {}
    info.update({"z": Z, "oblast_z": RZ, "format": "LD1 gzip (zopfli) v PMTiles v3", "soubory": "cr/{oblast}.pmtiles",
                 "oblasti": oblasti, "velikost_mb": round(total / 1e6, 1), "dlazdic": sum(v["d"] for v in oblasti.values()),
                 "zabaleno": time.strftime("%Y-%m-%d"), "licence": "© OpenStreetMap contributors (ODbL)"})
    with open(info_p, "w") as fh: json.dump(info, fh, ensure_ascii=False, separators=(",", ":"))
    print(len(oblasti), "oblastí,", round(total / 1e6, 1), "MB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
