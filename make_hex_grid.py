"""
make_hex_grid.py — Genera la malla ESTÁTICA de hexágonos H3 (res 8) que cubre
Ciudad de México y Estado de México completos, con numeración HEX-XXXXX fija.

Se corre UNA sola vez (o cuando se quiera regenerar) y el resultado se comitea:
    data/hex_grid_cdmx_edomex.csv   (hex_id, hex_code)

La numeración es determinista: hexes ordenados por hex_id (string H3 estable),
numerados secuencialmente. Mientras este archivo no se regenere, un hexágono
conserva su código para siempre, sin importar los datos de cada build.

Fuente de límites estatales: angelnmara/geojson (INEGI simplificado).

Uso:  python make_hex_grid.py
"""
import csv
import json
import os
import urllib.request

import h3

GEOJSON_URL = 'https://raw.githubusercontent.com/angelnmara/geojson/master/mexicoHigh.json'
STATES      = {'México', 'Ciudad de México'}
H3_RES      = 8
OUT         = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'data', 'hex_grid_cdmx_edomex.csv')


def rings_latlng(coords):
    """GeoJSON ring [[lng,lat],...] → [(lat,lng),...]"""
    return [(c[1], c[0]) for c in coords]


def cells_for_geometry(geom):
    cells = set()
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
    for poly in polys:
        outer = rings_latlng(poly[0])
        holes = [rings_latlng(r) for r in poly[1:]]
        shape = h3.LatLngPoly(outer, *holes)
        cells |= set(h3.polygon_to_cells(shape, H3_RES))
    return cells


def main():
    with urllib.request.urlopen(GEOJSON_URL, timeout=60) as r:
        data = json.loads(r.read().decode('utf-8'))

    cells = set()
    for feat in data['features']:
        name = feat['properties'].get('name', '')
        if name in STATES:
            c = cells_for_geometry(feat['geometry'])
            print(f'  {name}: {len(c):,} hexes')
            cells |= c

    ordered = sorted(cells)   # hex_id es estable → numeración estable
    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['hex_id', 'hex_code'])
        for i, hx in enumerate(ordered, 1):
            w.writerow([hx, f'HEX-{i:05d}'])
    print(f'✅ {len(ordered):,} hexes → {OUT}')


if __name__ == '__main__':
    main()
