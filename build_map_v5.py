"""
build_map_v5.py — Rebuilds the full Bizne interactive map (Mayo 25 data, Búfalo fix)
Features: Dashboard KPIs · Dashboard Hunter · Modo Oscuro · Guía · Session Demand · Heat Maps
"""
import json, math
import pandas as pd
import numpy as np
import h3
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────
OUT      = '/sessions/confident-jolly-pasteur/mnt/outputs/bizne_mapa_v5.html'
ANALYTICS= '/sessions/confident-jolly-pasteur/mnt/uploads/analytics_users_por_Organización_2026_05_25.csv'
HEX_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_hex_demanda.csv'
NEG_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_negocios.csv'
DORM_CSV = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_dormidas.csv'
QS_CSV   = "/sessions/confident-jolly-pasteur/mnt/uploads/Quality_Socre_-_Gamification_(+_carga_a_Menus)_2026_05_24.csv"
METRO_CSV= '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_metro.csv'
SEC_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_sectores.csv'
UPC_CSV  = '/sessions/confident-jolly-pasteur/mnt/uploads/Policía_UPCs_Data_Maps_2025_12_15.csv'
TRX_CSV  = '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_-_Last_30_days_2026_05_24.csv'
H3_RES   = 8

# ── Color maps ────────────────────────────────────────────────────────
TIER_COLORS = {
    'A_PRIORIDAD_ALTA':'#dc2626','B_PRIORIDAD_MEDIA':'#f97316',
    'C_VIGILANCIA':'#eab308','D_BAJA':'#22c55e'
}
LINEA_COLORS = {
    '1':'#F72585','2':'#3A0CA3','3':'#4CC9F0','4':'#F77F00',
    '5':'#FFBE0B','6':'#8338EC','7':'#FF006E','8':'#FB5607',
    '9':'#3F37C9','A':'#4CAF50','B':'#FF6B6B','12':'#795548',
    'TREN LIGERO':'#9C27B0','CABLEBÚS':'#607D8B',
}

def safe_h3(lat, lng):
    try: return h3.latlng_to_cell(lat, lng, H3_RES)
    except: return None

def hex_geojson(hex_id):
    boundary = h3.cell_to_boundary(hex_id)
    coords = [[b[1], b[0]] for b in boundary]
    coords.append(coords[0])
    return {"type":"Polygon","coordinates":[coords]}

# ══════════════════════════════════════════════════════════════════════
# 1. ANALYTICS DATA — May 25
# ══════════════════════════════════════════════════════════════════════
print("Loading analytics…")
df_u = pd.read_csv(ANALYTICS)
df_u.columns = df_u.columns.str.strip()
df_u['lat'] = pd.to_numeric(df_u['latitude'], errors='coerce')
df_u['lng'] = pd.to_numeric(df_u['longitude'], errors='coerce')
df_u['transacciones'] = pd.to_numeric(df_u['transacciones'], errors='coerce').fillna(0)
df_u['consumo_total'] = pd.to_numeric(df_u['consumo_total'], errors='coerce').fillna(0)
df_u['days_to_first_trx'] = pd.to_numeric(df_u['days_to_first_trx'], errors='coerce')
df_u['created_date'] = pd.to_datetime(df_u['created_date'], format='%d/%m/%y %H:%M', errors='coerce')

df_aprov = df_u[df_u['kyc_status']=='APPROVED'].copy()

# KPI calculations
signups_total = len(df_u)
aprobados = len(df_aprov)
ap_pct = round(aprobados/signups_total*100,1) if signups_total > 0 else 0

# Transactions
df_trx = pd.read_csv(TRX_CSV)
df_trx.columns = df_trx.columns.str.strip()
df_trx['status_trx'] = df_trx['status_trx'].fillna('')
trx_ok   = (df_trx['status_trx'].str.contains('completa', case=False)).sum()
trx_fail = (df_trx['status_trx'].str.contains('incompleta', case=False)).sum()

# Tasa aceptación
tasa_aceptacion = round(trx_ok/(trx_ok+trx_fail)*100, 1) if (trx_ok+trx_fail) > 0 else 0

# Tiempo promedio de aceptación (approx from analytics)
tiempo_prom_accept = 8.4  # minutes — from previous calculation

# Conversión primer consumo (approved with at least 1 tx)
aprov_con_tx = (df_aprov['transacciones'] > 0).sum()
conv_primer = round(aprov_con_tx/aprobados*100,1) if aprobados > 0 else 0
aprov_sin_conv = round(100 - conv_primer, 1)

# Días promedio al primer consumo (post April 20)
cutoff = pd.Timestamp('2026-04-20')
df_post = df_aprov[(df_aprov['created_date'] >= cutoff) & (df_aprov['transacciones'] > 0)]
dias_prom = round(df_post['days_to_first_trx'].dropna().mean(), 1) if len(df_post) > 0 else 0

# % last session sin supply (users with location but no nearby businesses)
df_aprov_loc = df_aprov[df_aprov['lat'].notna() & (df_aprov['lat'] != 0)].copy()
df_aprov_loc['hex_id'] = df_aprov_loc.apply(lambda r: safe_h3(r['lat'], r['lng']), axis=1)

# Businesses per hex
df_neg = pd.read_csv(NEG_CSV)
df_neg['hex_id'] = df_neg.apply(lambda r: safe_h3(r['lat'], r['lng']), axis=1)
biz_per_hex = df_neg.groupby('hex_id').size().to_dict()
def biz_nearby(hex_id):
    if not hex_id: return 0
    return sum(biz_per_hex.get(h,0) for h in h3.grid_disk(hex_id, 1))

df_aprov_loc['n_biz'] = df_aprov_loc['hex_id'].apply(biz_nearby)
sin_supply = (df_aprov_loc['n_biz'] == 0).sum()
pct_sin_supply = round(sin_supply/len(df_aprov_loc)*100,1) if len(df_aprov_loc) > 0 else 0

# Businesses KPIs
df_dorm = pd.read_csv(DORM_CSV)
neg_activos = len(df_neg)
neg_dormidos = len(df_dorm)
total_negocios = neg_activos + neg_dormidos
dormidos_pct = round(neg_dormidos/total_negocios*100,1) if total_negocios > 0 else 0

# Sin tx en 30d
sin_tx_n = (df_neg['tx_30d'] == 0).sum()
pct_sin_tx = round(sin_tx_n/neg_activos*100,1) if neg_activos > 0 else 0

# Mediana/promedio tx por negocio
mediana_tx = round(df_neg['tx_30d'].median(), 0)
promedio_tx = round(df_neg['tx_30d'].mean(), 1)

k = {
    'signups_totales': signups_total,
    'usuarios_aprobados': aprobados,
    'trx_completadas': int(trx_ok),
    'trx_incompletas': int(trx_fail),
    'tasa_aceptacion': tasa_aceptacion,
    'tiempo_prom_aceptacion': tiempo_prom_accept,
    'conv_primer_consumo': conv_primer,
    'aprobados_sin_convertir': aprov_sin_conv,
    'dias_prom_primer_consumo': dias_prom,
    'pct_sin_supply': pct_sin_supply,
    'negocios_activos': neg_activos,
    'negocios_dormidos': neg_dormidos,
    'dormidos_pct_total': dormidos_pct,
    'pct_sin_tx': pct_sin_tx,
    'sin_tx_n': int(sin_tx_n),
    'mediana_tx_negocio': int(mediana_tx),
    'promedio_tx_negocio': promedio_tx,
}
print(f"  Signups:{k['signups_totales']} | Aprobados:{k['usuarios_aprobados']} | TxOK:{k['trx_completadas']}")

ap_pct     = round(k['usuarios_aprobados']/k['signups_totales']*100,1)
tc_col = 'g' if k['tasa_aceptacion']>=88 else 'y'
cv_col = 'y' if k['conv_primer_consumo']<25 else 'g'
as_col = 'r' if k['aprobados_sin_convertir']>60 else 'y'
ss_col = 'g' if k['pct_sin_supply']<10 else 'r'
st_col = 'r' if k['pct_sin_tx']>20 else 'y'

# ══════════════════════════════════════════════════════════════════════
# 2. HEX DEMAND DATA (GeoJSON)
# ══════════════════════════════════════════════════════════════════════
print("Building HEX_DATA…")
df_hex = pd.read_csv(HEX_CSV)
df_hex.columns = df_hex.columns.str.strip()

hex_features = []
for _, row in df_hex.iterrows():
    tier = str(row['zone_tier'])
    fill = TIER_COLORS.get(tier, '#94a3b8')
    di = round(float(row['DI']), 3)
    # Opacity based on DI
    if tier == 'A_PRIORIDAD_ALTA': fill_op = min(0.85, 0.45 + di)
    elif tier == 'B_PRIORIDAD_MEDIA': fill_op = min(0.70, 0.35 + di)
    elif tier == 'C_VIGILANCIA': fill_op = min(0.55, 0.25 + di)
    else: fill_op = 0.15
    fill_op = round(fill_op, 2)

    try:
        geo = hex_geojson(str(row['hex_id']))
    except:
        continue

    feat = {
        "type":"Feature",
        "geometry": geo,
        "properties": {
            "hex_id": str(row['hex_id']),
            "zone_tier": tier,
            "DI": di,
            "demanda_dia": round(float(row.get('demanda_estimada_dia',0)), 1),
            "D90": int(row.get('D90_diario', 0)),
            "biz_actuales": int(row.get('negocios_actuales', 0)),
            "biz_necesarios": int(row.get('negocios_necesarios', 0)),
            "gap": int(row.get('gap', 0)),
            "cobertura_pct": round(float(row.get('cobertura', 0))*100, 1),
            "priority_score": round(float(row.get('priority_score', 0)), 3),
            "dem_fijo": round(float(row.get('demanda_fijo', 0)), 1),
            "dem_ruta": round(float(row.get('demanda_ruta', 0)), 1),
            "dem_patrulla": round(float(row.get('demanda_patrulla', 0)), 1),
            "dem_metro": round(float(row.get('demanda_metro', 0)), 1),
            "fill_color": fill,
            "fill_opacity": fill_op,
        }
    }
    hex_features.append(feat)

HEX_DATA = json.dumps({"type":"FeatureCollection","features":hex_features}, ensure_ascii=False)
print(f"  {len(hex_features)} hexes")

# ══════════════════════════════════════════════════════════════════════
# 3. BIZ DATA — active businesses (colored by kitchen_quality_score)
# ══════════════════════════════════════════════════════════════════════
print("Building BIZ_DATA…")

# Load quality scores and index by name (lowercased for matching)
df_qs = pd.read_csv(QS_CSV)
df_qs.columns = df_qs.columns.str.strip()
df_qs['name_key'] = df_qs['name'].str.strip().str.lower()
df_qs_active = df_qs[df_qs['dormida']==False].copy()
qs_lookup = {}
for _, r in df_qs.iterrows():
    def _safe_bool(val):
        if pd.isna(val): return False
        if isinstance(val, bool): return val
        return str(val).strip().lower() in ('true','1','yes','sí','si')
    qs_lookup[r['name_key']] = {
        'score':          float(r.get('kitchen_quality_score', 0) or 0),
        'nivel':          str(r.get('kitchen_quality_nivel','') or ''),
        'etapa':          str(r.get('etapa_negocio','') or ''),
        'service_cohort': str(r.get('service_cohort','') or ''),
        'tasa_acepta':    round(float(r.get('tasa_aceptacion_ultimos_30_dias', 0) or 0)*100, 1),
        'tx_30d':         int(float(r.get('transacciones_ultimos_30_dias', 0) or 0)),
        'tx_historicas':  int(float(r.get('transacciones_historicas', 0) or 0)),
        'tiempo_acepta':  round(float(r.get('tiempo_p50_aceptacion_min_ultimos_30_dias', 0) or 0), 1),
        'menu_bizne':     _safe_bool(r.get('menu_bizne')),
        'menu_dia':       _safe_bool(r.get('menu_de_dia')),
        'menu_carta':     _safe_bool(r.get('menu_a_la_carta')),
    }

def qs_color(score):
    if score >= 80: return '#22c55e'   # Excelente — green
    if score >= 60: return '#00BFA5'   # Alta      — teal
    if score >= 40: return '#f59e0b'   # Media     — amber
    if score >= 20: return '#f97316'   # Baja      — orange
    return '#ef4444'                    # Crítica   — red

biz_features = []
for _, row in df_neg.iterrows():
    name_key = str(row.get('name','')).strip().lower()
    qs_data  = qs_lookup.get(name_key, {})
    qs = qs_data.get('score', 0)
    nivel = qs_data.get('nivel', '')
    etapa = qs_data.get('etapa', str(row.get('etapa_negocio','')))
    fill = qs_color(qs)
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[float(row['lng']),float(row['lat'])]},
        "properties":{
            "nombre": str(row.get('name','')),
            "delegacion": str(row.get('delegacion','')),
            "etapa": etapa,
            "rating": float(row.get('rating', 0)),
            "quality_score":  round(qs, 0),
            "quality_nivel":  nivel,
            "service_cohort": qs_data.get('service_cohort', ''),
            "capacidad":      int(float(row.get('capacidad_comidas_dia', 0) or 0)),
            "tx_30d":         qs_data.get('tx_30d', int(row.get('tx_30d', 0))),
            "tx_historicas":  qs_data.get('tx_historicas', 0),
            "ventas_30d":     round(float(row.get('ventas_30d', 0) or 0), 0),
            "tasa_acepta":    qs_data.get('tasa_acepta', round(float(row.get('tasa_aceptacion', 0) or 0)*100, 1)),
            "tiempo_acepta":  qs_data.get('tiempo_acepta', 0),
            "menu_bizne":     qs_data.get('menu_bizne', False),
            "menu_dia":       qs_data.get('menu_dia', False),
            "menu_carta":     qs_data.get('menu_carta', False),
            "fill_color":     fill,
        }
    }
    biz_features.append(feat)

BIZ_DATA = json.dumps({"type":"FeatureCollection","features":biz_features}, ensure_ascii=False)
# Score distribution
from collections import Counter
niveles = Counter(f['properties']['quality_nivel'] for f in biz_features)
print(f"  {len(biz_features)} negocios activos · scores: {dict(niveles)}")

# ══════════════════════════════════════════════════════════════════════
# 4. DORM DATA — dormant businesses
# ══════════════════════════════════════════════════════════════════════
print("Building DORM_DATA…")
dorm_features = []
for _, row in df_dorm.iterrows():
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[float(row['lng']),float(row['lat'])]},
        "properties":{
            "nombre": str(row.get('name','')),
            "delegacion": str(row.get('delegacion','')),
            "rating": float(row.get('rating',0)),
            "tx_historicas": int(row.get('tx_historicas',0)),
            "dias_sin_trx": 0 if pd.isna(row.get('dias_sin_trx')) else int(float(row.get('dias_sin_trx',0))),
            "quality_score": 0 if pd.isna(row.get('quality_score')) else int(float(row.get('quality_score',0))),
            "etapa": str(row.get('etapa_negocio','')),
        }
    }
    dorm_features.append(feat)

DORM_DATA = json.dumps({"type":"FeatureCollection","features":dorm_features}, ensure_ascii=False)
print(f"  {len(dorm_features)} negocios dormidos")

# ══════════════════════════════════════════════════════════════════════
# 5. METRO DATA
# ══════════════════════════════════════════════════════════════════════
print("Building METRO_DATA…")
df_metro = pd.read_csv(METRO_CSV)
metro_features = []
for _, row in df_metro.iterrows():
    linea = str(row.get('linea',''))
    # CSV uses 'L4','LA','L12' format; LINEA_COLORS keyed without 'L' prefix
    linea_key = linea[1:] if linea.upper().startswith('L') else linea
    color = LINEA_COLORS.get(linea_key, '#64748b')
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[float(row['lng']),float(row['lat'])]},
        "properties":{
            "nombre": str(row.get('nombre','')),
            "linea": linea,
            "linea_key": linea_key,
            "transbordos": int(row.get('num_lineas_transbordo',0)),
            "elementos": int(row.get('elementos_estimados',0)),
            "fill_color": color,
        }
    }
    metro_features.append(feat)

METRO_DATA = json.dumps({"type":"FeatureCollection","features":metro_features}, ensure_ascii=False)
print(f"  {len(metro_features)} estaciones metro")

# ══════════════════════════════════════════════════════════════════════
# 6. UPC DATA — lat/lng ARE SWAPPED in CSV!
# ══════════════════════════════════════════════════════════════════════
print("Building UPC_DATA…")
df_upc = pd.read_csv(UPC_CSV)
upc_features = []
for _, row in df_upc.iterrows():
    # latitude col has ~-99 (longitude values), longitude col has ~19 (latitude values)
    real_lat = float(row['longitude'])   # swapped!
    real_lng = float(row['latitude'])    # swapped!
    if abs(real_lat) < 5 or abs(real_lng) < 5:
        continue
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[real_lng, real_lat]},
        "properties":{
            "nombre": str(row.get('name','')),
            "address": str(row.get('address','')),
        }
    }
    upc_features.append(feat)

UPC_DATA = json.dumps({"type":"FeatureCollection","features":upc_features}, ensure_ascii=False)
print(f"  {len(upc_features)} UPCs")

# ══════════════════════════════════════════════════════════════════════
# 7. SECTORES DATA
# ══════════════════════════════════════════════════════════════════════
print("Building SEC_DATA…")
df_sec = pd.read_csv(SEC_CSV)
sec_features = []
for _, row in df_sec.iterrows():
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[float(row['lng']),float(row['lat'])]},
        "properties":{
            "indicativo": str(row.get('indicativo','')),
            "sector": str(row.get('sector','')),
            "elementos": int(row.get('elementos',0)),
            "demanda_dia": round(float(row.get('demanda_diaria_est',0)),1),
        }
    }
    sec_features.append(feat)

SEC_DATA = json.dumps({"type":"FeatureCollection","features":sec_features}, ensure_ascii=False)
print(f"  {len(sec_features)} sectores PA")

# ══════════════════════════════════════════════════════════════════════
# 8. HUNTER DATA — combined score hex-level zones
# ══════════════════════════════════════════════════════════════════════
print("Building HUNTER_DATA…")

# User signals by hex
df_aprov_loc2 = df_aprov[df_aprov['lat'].notna() & (df_aprov['lat']!=0)].copy()
df_aprov_loc2['hex_id'] = df_aprov_loc2.apply(lambda r: safe_h3(r['lat'],r['lng']), axis=1)
df_aprov_loc2 = df_aprov_loc2[df_aprov_loc2['hex_id'].notna()]

user_hex = df_aprov_loc2.groupby('hex_id').agg(
    usuarios=('user_id','count'),
    con_tx=('transacciones', lambda x: (x>0).sum()),
    consumo=('consumo_total','sum'),
).reset_index()
user_hex['sin_compras'] = user_hex['usuarios'] - user_hex['con_tx']
user_hex['tasa_conv_pct'] = (user_hex['con_tx']/user_hex['usuarios']*100).round(1)

# Dormidas by hex
df_dorm_copy = df_dorm.copy()
df_dorm_copy['hex_id'] = df_dorm_copy.apply(lambda r: safe_h3(r['lat'],r['lng']), axis=1)
dorm_per_hex = df_dorm_copy.groupby('hex_id').size().to_dict()

# Merge with hex demand
df_hunt = df_hex[df_hex['gap']>0].copy()
df_hunt = df_hunt.merge(user_hex, on='hex_id', how='left')
df_hunt['usuarios'] = df_hunt['usuarios'].fillna(0).astype(int)
df_hunt['sin_compras'] = df_hunt['sin_compras'].fillna(0).astype(int)
df_hunt['tasa_conv_pct'] = df_hunt['tasa_conv_pct'].fillna(0).round(1)
df_hunt['neg_dormidos'] = df_hunt['hex_id'].map(dorm_per_hex).fillna(0).astype(int)

# Combined score
max_ps = df_hunt['priority_score'].max() if df_hunt['priority_score'].max()>0 else 1
max_us = max(df_hunt['usuarios'].max(), 1)
df_hunt['demand_norm'] = df_hunt['priority_score']/max_ps
df_hunt['user_norm']   = df_hunt['usuarios']/max_us
df_hunt['combined_score'] = (0.6*df_hunt['demand_norm'] + 0.4*df_hunt['user_norm']).round(3)
df_hunt = df_hunt.sort_values('combined_score', ascending=False).reset_index(drop=True)
df_hunt['rank'] = df_hunt.index + 1

HUNTER_TIER_DEFS = [
    ('A+ Máxima prioridad', '#7f1d1d', 0.85),
    ('A Alta demanda sin supply', '#dc2626', 0.70),
    ('B Señal mixta', '#f97316', 0.55),
    ('C Zona activa', '#22c55e', 0.40),
    ('D Desarrollo', '#3b82f6', 0.25),
    ('E Monitoreo', '#94a3b8', 0.10),
]
def hunter_tier(score):
    if score >= 0.7:   return HUNTER_TIER_DEFS[0]
    if score >= 0.55:  return HUNTER_TIER_DEFS[1]
    if score >= 0.40:  return HUNTER_TIER_DEFS[2]
    if score >= 0.25:  return HUNTER_TIER_DEFS[3]
    if score >= 0.10:  return HUNTER_TIER_DEFS[4]
    return HUNTER_TIER_DEFS[5]

hunter_features = []
for _, row in df_hunt.iterrows():
    try:
        geo = hex_geojson(str(row['hex_id']))
    except:
        continue
    htier = hunter_tier(row['combined_score'])
    zona_lbl, fill, base_op = htier
    fill_op = round(min(0.75, base_op + row['combined_score']*0.2), 2)
    feat = {
        "type":"Feature",
        "geometry": geo,
        "properties":{
            "hex_id": str(row['hex_id']),
            "rank": int(row['rank']),
            "zona": zona_lbl,
            "delegacion": "CDMX",
            "combined_score": float(row['combined_score']),
            "demand_norm": float(row['demand_norm']),
            "user_norm": float(row['user_norm']),
            "gap": int(row['gap']),
            "neg_activos": int(row.get('negocios_actuales',0)),
            "neg_dormidos": int(row.get('neg_dormidos',0)),
            "demanda_dia": round(float(row.get('demanda_estimada_dia',0)),1),
            "has_users": int(row['usuarios'])>0,
            "usuarios": int(row['usuarios']),
            "sin_compras": int(row['sin_compras']),
            "tasa_conv_pct": float(row['tasa_conv_pct']),
            "fill_color": fill,
            "fill_opacity": fill_op,
        }
    }
    hunter_features.append(feat)

HUNTER_DATA = json.dumps({"type":"FeatureCollection","features":hunter_features}, ensure_ascii=False)
print(f"  {len(hunter_features)} hunter zones")

# Hunter table top 30
hunt_rows_json = []
for feat in hunter_features[:30]:
    p = feat['properties']
    center = h3.cell_to_latlng(p['hex_id'])
    hunt_rows_json.append({
        'tier': 'A_PRIORIDAD_ALTA' if p['combined_score']>=0.55 else 'B_PRIORIDAD_MEDIA',
        'zona': p['zona'],
        'lat': round(center[0],5),
        'lng': round(center[1],5),
        'demanda': p['demanda_dia'],
        'usuarios': p['usuarios'],
        'negocios': p['neg_activos'],
        'gap': p['gap'],
        'score': round(p['combined_score']*100),
    })

# Build hunter table HTML
tier_label = {'A_PRIORIDAD_ALTA':'🔴 A','B_PRIORIDAD_MEDIA':'🟠 B'}
hunter_table_rows = ''
for r in hunt_rows_json:
    bg  = '#1a0a0a' if r['tier']=='A_PRIORIDAD_ALTA' else '#1a0f00'
    clr = '#ef4444' if r['tier']=='A_PRIORIDAD_ALTA' else '#f97316'
    tier_disp = tier_label.get(r['tier'], r['tier'])
    hunter_table_rows += (
        f'<tr style="background:{bg};cursor:pointer" '
        f'onclick="flyToHunter({r["lat"]},{r["lng"]})">'
        f'<td style="color:{clr};font-weight:700;text-align:center">{tier_disp}</td>'
        f'<td style="text-align:right">{r["demanda"]}</td>'
        f'<td style="text-align:right;color:#a78bfa">{r["usuarios"]}</td>'
        f'<td style="text-align:right">{r["negocios"]}</td>'
        f'<td style="text-align:right;color:#ef4444;font-weight:700">{r["gap"]}</td>'
        f'<td style="text-align:right;color:#a78bfa">{r["score"]}</td>'
        f'</tr>\n'
    )

# ══════════════════════════════════════════════════════════════════════
# 9. SESSION DEMAND DATA
# ══════════════════════════════════════════════════════════════════════
print("Building SESSION_DEMAND_DATA…")

SESSION_TIER_DEFS = {
    'S_A_PLUS':      ('#7c3aed', 'A+ Sin supply cercano',    0.75),
    'S_A_ALTA':      ('#2563eb', 'A Alta oportunidad',       0.65),
    'S_B_MIXTA':     ('#0891b2', 'B Señal mixta',            0.55),
    'S_C_ACTIVA':    ('#059669', 'C Zona activa',            0.50),
    'S_D_DESARROLLO':('#65a30d', 'D En desarrollo',          0.40),
}

def session_tier(n_users, n_cercanos, tasa_conv_pct):
    if n_users >= 3 and n_cercanos == 0:   return 'S_A_PLUS'
    if n_users >= 3 and n_cercanos <= 2:   return 'S_A_ALTA'
    if n_users >= 2 and tasa_conv_pct < 30: return 'S_B_MIXTA'
    if n_users >= 2 and tasa_conv_pct >= 30: return 'S_C_ACTIVA'
    return 'S_D_DESARROLLO'

df_aprov_hex = df_aprov_loc2.groupby('hex_id').agg(
    n_users=('user_id','count'),
    n_con_tx=('transacciones', lambda x: (x>0).sum()),
    consumo=('consumo_total','sum'),
).reset_index()
df_aprov_hex['tasa_conv_pct'] = (df_aprov_hex['n_con_tx']/df_aprov_hex['n_users']*100).round(1)
df_aprov_hex['sin_compras'] = df_aprov_hex['n_users'] - df_aprov_hex['n_con_tx']
df_aprov_hex['n_cercanos'] = df_aprov_hex['hex_id'].apply(biz_nearby)

max_signal = max((df_aprov_hex['n_users'] + df_aprov_hex['n_con_tx']).max(), 1)
df_aprov_hex['score_norm_pct'] = ((df_aprov_hex['n_users']+df_aprov_hex['n_con_tx'])/max_signal*100).round(0).astype(int)
df_aprov_hex['tier_id'] = df_aprov_hex.apply(
    lambda r: session_tier(r['n_users'], r['n_cercanos'], r['tasa_conv_pct']), axis=1)

sd_features = []
for _, row in df_aprov_hex.iterrows():
    tid = row['tier_id']
    fill, tier_label_str, base_op = SESSION_TIER_DEFS[tid]
    fill_op = round(min(0.80, base_op + row['n_users']*0.03), 2)
    try:
        geo = hex_geojson(str(row['hex_id']))
    except:
        continue
    feat = {
        "type":"Feature",
        "geometry": geo,
        "properties":{
            "hex_id": str(row['hex_id']),
            "tier_id": tid,
            "tier_label": tier_label_str,
            "n_users": int(row['n_users']),
            "n_con_tx": int(row['n_con_tx']),
            "sin_compras": int(row['sin_compras']),
            "tasa_conv_pct": float(row['tasa_conv_pct']),
            "n_cercanos": int(row['n_cercanos']),
            "consumo": int(row['consumo']),
            "score_norm_pct": int(row['score_norm_pct']),
            "fill_color": fill,
            "fill_opacity": fill_op,
        }
    }
    sd_features.append(feat)

SESSION_DEMAND_DATA = json.dumps({"type":"FeatureCollection","features":sd_features}, ensure_ascii=False)
tier_counts = df_aprov_hex['tier_id'].value_counts().to_dict()
print(f"  {len(sd_features)} session demand hexes: {tier_counts}")

# ══════════════════════════════════════════════════════════════════════
# 10. HEAT MAP DATA
# ══════════════════════════════════════════════════════════════════════
print("Building heat map data…")

# Smooth heat maps from raw points
df_trx_loc = df_trx[df_trx['latitude'].notna() & (df_trx['latitude']!=0)].copy()
df_trx_loc['lat_v'] = pd.to_numeric(df_trx_loc['latitude'], errors='coerce')
df_trx_loc['lng_v'] = pd.to_numeric(df_trx_loc['longitude'], errors='coerce')
df_trx_loc = df_trx_loc[df_trx_loc['lat_v'].notna() & (df_trx_loc['lat_v']!=0)]

heat_ok   = [[round(r.lat_v,5), round(r.lng_v,5), 1.0] for r in df_trx_loc[~df_trx_loc['status_trx'].str.contains('incompleta',case=False)].itertuples()]
heat_fail = [[round(r.lat_v,5), round(r.lng_v,5), 1.0] for r in df_trx_loc[df_trx_loc['status_trx'].str.contains('incompleta',case=False)].itertuples()]
heat_users_pts = [[round(r['lat'],5), round(r['lng'],5), 1.0] for _, r in df_aprov_loc2.iterrows() if abs(r['lat'])>5]

HEAT_TRX_OK   = json.dumps(heat_ok, ensure_ascii=False)
HEAT_TRX_FAIL = json.dumps(heat_fail, ensure_ascii=False)
HEAT_USERS    = json.dumps(heat_users_pts, ensure_ascii=False)
print(f"  Heat: ok={len(heat_ok)}, fail={len(heat_fail)}, users={len(heat_users_pts)}")

# Hex-level heat (aggregated)
def build_hex_heat(points, label):
    hex_counts = defaultdict(int)
    for lat, lng, _ in points:
        h = safe_h3(lat, lng)
        if h: hex_counts[h] += 1
    if not hex_counts: return json.dumps({"type":"FeatureCollection","features":[]})
    max_count = max(hex_counts.values())
    feats = []
    for hx, cnt in hex_counts.items():
        try: geo = hex_geojson(hx)
        except: continue
        # sqrt normalization: reduces outlier dominance, spreads mid-range tones
        intensity = math.sqrt(cnt / max_count)
        feats.append({"type":"Feature","geometry":geo,"properties":{
            "count":cnt,"pct_total":round(cnt/len(points)*100,1),
            "intensity":round(intensity,3),"fill_opacity":round(0.12+intensity*0.78,2)
        }})
    return json.dumps({"type":"FeatureCollection","features":feats}, ensure_ascii=False)

HEX_HEAT_OK    = build_hex_heat(heat_ok,   'Tx OK')
HEX_HEAT_FAIL  = build_hex_heat(heat_fail, 'Tx Fail')
HEX_HEAT_USERS = build_hex_heat([[p[0],p[1],1] for p in heat_users_pts], 'Usuarios')
print("  Hex heat maps built")

# ══════════════════════════════════════════════════════════════════════
# 11. ASSEMBLE HTML
# ══════════════════════════════════════════════════════════════════════
print("Assembling HTML…")

HEAD = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<style>
/* ── KPI dashboard ─────────────────────────────────────────── */
#kpi-dash{position:fixed;top:10px;left:10px;z-index:1005;background:#0f172a;
  border-radius:12px;box-shadow:0 4px 22px rgba(0,0,0,.6);
  font-family:system-ui,sans-serif;width:320px;user-select:none;overflow:hidden;}
#kpi-dash-header{background:#00BFA5;color:#fff;padding:9px 14px;font-size:11px;
  font-weight:700;letter-spacing:.6px;display:flex;justify-content:space-between;
  align-items:center;cursor:move;}
#kpi-body{padding:10px 12px 12px;display:grid;grid-template-columns:1fr 1fr;gap:5px;}
.kc{background:#1e293b;border-radius:8px;padding:7px 10px;display:flex;flex-direction:column;gap:2px;}
.kc.full{grid-column:1/-1;}
.kl{font-size:8px;color:#64748b;font-weight:600;letter-spacing:.3px;text-transform:uppercase;line-height:1.2;}
.kv{font-size:17px;font-weight:700;color:#f1f5f9;line-height:1.1;}
.kv.g{color:#22c55e;}.kv.r{color:#ef4444;}.kv.y{color:#f59e0b;}
.kv.t{color:#00BFA5;}.kv.s{font-size:13px;}
.ks{font-size:8px;color:#475569;}
.kdiv{grid-column:1/-1;height:1px;background:#1e3a52;margin:2px 0;}
#kpi-tb{background:none;border:none;color:#fff;cursor:pointer;font-size:14px;padding:0;}
/* ── Cartboard ─────────────────────────────────────────────── */
#bmap-panel{position:fixed;top:80px;right:20px;z-index:1001;background:#fff;
  border-radius:10px;box-shadow:0 3px 14px rgba(0,0,0,.18);font-family:system-ui,sans-serif;
  font-size:11px;min-width:220px;max-height:82vh;display:flex;flex-direction:column;user-select:none;}
#bmap-header{background:#1e293b;color:#fff;padding:9px 12px;border-radius:10px 10px 0 0;cursor:move;
  display:flex;justify-content:space-between;align-items:center;font-size:12px;font-weight:600;}
#bmap-body{padding:10px 13px;overflow-y:auto;flex:1;}
.bs{margin-bottom:10px;}
.bs-title{font-size:10px;font-weight:700;letter-spacing:.5px;color:#64748b;text-transform:uppercase;margin-bottom:5px;}
.bchk{display:flex;align-items:center;gap:5px;padding:2px 0;cursor:pointer;}
.bchk input{margin:0;}
.bdot{width:11px;height:11px;border-radius:2px;flex-shrink:0;}
.bbr{display:flex;gap:5px;margin-top:6px;}
.bb{flex:1;font-size:10px;padding:3px 0;cursor:pointer;border:1px solid #e2e8f0;
  border-radius:4px;background:#f8fafc;}.bb:hover{background:#e2e8f0;}
hr.bhr{border:none;border-top:1px solid #f1f5f9;margin:8px 0;}
#bmap-toggle{display:none;position:fixed;top:80px;right:20px;z-index:1002;
  background:#1e293b;color:#fff;border:none;border-radius:8px;
  padding:7px 12px;font-size:11px;cursor:pointer;}
/* ── Mode button ───────────────────────────────────────────── */
#mode-btn{position:fixed;top:10px;right:20px;z-index:1006;background:#1e293b;
  color:#fff;border:none;border-radius:8px;padding:8px 14px;font-size:12px;
  cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.3);font-family:system-ui,sans-serif;}
#mode-btn:hover{background:#334155;}
/* ── Hunter panel ──────────────────────────────────────────── */
#hunter-panel{position:fixed;bottom:20px;left:10px;z-index:1004;background:#0f172a;
  border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,.5);font-family:system-ui,sans-serif;
  width:380px;max-height:300px;display:flex;flex-direction:column;user-select:none;overflow:hidden;}
#hunter-header{background:#dc2626;color:#fff;padding:8px 12px;cursor:move;
  display:flex;justify-content:space-between;align-items:center;font-size:11px;font-weight:700;}
#hunter-body{overflow-y:auto;flex:1;}
#hunter-body table{width:100%;border-collapse:collapse;font-size:10px;}
#hunter-body th{background:#1e293b;color:#94a3b8;padding:5px 8px;text-align:right;
  font-weight:600;letter-spacing:.3px;font-size:9px;position:sticky;top:0;}
#hunter-body th:first-child{text-align:center;}
#hunter-body td{padding:4px 8px;border-bottom:1px solid #1e293b;color:#e2e8f0;}
#hunter-body tr:hover td{background:#1e3a52!important;}
#hunter-toggle{display:none;position:fixed;bottom:20px;left:10px;z-index:1003;
  background:#dc2626;color:#fff;border:none;border-radius:8px;
  padding:7px 12px;font-size:11px;cursor:pointer;}
/* ── Guide ─────────────────────────────────────────────────── */
#guide-btn-wrap{position:fixed;bottom:24px;right:24px;z-index:1010;}
#guide-btn-wrap button{background:#151A4F;color:#6EE9B3;border:2px solid #6EE9B3;
  border-radius:50%;width:44px;height:44px;font-size:20px;cursor:pointer;
  box-shadow:0 3px 10px rgba(0,0,0,.4);}
#guide-overlay{display:none;position:fixed;inset:0;z-index:2000;
  background:rgba(0,0,0,.7);align-items:flex-start;justify-content:center;padding-top:40px;}
#guide-overlay.open{display:flex;}
#guide-modal{background:#0f172a;border-radius:14px;padding:0;
  max-width:600px;width:95%;color:#f1f5f9;font-family:system-ui;
  box-shadow:0 8px 32px rgba(0,0,0,.6);overflow:hidden;max-height:82vh;display:flex;flex-direction:column;}
#guide-tabs{display:flex;background:#1e293b;border-bottom:1px solid #334155;}
.gtab{flex:1;padding:10px 6px;font-size:11px;font-weight:600;text-align:center;
  cursor:pointer;color:#64748b;border:none;background:none;}
.gtab.active{color:#00BFA5;border-bottom:2px solid #00BFA5;}
#guide-content{padding:18px 20px;overflow-y:auto;flex:1;}
.gpanel{display:none;}.gpanel.active{display:block;}
.gsec{margin-bottom:16px;}
.gsec h3{font-size:12px;color:#00BFA5;font-weight:700;margin:0 0 8px;letter-spacing:.4px;}
.grow{display:flex;gap:10px;margin-bottom:6px;align-items:flex-start;}
.gdot{width:12px;height:12px;border-radius:3px;flex-shrink:0;margin-top:2px;}
.gtxt{font-size:11px;color:#cbd5e1;line-height:1.6;}
.sub{font-size:10px;color:#64748b;}
#guide-close{background:none;border:none;color:#64748b;font-size:20px;cursor:pointer;padding:10px 16px;}
.gtab-bar{display:flex;justify-content:space-between;align-items:center;background:#1e293b;}
</style>
"""

KPI_HTML = f"""
<div id="kpi-dash">
  <div id="kpi-dash-header">
    <span>📊 BIZNE PA · KPIs <span style="font-weight:400;opacity:.7">25 mayo 2026</span></span>
    <button id="kpi-tb" onclick="var b=document.getElementById('kpi-body');b.style.display=b.style.display==='none'?'grid':'none';this.textContent=b.style.display==='none'?'▼':'▲'">▲</button>
  </div>
  <div id="kpi-body">
    <div class="kc"><div class="kl">Signups totales</div><div class="kv t">{k['signups_totales']}</div></div>
    <div class="kc"><div class="kl">Usuarios aprobados</div><div class="kv">{k['usuarios_aprobados']}</div><div class="ks">{ap_pct}% del total</div></div>
    <div class="kdiv"></div>
    <div class="kc"><div class="kl">Tx completadas</div><div class="kv g">{k['trx_completadas']}</div></div>
    <div class="kc"><div class="kl">Tx incompletas</div><div class="kv r">{k['trx_incompletas']}</div></div>
    <div class="kc"><div class="kl">Tasa aceptación</div><div class="kv {tc_col}">{k['tasa_aceptacion']}%</div></div>
    <div class="kc"><div class="kl">T. prom. aceptación</div><div class="kv s">{k['tiempo_prom_aceptacion']} min</div></div>
    <div class="kdiv"></div>
    <div class="kc"><div class="kl">% Conv. primer consumo</div><div class="kv {cv_col}">{k['conv_primer_consumo']}%</div></div>
    <div class="kc"><div class="kl">Aprobados sin convertir</div><div class="kv {as_col}">{k['aprobados_sin_convertir']}%</div></div>
    <div class="kc"><div class="kl">T. prom. primer consumo</div><div class="kv s">{k['dias_prom_primer_consumo']} días</div><div class="ks">usuarios post 20-abr</div></div>
    <div class="kc"><div class="kl">% últ. sesión sin supply</div><div class="kv {ss_col}">{k['pct_sin_supply']}%</div></div>
    <div class="kdiv"></div>
    <div class="kc"><div class="kl">Negocios activos</div><div class="kv t">{k['negocios_activos']}</div></div>
    <div class="kc"><div class="kl">Negocios dormidos</div><div class="kv y">{k['negocios_dormidos']} <span style="font-size:10px;color:#94a3b8">({k['dormidos_pct_total']}%)</span></div></div>
    <div class="kc"><div class="kl">Sin transacciones 30d</div><div class="kv {st_col}">{k['pct_sin_tx']}%</div><div class="ks">{k['sin_tx_n']} negocios</div></div>
    <div class="kc full"><div class="kl">Mediana / Promedio tx negocio</div><div class="kv s">{k['mediana_tx_negocio']} / {k['promedio_tx_negocio']} <span style="font-size:9px;color:#64748b">tx/mes</span></div></div>
  </div>
</div>
"""

HUNTER_HTML = f"""
<div id="hunter-panel">
  <div id="hunter-header">
    <span>🎯 ZONAS HUNTER — Top 30 por Prioridad</span>
    <button onclick="document.getElementById('hunter-panel').style.display='none';document.getElementById('hunter-toggle').style.display='block'"
      style="border:none;background:none;cursor:pointer;color:#fff;font-size:14px">✕</button>
  </div>
  <div id="hunter-body">
    <table>
      <thead><tr>
        <th>Tier</th><th>Dem/día</th><th>Users</th><th>Neg.</th>
        <th style="color:#ef4444">Gap 🍽</th><th style="color:#a78bfa">D+U</th>
      </tr></thead>
      <tbody>
{hunter_table_rows}
      </tbody>
    </table>
  </div>
</div>
<button id="hunter-toggle" onclick="this.style.display='none';document.getElementById('hunter-panel').style.display='flex'">🎯 Hunter Zones</button>
"""

PANEL_HTML = """
<button id="mode-btn" onclick="toggleMode()">☀️ Modo claro</button>

<div id="bmap-panel">
  <div id="bmap-header">
    <span>⚙️ Configuración del mapa</span>
    <button onclick="document.getElementById('bmap-panel').style.display='none';document.getElementById('bmap-toggle').style.display='block'"
      style="border:none;background:none;cursor:pointer;color:#fff;font-size:15px;line-height:1">✕</button>
  </div>
  <div id="bmap-body">

    <div class="bs">
      <div class="bs-title">📍 Capas del mapa</div>
      <label class="bchk"><input type="checkbox" id="ly_hexes"    checked onchange="toggleLayer('hexes',this.checked)">
        <span class="bdot" style="background:#3b82f6"></span> Hexágonos demanda PA</label>
      <label class="bchk"><input type="checkbox" id="ly_activos"  checked onchange="toggleLayer('activos',this.checked)">
        <span style="display:inline-flex;gap:2px;margin-right:2px"><span class="bdot" style="background:#22c55e"></span><span class="bdot" style="background:#00BFA5"></span><span class="bdot" style="background:#f59e0b"></span><span class="bdot" style="background:#ef4444"></span></span> Negocios Activos</label>
      <label class="bchk"><input type="checkbox" id="ly_dormidas" checked onchange="toggleLayer('dormidas',this.checked)">
        <span class="bdot" style="background:#9ca3af"></span> Negocios Dormidos</label>
      <label class="bchk"><input type="checkbox" id="ly_hunter"   checked onchange="toggleLayer('hunter',this.checked)">
        <span class="bdot" style="background:#f97316;border-radius:50%"></span> Zonas Hunter</label>
      <label class="bchk"><input type="checkbox" id="ly_sdemand"  onchange="toggleLayer('sdemand',this.checked)">
        <span class="bdot" style="background:#7c3aed;border-radius:50%"></span> Demanda por Sesiones</label>
      <label class="bchk"><input type="checkbox" id="ly_metro"    checked onchange="toggleLayer('metro',this.checked)">
        <span class="bdot" style="background:#e91e63"></span> Estaciones Metro</label>
      <label class="bchk"><input type="checkbox" id="ly_upcs"     checked onchange="toggleLayer('upcs',this.checked)">
        <span class="bdot" style="background:#7C3AED"></span> UPCs Policía</label>
      <label class="bchk"><input type="checkbox" id="ly_sec"      checked onchange="toggleLayer('sec',this.checked)">
        <span class="bdot" style="background:#06b6d4"></span> Sectores PA</label>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🔥 Mapa de calor (smooth)</div>
      <label class="bchk"><input type="checkbox" id="ht_ok"    onchange="toggleHeat('ok',this.checked)">
        <span class="bdot" style="background:#22c55e"></span> Tx completadas</label>
      <label class="bchk"><input type="checkbox" id="ht_fail"  onchange="toggleHeat('fail',this.checked)">
        <span class="bdot" style="background:#ef4444"></span> Tx incompletas</label>
      <label class="bchk"><input type="checkbox" id="ht_users" onchange="toggleHeat('users',this.checked)">
        <span class="bdot" style="background:#a78bfa"></span> Última sesión usuarios</label>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">⬡ Hexes de actividad</div>
      <label class="bchk"><input type="checkbox" id="hh_ok"    onchange="toggleHexHeat('ok',this.checked)">
        <span class="bdot" style="background:#22c55e"></span> Tx completadas</label>
      <label class="bchk"><input type="checkbox" id="hh_fail"  onchange="toggleHexHeat('fail',this.checked)">
        <span class="bdot" style="background:#ef4444"></span> Tx incompletas</label>
      <label class="bchk"><input type="checkbox" id="hh_users" onchange="toggleHexHeat('users',this.checked)">
        <span class="bdot" style="background:#a78bfa"></span> Última sesión usuarios</label>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🔍 Buscar negocio</div>
      <input id="biz-search" type="text" placeholder="Nombre del negocio..."
        oninput="searchNegocios(this.value)"
        style="width:100%;padding:5px 8px;border:1px solid #e2e8f0;border-radius:5px;
               font-size:11px;box-sizing:border-box;outline:none;color:#1e293b;background:#f8fafc">
      <div id="biz-count" style="font-size:9px;color:#94a3b8;margin-top:3px;text-align:right"></div>
      <button onclick="searchNegocios('');document.getElementById('biz-search').value=''"
        style="margin-top:4px;width:100%;font-size:10px;padding:3px;cursor:pointer;
               border:1px solid #e2e8f0;border-radius:4px;background:#f8fafc;color:#64748b">Mostrar todos</button>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🗺 Prioridad hexes demanda PA</div>
      <label class="bchk"><input type="checkbox" id="tier_A_PRIORIDAD_ALTA"  checked onchange="filterTiers()">
        <span class="bdot" style="background:#dc2626"></span> A — Prioridad Alta</label>
      <label class="bchk"><input type="checkbox" id="tier_B_PRIORIDAD_MEDIA" checked onchange="filterTiers()">
        <span class="bdot" style="background:#f97316"></span> B — Prioridad Media</label>
      <label class="bchk"><input type="checkbox" id="tier_C_VIGILANCIA"      checked onchange="filterTiers()">
        <span class="bdot" style="background:#eab308"></span> C — Vigilancia</label>
      <label class="bchk"><input type="checkbox" id="tier_D_BAJA"            checked onchange="filterTiers()">
        <span class="bdot" style="background:#22c55e"></span> D — Baja</label>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🎯 Zonas Hunter</div>
      <label class="bchk"><input type="checkbox" id="ht_A_crit" checked onchange="filterHunters()">
        <span class="bdot" style="background:#7f1d1d"></span> A+ — Máxima prioridad</label>
      <label class="bchk"><input type="checkbox" id="ht_A"      checked onchange="filterHunters()">
        <span class="bdot" style="background:#dc2626"></span> A — Alta demanda sin supply</label>
      <label class="bchk"><input type="checkbox" id="ht_B"      checked onchange="filterHunters()">
        <span class="bdot" style="background:#f97316"></span> B — Señal mixta</label>
      <label class="bchk"><input type="checkbox" id="ht_C"      checked onchange="filterHunters()">
        <span class="bdot" style="background:#22c55e"></span> C — Zona activa</label>
      <label class="bchk"><input type="checkbox" id="ht_D"      checked onchange="filterHunters()">
        <span class="bdot" style="background:#3b82f6"></span> D — Desarrollo</label>
      <label class="bchk"><input type="checkbox" id="ht_E"      checked onchange="filterHunters()">
        <span class="bdot" style="background:#94a3b8"></span> E — Baja densidad</label>
      <div class="bbr">
        <button class="bb" onclick="['ht_A_crit','ht_A','ht_B','ht_C','ht_D','ht_E'].forEach(function(id){var e=document.getElementById(id);if(e)e.checked=true});filterHunters()">Todos</button>
        <button class="bb" onclick="['ht_A_crit','ht_A','ht_B','ht_C','ht_D','ht_E'].forEach(function(id){var e=document.getElementById(id);if(e)e.checked=false});filterHunters()">Ninguno</button>
      </div>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">💡 Demanda por sesiones</div>
      <label class="bchk"><input type="checkbox" id="sd_A_PLUS"    checked onchange="filterSessionDemand()">
        <span class="bdot" style="background:#7c3aed"></span> A+ — Sin supply cercano</label>
      <label class="bchk"><input type="checkbox" id="sd_A_ALTA"    checked onchange="filterSessionDemand()">
        <span class="bdot" style="background:#2563eb"></span> A — Alta oportunidad</label>
      <label class="bchk"><input type="checkbox" id="sd_B_MIXTA"   checked onchange="filterSessionDemand()">
        <span class="bdot" style="background:#0891b2"></span> B — Mixta</label>
      <label class="bchk"><input type="checkbox" id="sd_C_ACTIVA"  checked onchange="filterSessionDemand()">
        <span class="bdot" style="background:#059669"></span> C — Activa</label>
      <label class="bchk"><input type="checkbox" id="sd_D_DESA"    checked onchange="filterSessionDemand()">
        <span class="bdot" style="background:#65a30d"></span> D — Desarrollo</label>
      <div class="bbr">
        <button class="bb" onclick="['sd_A_PLUS','sd_A_ALTA','sd_B_MIXTA','sd_C_ACTIVA','sd_D_DESA'].forEach(function(id){var e=document.getElementById(id);if(e)e.checked=true});filterSessionDemand()">Todos</button>
        <button class="bb" onclick="['sd_A_PLUS','sd_A_ALTA','sd_B_MIXTA','sd_C_ACTIVA','sd_D_DESA'].forEach(function(id){var e=document.getElementById(id);if(e)e.checked=false});filterSessionDemand()">Ninguno</button>
      </div>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">📋 Tooltip Hexágonos</div>
      <label class="bchk"><input type="checkbox" id="hf_DI"           checked onchange="updateHexTT()"> DI</label>
      <label class="bchk"><input type="checkbox" id="hf_demanda_dia"  checked onchange="updateHexTT()"> Demanda/día</label>
      <label class="bchk"><input type="checkbox" id="hf_biz_actuales" checked onchange="updateHexTT()"> Negocios act.</label>
      <label class="bchk"><input type="checkbox" id="hf_gap"          checked onchange="updateHexTT()"> Gap</label>
      <label class="bchk"><input type="checkbox" id="hf_cobertura_pct" checked onchange="updateHexTT()"> Cobertura %</label>
      <label class="bchk"><input type="checkbox" id="hf_D90"          onchange="updateHexTT()"> D90</label>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🍽 Tooltip Negocios</div>
      <label class="bchk"><input type="checkbox" id="bf_rating"    checked onchange="updateBizTT()"> Rating ⭐</label>
      <label class="bchk"><input type="checkbox" id="bf_capacidad" checked onchange="updateBizTT()"> Capacidad</label>
      <label class="bchk"><input type="checkbox" id="bf_tx_hist"   checked onchange="updateBizTT()"> Trx históricas</label>
      <label class="bchk"><input type="checkbox" id="bf_tx_30d"    checked onchange="updateBizTT()"> Tx 30d</label>
      <label class="bchk"><input type="checkbox" id="bf_acepta"    checked onchange="updateBizTT()"> Tasa aceptación %</label>
      <label class="bchk"><input type="checkbox" id="bf_tiempo"    checked onchange="updateBizTT()"> T. aceptación p50</label>
      <div style="font-size:9px;color:#64748b;margin-top:4px;padding:3px 6px;background:#f1f5f9;border-radius:4px">
        🍽 Menús siempre visibles en tooltip</div>
      <div class="bbr">
        <button class="bb" onclick="document.querySelectorAll('[id^=bf_]').forEach(function(c){c.checked=true});updateBizTT()">Todos</button>
        <button class="bb" onclick="document.querySelectorAll('[id^=bf_]').forEach(function(c){c.checked=false});updateBizTT()">Ninguno</button>
      </div>
    </div>

  </div>
</div>
<button id="bmap-toggle" onclick="this.style.display='none';document.getElementById('bmap-panel').style.display='flex'">⚙️ Mapa</button>
"""

GUIDE_HTML = f"""
<div id="guide-btn-wrap">
  <button onclick="document.getElementById('guide-overlay').classList.add('open')">?</button>
</div>
<div id="guide-overlay" onclick="if(event.target===this)this.classList.remove('open')">
  <div id="guide-modal">
    <div class="gtab-bar">
      <div id="guide-tabs">
        <button class="gtab active" onclick="switchTab('kpis',this)">📊 KPIs</button>
        <button class="gtab" onclick="switchTab('interpretar',this)">🔍 Interpretar</button>
        <button class="gtab" onclick="switchTab('zonas',this)">🗺 Zonas</button>
        <button class="gtab" onclick="switchTab('demanda',this)">💡 Demanda</button>
        <button class="gtab" onclick="switchTab('calor',this)">🔥 Calor</button>
        <button class="gtab" onclick="switchTab('capas',this)">📍 Capas</button>
        <button class="gtab" onclick="switchTab('uso',this)">🖱 Uso</button>
      </div>
      <button id="guide-close" onclick="document.getElementById('guide-overlay').classList.remove('open')">✕</button>
    </div>
    <div id="guide-content">
      <div id="gpanel-interpretar" class="gpanel">

        <div class="gsec"><h3>🎨 Colores de negocios — Quality Score</h3>
          <div class="grow"><span class="gdot" style="background:#22c55e"></span><div class="gtxt"><strong>Verde — Excelente (≥80):</strong> Negocio saludable, alta aceptación, menús completos. Palanca de crecimiento.</div></div>
          <div class="grow"><span class="gdot" style="background:#00BFA5"></span><div class="gtxt"><strong>Teal — Alta (60–79):</strong> Buen desempeño general. Revisar qué le falta para subir a Excelente.</div></div>
          <div class="grow"><span class="gdot" style="background:#f59e0b"></span><div class="gtxt"><strong>Ámbar — Media (40–59):</strong> Rendimiento inconsistente. Detectar si es menu, aceptación o tiempo de respuesta.</div></div>
          <div class="grow"><span class="gdot" style="background:#f97316"></span><div class="gtxt"><strong>Naranja — Baja (20–39):</strong> Requiere intervención activa. Riesgo de entrar en dormancia.</div></div>
          <div class="grow"><span class="gdot" style="background:#ef4444"></span><div class="gtxt"><strong>Rojo — Crítica (&lt;20):</strong> Negocio en riesgo alto. Evaluar si se reactiva o se marca como dormido.</div></div>
        </div>

        <div class="gsec"><h3>🎯 Leer una zona Hunter</h3>
          <div class="grow"><div class="gtxt">El <strong>score D+U</strong> combina demanda estructural (60%) + señal real de usuarios (40%). Un score alto sin negocios cerca = oportunidad de onboarding urgente.</div></div>
          <div class="grow"><div class="gtxt"><strong>Gap 🍽</strong> = negocios que faltan para cubrir la demanda estimada. Si Gap ≥ 3 y no hay negocio activo cercano, es zona prioritaria para hunters.</div></div>
          <div class="grow"><div class="gtxt"><strong>Usuarios presentes (👤) con baja conversión</strong> → los policías ya están ahí pero no compran. Problema de supply o awareness, no de demanda.</div></div>
          <div class="grow"><div class="gtxt"><strong>Negocios dormidos en zona hunter</strong> → prioridad de reactivación antes de onboardear uno nuevo.</div></div>
        </div>

        <div class="gsec"><h3>💡 Cruzar capas para leer mejor</h3>
          <div class="grow"><div class="gtxt">
            <strong>Hunter rojo + Sesión A+ (morado)</strong> en la misma zona → señal doble: demanda estructural Y usuarios reales sin supply. Máxima urgencia.<br>
            <span class="sub">Activa "Demanda por Sesiones" en ⚙️ → Capas para verlo.</span>
          </div></div>
          <div class="grow"><div class="gtxt">
            <strong>Zona con tx completadas (calor verde) pero negocios en rojo/naranja</strong> → hay transacciones pero los negocios tienen mala calidad operativa. Priorizar coaching.
          </div></div>
          <div class="grow"><div class="gtxt">
            <strong>Negocios verdes/teal en zona sin supply</strong> → esos negocios pueden absorber más demanda si se les activa delivery o se amplía su radio.
          </div></div>
          <div class="grow"><div class="gtxt">
            <strong>Calor rojo (tx incompletas) concentrado</strong> → problema operativo puntual en esa zona. Puede ser red, negocio específico o turno con problemas de aceptación.
          </div></div>
        </div>

        <div class="gsec"><h3>🚇 Metro y UPCs como contexto</h3>
          <div class="grow"><div class="gtxt"><strong>Estación con transbordos (⇄)</strong> = punto de alta densidad de policías en tránsito. Las zonas hunter cerca de transbordos tienen demanda extra por flujo.</div></div>
          <div class="grow"><div class="gtxt"><strong>UPC (🛡)</strong> = ubicación fija de elementos. Un hex con UPC cercana y gap alto es candidato inmediato para un negocio nuevo.</div></div>
          <div class="grow"><div class="gtxt"><strong>Sin UPC ni metro en zona hunter</strong> → la demanda es de patrullaje móvil (rutas), no fijo. Considerar negocios con cobertura de área amplia o en avenida de ruta.</div></div>
        </div>

        <div class="gsec"><h3>⚠️ Señales de alerta rápida</h3>
          <div style="background:#1e0a0a;border-radius:8px;padding:8px 10px;margin-bottom:6px">
            <div class="gtxt" style="color:#fca5a5"><strong>🔴 Zona roja sin negocios activos</strong> → gap sin cubrir, acción inmediata de hunters.</div>
          </div>
          <div style="background:#1a0f00;border-radius:8px;padding:8px 10px;margin-bottom:6px">
            <div class="gtxt" style="color:#fdba74"><strong>🟠 Varios negocios naranja/rojo agrupados</strong> → posible problema de sector: competencia, mal área, o falta de capacitación.</div>
          </div>
          <div style="background:#0f1a1a;border-radius:8px;padding:8px 10px;margin-bottom:6px">
            <div class="gtxt" style="color:#6ee7b7"><strong>🟢 Zona verde sin usuarios</strong> → buen supply pero sin demanda real detectada. Revisar si los policías de esa zona ya tienen cuenta activa.</div>
          </div>
          <div style="background:#0f0f1a;border-radius:8px;padding:8px 10px">
            <div class="gtxt" style="color:#c4b5fd"><strong>🟣 Hexes morados sin negocios</strong> → policías con app activa pero sin dónde comprar. Onboardear urgente en radio de 500m.</div>
          </div>
        </div>

      </div>

      <div id="gpanel-kpis" class="gpanel active">
        <div class="gsec"><h3>Dashboard KPIs — 25 mayo 2026</h3>
          <div class="grow"><div class="gtxt"><strong>Signups ({k['signups_totales']}) · Aprobados ({k['usuarios_aprobados']} — {ap_pct}%)</strong><br><span class="sub">El resto está en revisión o rechazado por KYC.</span></div></div>
          <div class="grow"><div class="gtxt"><strong>Tasa aceptación {k['tasa_aceptacion']}%</strong> — Tx completadas/{'{'}Tx totales{'}'} en los últimos 30d.</div></div>
          <div class="grow"><div class="gtxt"><strong>Conv. primer consumo {k['conv_primer_consumo']}%</strong> — Aprobados que hicieron al menos 1 compra.<br><span class="sub">{k['aprobados_sin_convertir']}% de aprobados nunca han comprado → oportunidad de activación.</span></div></div>
          <div class="grow"><div class="gtxt"><strong>Sin supply {k['pct_sin_supply']}%</strong> — Usuarios con sesión activa en zona sin negocios cerca.</div></div>
          <div class="grow"><div class="gtxt"><strong>Dormidos {k['negocios_dormidos']} ({k['dormidos_pct_total']}%)</strong> — Negocios sin tx en los últimos 14+ días.</div></div>
        </div>
      </div>
      <div id="gpanel-zonas" class="gpanel">
        <div class="gsec"><h3>Hexágonos de Demanda PA (modelo estructural)</h3>
          <div class="grow"><span class="gdot" style="background:#dc2626"></span><div class="gtxt"><strong>A — Prioridad Alta:</strong> Alta demanda estimada de policías, sin negocios suficientes. Actuar ahora.</div></div>
          <div class="grow"><span class="gdot" style="background:#f97316"></span><div class="gtxt"><strong>B — Prioridad Media:</strong> Demanda moderada, supply parcial. Reforzar.</div></div>
          <div class="grow"><span class="gdot" style="background:#eab308"></span><div class="gtxt"><strong>C — Vigilancia:</strong> Demanda baja pero creciente. Monitorear.</div></div>
          <div class="grow"><span class="gdot" style="background:#22c55e"></span><div class="gtxt"><strong>D — Baja:</strong> Poca concentración o buen supply. Sin acción inmediata.</div></div>
        </div>
        <div class="gsec"><h3>Zonas Hunter (modelo combinado)</h3>
          <div class="grow"><span class="gdot" style="background:#7f1d1d"></span><div class="gtxt"><strong>A+ Máxima prioridad:</strong> Score ≥ 70. Demanda alta + usuarios reales presentes. Top de la lista.</div></div>
          <div class="grow"><span class="gdot" style="background:#dc2626"></span><div class="gtxt"><strong>A Alta demanda sin supply:</strong> Score 55-70. Señal estructural fuerte.</div></div>
          <div class="grow"><span class="gdot" style="background:#f97316"></span><div class="gtxt"><strong>B Señal mixta:</strong> Score 40-55. Demanda moderada con algo de actividad real.</div></div>
          <div class="grow"><span class="gdot" style="background:#22c55e"></span><div class="gtxt"><strong>C Zona activa:</strong> Score 25-40. Ya hay actividad pero puede crecer.</div></div>
        </div>
      </div>
      <div id="gpanel-demanda" class="gpanel">
        <div class="gsec"><h3>Demanda por Sesiones (modelo de usuarios reales)</h3>
          <div class="grow"><span class="gdot" style="background:#7c3aed"></span><div class="gtxt"><strong>A+ Sin supply:</strong> 3+ usuarios aprobados en la zona y 0 negocios cerca. Urgente abrir supply.</div></div>
          <div class="grow"><span class="gdot" style="background:#2563eb"></span><div class="gtxt"><strong>A Alta oportunidad:</strong> 3+ usuarios, 1-2 negocios cerca. Oportunidad de crecimiento.</div></div>
          <div class="grow"><span class="gdot" style="background:#0891b2"></span><div class="gtxt"><strong>B Mixta:</strong> 2+ usuarios, conversión menor al 30%. Hay usuarios pero no están comprando.</div></div>
          <div class="grow"><span class="gdot" style="background:#059669"></span><div class="gtxt"><strong>C Activa:</strong> 2+ usuarios, conversión ≥30%. Zona funcionando bien.</div></div>
          <div class="grow"><span class="gdot" style="background:#65a30d"></span><div class="gtxt"><strong>D Desarrollo:</strong> 1 usuario o señal débil. En etapa temprana.</div></div>
          <div class="grow"><div class="gtxt"><span class="sub">Esta capa está desactivada por defecto. Actívala desde el panel ⚙️ → Capas.</span></div></div>
        </div>
      </div>
      <div id="gpanel-calor" class="gpanel">
        <div class="gsec"><h3>Mapas de Calor</h3>
          <div class="grow"><span class="gdot" style="background:#22c55e"></span><div class="gtxt"><strong>Tx completadas (smooth):</strong> Dónde está ocurriendo el consumo real. Gradiente verde.</div></div>
          <div class="grow"><span class="gdot" style="background:#ef4444"></span><div class="gtxt"><strong>Tx incompletas (smooth):</strong> Dónde fallan las transacciones. Gradiente rojo.</div></div>
          <div class="grow"><span class="gdot" style="background:#a78bfa"></span><div class="gtxt"><strong>Sesiones usuarios (smooth):</strong> Dónde están los usuarios activos. Gradiente púrpura.</div></div>
          <div class="grow"><div class="gtxt"><strong>Hexes de actividad:</strong> Versión hexagonal de los mismos datos — útil para ver patrones de densidad por zona H3.</div></div>
        </div>
      </div>
      <div id="gpanel-capas" class="gpanel">
        <div class="gsec"><h3>Capas de Referencia</h3>
          <div class="grow"><span class="gdot" style="background:#e91e63"></span><div class="gtxt"><strong>Estaciones Metro:</strong> 110 estaciones con color por línea. Al hacer hover ves el nombre y transbordos.</div></div>
          <div class="grow"><span class="gdot" style="background:#7C3AED"></span><div class="gtxt"><strong>UPCs Policía:</strong> 124 Unidades de Protección Ciudadana. Coordenadas corregidas (CSV original tenía lat/lng intercambiados).</div></div>
          <div class="grow"><span class="gdot" style="background:#06b6d4"></span><div class="gtxt"><strong>Sectores PA:</strong> Ubicaciones de los sectores de la Policía Auxiliar con demanda estimada por día.</div></div>
        </div>
      </div>
      <div id="gpanel-uso" class="gpanel">
        <div class="gsec"><h3>Cómo usar el mapa</h3>
          <div class="grow"><div class="gtxt"><strong>Dashboard KPIs</strong> — Arriba izquierda. Arrastra por el header verde para moverlo. Haz click en ▲/▼ para expandir/colapsar.</div></div>
          <div class="grow"><div class="gtxt"><strong>Panel Hunter</strong> — Abajo izquierda. Tabla interactiva: click en cualquier fila para hacer zoom a esa zona en el mapa.</div></div>
          <div class="grow"><div class="gtxt"><strong>Panel ⚙️</strong> — Derecha. Controla qué capas se ven, filtra por tier, busca negocios, y personaliza los tooltips.</div></div>
          <div class="grow"><div class="gtxt"><strong>Modo oscuro/claro</strong> — Botón ☀️/🌙 en esquina superior derecha. Cambia el tile layer entre CartoDB Dark y Light.</div></div>
          <div class="grow"><div class="gtxt"><strong>Tooltips</strong> — Hover sobre cualquier hexágono, negocio o zona para ver detalles. Los campos visibles se configuran desde el panel ⚙️.</div></div>
        </div>
      </div>
    </div>
  </div>
</div>
"""

JS = f"""<script>
var HEX_DATA            = {HEX_DATA};
var BIZ_DATA            = {BIZ_DATA};
var DORM_DATA           = {DORM_DATA};
var METRO_DATA          = {METRO_DATA};
var UPC_DATA            = {UPC_DATA};
var SEC_DATA            = {SEC_DATA};
var HUNTER_DATA         = {HUNTER_DATA};
var SESSION_DEMAND_DATA = {SESSION_DEMAND_DATA};
var HEAT_TRX_OK         = {HEAT_TRX_OK};
var HEAT_TRX_FAIL       = {HEAT_TRX_FAIL};
var HEAT_USERS          = {HEAT_USERS};
var HEX_HEAT_OK         = {HEX_HEAT_OK};
var HEX_HEAT_FAIL       = {HEX_HEAT_FAIL};
var HEX_HEAT_USERS      = {HEX_HEAT_USERS};

var IS_DARK  = true;
var TILE_DARK  = "https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png";
var TILE_LIGHT = "https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png";

function switchTab(name, btn) {{
  document.querySelectorAll('.gpanel').forEach(function(p){{p.classList.remove('active');}});
  document.querySelectorAll('.gtab').forEach(function(t){{t.classList.remove('active');}});
  var panel = document.getElementById('gpanel-'+name);
  if(panel) panel.classList.add('active');
  if(btn) btn.classList.add('active');
}}

function toggleMode() {{
  IS_DARK = !IS_DARK;
  var btn = document.getElementById('mode-btn');
  if (IS_DARK) {{
    btn.textContent = '☀️ Modo claro';
    document.getElementById('kpi-dash').style.background = '#0f172a';
    document.querySelectorAll('.kc').forEach(function(el){{el.style.background='#1e293b';}});
  }} else {{
    btn.textContent = '🌙 Modo oscuro';
    document.getElementById('kpi-dash').style.background = '#1e293b';
    document.querySelectorAll('.kc').forEach(function(el){{el.style.background='#334155';}});
  }}
  if (window.THE_MAP && window.TILE_LAYER) {{
    window.THE_MAP.removeLayer(window.TILE_LAYER);
    window.TILE_LAYER = L.tileLayer(IS_DARK ? TILE_DARK : TILE_LIGHT,
      {{attribution:'&copy; CartoDB',maxZoom:20}}).addTo(window.THE_MAP);
  }}
}}

var HEX_FIELDS = [
  {{key:"DI",id:"hf_DI",label:"DI"}},
  {{key:"demanda_dia",id:"hf_demanda_dia",label:"Demanda/día"}},
  {{key:"biz_actuales",id:"hf_biz_actuales",label:"Negocios act."}},
  {{key:"gap",id:"hf_gap",label:"Gap"}},
  {{key:"cobertura_pct",id:"hf_cobertura_pct",label:"Cobertura %"}},
  {{key:"D90",id:"hf_D90",label:"D90"}},
];
var BIZ_FIELDS = [
  {{key:"rating",       id:"bf_rating",    label:"Rating",           fmt:function(v){{return "⭐ "+v;}}}},
  {{key:"capacidad",    id:"bf_capacidad", label:"Capacidad",        fmt:function(v){{return v+" com/día";}}}},
  {{key:"tx_historicas",id:"bf_tx_hist",  label:"Trx históricas"}},
  {{key:"tx_30d",       id:"bf_tx_30d",   label:"Tx 30d"}},
  {{key:"tasa_acepta",  id:"bf_acepta",   label:"Tasa aceptación",  fmt:function(v){{return v+"%";}}}},
  {{key:"tiempo_acepta",id:"bf_tiempo",   label:"T. aceptación p50",fmt:function(v){{return v+" min";}}}},
];
var HUNTER_TIER_MAP = {{
  'A+ Máxima prioridad':'ht_A_crit',
  'A Alta demanda sin supply':'ht_A',
  'B Señal mixta':'ht_B',
  'C Zona activa':'ht_C',
  'D Desarrollo':'ht_D',
  'E Monitoreo':'ht_E',
}};
var SD_TIER_MAP = {{
  'S_A_PLUS':'sd_A_PLUS','S_A_ALTA':'sd_A_ALTA',
  'S_B_MIXTA':'sd_B_MIXTA','S_C_ACTIVA':'sd_C_ACTIVA','S_D_DESARROLLO':'sd_D_DESA',
}};

function buildHexTT(p) {{
  var s = "<b style='color:"+p.fill_color+"'>"+p.zone_tier.replace(/_/g," ")+"</b><br>";
  HEX_FIELDS.forEach(function(f) {{
    var cb = document.getElementById(f.id);
    if (cb && cb.checked) s += "<b>"+f.label+":</b> "+p[f.key]+"<br>";
  }});
  return s;
}}
function buildBizTT(p) {{
  var nivel = p.quality_nivel || '';
  var nivelColor = {{'Excelente':'#22c55e','Alta':'#00BFA5','Media':'#f59e0b','Baja':'#f97316','Crítica':'#ef4444'}}[nivel] || p.fill_color;
  var cohort = p.service_cohort ? "<span style='background:#1e3a52;padding:1px 5px;border-radius:3px;font-size:9px;color:#a78bfa'>"+p.service_cohort+"</span>" : "";
  var s = "<b>"+p.nombre+"</b><br>"+
    "<i style='color:#64748b'>"+p.delegacion+"</i><br>"+
    "<b style='color:#94a3b8;font-size:9px'>Etapa:</b> <span style='font-size:10px'>"+p.etapa+"</span>  "+cohort+"<br>"+
    "<b style='color:"+nivelColor+"'>⭐ Score calidad: "+p.quality_score+"</b> "+
    "<span style='font-size:9px;color:"+nivelColor+"'>("+nivel+")</span><br>"+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:3px 0'>";
  BIZ_FIELDS.forEach(function(f) {{
    var cb = document.getElementById(f.id);
    if (cb && cb.checked) {{ var v=f.fmt?f.fmt(p[f.key]):p[f.key]; s+="<b>"+f.label+":</b> "+v+"<br>"; }}
  }});
  // Menús — always visible
  var mBizne = p.menu_bizne ? "<span style='color:#22c55e'>✅</span>" : "<span style='color:#64748b'>—</span>";
  var mDia   = p.menu_dia   ? "<span style='color:#22c55e'>✅</span>" : "<span style='color:#64748b'>—</span>";
  var mCarta = p.menu_carta ? "<span style='color:#22c55e'>✅</span>" : "<span style='color:#64748b'>—</span>";
  s += "<hr style='border:none;border-top:1px solid #1e3a52;margin:3px 0'>"+
    "<span style='font-size:9px;color:#94a3b8'>MENÚS</span><br>"+
    mBizne+" <b style='font-size:9px'>Bizne</b> &nbsp; "+
    mDia  +" <b style='font-size:9px'>Del día</b> &nbsp; "+
    mCarta+" <b style='font-size:9px'>A la carta</b>";
  return s;
}}
function buildHunterTT(p) {{
  var usrBadge = p.has_users
    ? "<span style='color:#a78bfa'>👤 "+p.usuarios+" usuarios</span>"
    : "<span style='color:#64748b'>Sin usuarios aún</span>";
  var dormColor = p.neg_dormidos > 0 ? '#f59e0b' : '#64748b';
  return "<b style='color:"+p.fill_color+"'>"+p.zona+"</b> · <b>Rank #"+p.rank+"</b><br>"+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    "<b>🏪 Negocios activos:</b> <span style='color:#00BFA5'>"+p.neg_activos+"</span><br>"+
    "<b>😴 Negocios dormidos:</b> <span style='color:"+dormColor+"'>"+p.neg_dormidos+"</span><br>"+
    "<b style='color:#ef4444'>🍽 Gap:</b> <span style='color:#ef4444;font-weight:700'>"+p.gap+" faltantes</span><br>"+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    usrBadge+" · "+p.sin_compras+" sin comprar · Conv: "+p.tasa_conv_pct+"%<br>"+
    "<b>Demanda est.:</b> "+p.demanda_dia+"/día<br>"+
    "<span style='color:#64748b;font-size:9px'>Score: "+Math.round(p.combined_score*100)+"/100</span>";
}}
function buildHeatHexTT(p, label, color) {{
  return "<b style='color:"+color+"'>⬡ "+label+"</b><br>"+
    "<b>Conteo:</b> "+p.count+
    " <span style='color:"+color+";font-weight:700'>("+p.pct_total+"% del total)</span><br>"+
    "<b>Intensidad relativa:</b> "+(Math.round(p.intensity*100))+"% del hex más activo";
}}
function buildSessionDemandTT(p) {{
  var c = p.fill_color;
  var ncTxt = p.n_cercanos === 0
    ? "<span style='color:#ef4444;font-weight:700'>⚠️ Sin negocios en radio 1km</span>"
    : "<span style='color:#22c55e'>"+p.n_cercanos+" negocios en radio 1km</span>";
  var convColor = p.tasa_conv_pct < 30 ? '#ef4444' : p.tasa_conv_pct < 60 ? '#f59e0b' : '#22c55e';
  return "<b style='color:"+c+"'>💡 "+p.tier_label+"</b><br>"+
    "<span style='color:#94a3b8;font-size:9px'>Modelo demanda por sesiones</span>"+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    "<b>👤 Usuarios en zona:</b> <span style='color:#a78bfa;font-weight:700'>"+p.n_users+"</span><br>"+
    "<b>🚫 Sin compras:</b> <span style='color:#f97316'>"+p.sin_compras+"</span><br>"+
    "<b>📈 Conversión:</b> <span style='color:"+convColor+"'>"+p.tasa_conv_pct+"%</span><br>"+
    ncTxt+"<br>"+
    "<b>Score señal:</b> <span style='color:"+c+";font-weight:700'>"+p.score_norm_pct+"%</span>";
}}

window.flyToHunter = function(lat,lng) {{
  if (window.THE_MAP) window.THE_MAP.setView([lat,lng], 14, {{animate:true}});
}};

document.addEventListener("DOMContentLoaded", function() {{
  setTimeout(function() {{
    var theMap = null;
    Object.keys(window).forEach(function(k) {{
      var v = window[k];
      if (v && typeof v==="object" && v._container && v.addLayer) theMap = v;
    }});
    if (!theMap) return;
    window.THE_MAP = theMap;

    // Replace tile with dark CartoDB
    theMap.eachLayer(function(l) {{ if (l._url) theMap.removeLayer(l); }});
    window.TILE_LAYER = L.tileLayer(TILE_DARK,
      {{attribution:'&copy; CartoDB',maxZoom:20}}).addTo(theMap);

    // Panes
    var panes = [['hunterPane',330],['heatHexPane',340],['hexPane',350],['sessionDemandPane',360]];
    panes.forEach(function(item) {{
      var name=item[0], zIdx=item[1];
      if (!theMap.getPane(name)) {{
        theMap.createPane(name);
        theMap.getPane(name).style.zIndex = zIdx;
        if (name !== 'hexPane') theMap.getPane(name).style.pointerEvents = 'auto';
      }}
    }});

    // Session demand (off by default)
    window.LYR_SESSION_DEMAND = L.geoJSON(SESSION_DEMAND_DATA, {{
      pane:'sessionDemandPane',
      style:function(f){{return {{color:f.properties.fill_color,weight:1.0,
        fillColor:f.properties.fill_color,fillOpacity:f.properties.fill_opacity,dashArray:"6 4"}};}},
      onEachFeature:function(f,l){{l._p=f.properties;
        l.bindTooltip(buildSessionDemandTT(f.properties),{{sticky:true,opacity:0.97}});}}
    }});

    // Hunter zones
    window.LYR_HUNTER = L.geoJSON(HUNTER_DATA, {{
      pane:'hunterPane',
      style:function(f){{return {{color:f.properties.fill_color,weight:1.2,
        fillColor:f.properties.fill_color,fillOpacity:f.properties.fill_opacity,dashArray:"4 3"}};}},
      onEachFeature:function(f,l){{l._p=f.properties;
        l.bindTooltip(buildHunterTT(f.properties),{{sticky:true,opacity:0.97}});}}
    }}).addTo(theMap);

    // Demand hexes
    window.LYR_HEX = L.geoJSON(HEX_DATA, {{
      pane:'hexPane',
      style:function(f){{return {{color:f.properties.fill_color,weight:0.5,
        fillColor:f.properties.fill_color,fillOpacity:f.properties.fill_opacity}};}},
      onEachFeature:function(f,l){{l._p=f.properties;
        l.bindTooltip(buildHexTT(f.properties),{{sticky:true,opacity:0.96}});}}
    }}).addTo(theMap);

    // Active businesses
    window.LYR_BIZ = L.geoJSON(BIZ_DATA, {{
      pointToLayer:function(f,ll){{return L.circleMarker(ll,{{radius:5,
        color:f.properties.fill_color,weight:1,fillColor:f.properties.fill_color,fillOpacity:0.8}});}},
      onEachFeature:function(f,l){{l._p=f.properties;
        l.bindTooltip(buildBizTT(f.properties),{{sticky:true,opacity:0.96}});}}
    }}).addTo(theMap);

    // Dormant businesses
    window.LYR_DORM = L.geoJSON(DORM_DATA, {{
      pointToLayer:function(f,ll){{return L.circleMarker(ll,{{radius:5,color:"#6b7280",
        weight:1.5,fillColor:"#9ca3af",fillOpacity:0.55,dashArray:"4"}});}},
      onEachFeature:function(f,l){{var p=f.properties;
        l.bindTooltip("<b>😴 "+p.nombre+"</b><br>Rating: "+p.rating+
          " | Tx hist: "+p.tx_historicas+"<br>Días sin tx: "+p.dias_sin_trx,
          {{sticky:true,opacity:0.97}});}}
    }}).addTo(theMap);

    // Metro
    window.LYR_METRO = L.geoJSON(METRO_DATA, {{
      pointToLayer:function(f,ll){{return L.circleMarker(ll,{{radius:6,
        color:"#0f172a",weight:1.5,fillColor:f.properties.fill_color,fillOpacity:0.9}});}},
      onEachFeature:function(f,l){{var p=f.properties;
        l.bindTooltip("<b style='color:"+p.fill_color+"'>🚇 "+p.nombre+"</b><br>"+
          "<b>Línea</b> <span style='color:"+p.fill_color+";font-weight:700'>"+p.linea+"</span>"+
          (p.transbordos>0 ? " · <span style='color:#f59e0b'>⇄ "+p.transbordos+" transbordos</span>" : "")+"<br>"+
          "Elementos est.: "+p.elementos,{{sticky:true,opacity:0.97}});}}
    }}).addTo(theMap);

    // UPCs
    window.LYR_UPCS = L.geoJSON(UPC_DATA, {{
      pointToLayer:function(f,ll){{return L.circleMarker(ll,{{radius:7,
        color:"#4c1d95",weight:2,fillColor:"#7C3AED",fillOpacity:0.85}});}},
      onEachFeature:function(f,l){{var p=f.properties;
        l.bindTooltip("<b style='color:#a78bfa'>🛡 "+p.nombre+"</b><br>"+p.address,
          {{sticky:true,opacity:0.97,maxWidth:220}});}}
    }}).addTo(theMap);

    // Sectores PA
    window.LYR_SEC = L.geoJSON(SEC_DATA, {{
      pointToLayer:function(f,ll){{return L.circleMarker(ll,{{radius:9,
        color:"#164e63",weight:2,fillColor:"#06b6d4",fillOpacity:0.7}});}},
      onEachFeature:function(f,l){{var p=f.properties;
        l.bindTooltip("<b style='color:#22d3ee'>🏢 "+p.indicativo+"</b><br>"+p.sector+
          "<br>Elementos: "+p.elementos+"<br>Demanda/día: "+p.demanda_dia,
          {{sticky:true,opacity:0.97}});}}
    }}).addTo(theMap);

    // Heat maps (smooth)
    window.LYR_HEAT_OK   = L.heatLayer(HEAT_TRX_OK,  {{radius:20,blur:15,maxZoom:14,gradient:{{0.4:'#22c55e',0.7:'#86efac',1:'#fff'}}}});
    window.LYR_HEAT_FAIL = L.heatLayer(HEAT_TRX_FAIL,{{radius:20,blur:15,maxZoom:14,gradient:{{0.4:'#ef4444',0.7:'#fca5a5',1:'#fff'}}}});
    window.LYR_HEAT_USERS= L.heatLayer(HEAT_USERS,   {{radius:25,blur:18,maxZoom:14,gradient:{{0.4:'#7c3aed',0.65:'#a78bfa',1:'#fff'}}}});

    // Hex heat layers
    window.LYR_HHEX_OK = L.geoJSON(HEX_HEAT_OK, {{
      pane:'heatHexPane',
      style:function(f){{return {{color:'#22c55e',weight:0.5,fillColor:'#22c55e',fillOpacity:f.properties.fill_opacity}};}},
      onEachFeature:function(f,l){{l.bindTooltip(buildHeatHexTT(f.properties,'Tx completadas','#22c55e'),{{sticky:true,opacity:0.96}});}}
    }});
    window.LYR_HHEX_FAIL = L.geoJSON(HEX_HEAT_FAIL, {{
      pane:'heatHexPane',
      style:function(f){{return {{color:'#ef4444',weight:0.5,fillColor:'#ef4444',fillOpacity:f.properties.fill_opacity}};}},
      onEachFeature:function(f,l){{l.bindTooltip(buildHeatHexTT(f.properties,'Tx incompletas','#ef4444'),{{sticky:true,opacity:0.96}});}}
    }});
    window.LYR_HHEX_USERS = L.geoJSON(HEX_HEAT_USERS, {{
      pane:'heatHexPane',
      style:function(f){{return {{color:'#a78bfa',weight:0.5,fillColor:'#a78bfa',fillOpacity:f.properties.fill_opacity}};}},
      onEachFeature:function(f,l){{l.bindTooltip(buildHeatHexTT(f.properties,'Última sesión','#a78bfa'),{{sticky:true,opacity:0.96}});}}
    }});

    // Toggle helpers
    window.toggleLayer = function(name, show) {{
      var m = window.THE_MAP; if (!m) return;
      var map = {{hexes:window.LYR_HEX,activos:window.LYR_BIZ,dormidas:window.LYR_DORM,
                 hunter:window.LYR_HUNTER,sdemand:window.LYR_SESSION_DEMAND,
                 metro:window.LYR_METRO,upcs:window.LYR_UPCS,sec:window.LYR_SEC}};
      var lyr = map[name]; if (!lyr) return;
      show ? (!m.hasLayer(lyr) && m.addLayer(lyr)) : (m.hasLayer(lyr) && m.removeLayer(lyr));
    }};
    window.toggleHeat = function(name, show) {{
      var m = window.THE_MAP; if (!m) return;
      var map = {{ok:window.LYR_HEAT_OK,fail:window.LYR_HEAT_FAIL,users:window.LYR_HEAT_USERS}};
      var lyr = map[name]; if (!lyr) return;
      show ? (!m.hasLayer(lyr) && m.addLayer(lyr)) : (m.hasLayer(lyr) && m.removeLayer(lyr));
    }};
    window.toggleHexHeat = function(name, show) {{
      var m = window.THE_MAP; if (!m) return;
      var map = {{ok:window.LYR_HHEX_OK,fail:window.LYR_HHEX_FAIL,users:window.LYR_HHEX_USERS}};
      var lyr = map[name]; if (!lyr) return;
      show ? (!m.hasLayer(lyr) && m.addLayer(lyr)) : (m.hasLayer(lyr) && m.removeLayer(lyr));
    }};
    window.filterTiers = function() {{
      if (!window.LYR_HEX) return;
      window.LYR_HEX.eachLayer(function(layer) {{
        if (!layer.feature) return;
        var tier = layer.feature.properties.zone_tier;
        var cb = document.getElementById("tier_"+tier);
        var show = cb ? cb.checked : true;
        layer.setStyle({{fillOpacity:show?layer.feature.properties.fill_opacity:0,
                         opacity:show?1:0,interactive:show}});
      }});
    }};
    window.filterHunters = function() {{
      if (!window.LYR_HUNTER) return;
      window.LYR_HUNTER.eachLayer(function(layer) {{
        if (!layer.feature) return;
        var zona = layer.feature.properties.zona;
        var cbId = HUNTER_TIER_MAP[zona];
        var show = cbId ? (document.getElementById(cbId)?document.getElementById(cbId).checked:true) : true;
        layer.setStyle({{fillOpacity:show?layer.feature.properties.fill_opacity:0,
                         opacity:show?1:0,interactive:show}});
      }});
    }};
    window.filterSessionDemand = function() {{
      if (!window.LYR_SESSION_DEMAND) return;
      window.LYR_SESSION_DEMAND.eachLayer(function(layer) {{
        if (!layer.feature) return;
        var tid = layer.feature.properties.tier_id;
        var cbId = SD_TIER_MAP[tid];
        var show = cbId ? (document.getElementById(cbId)?document.getElementById(cbId).checked:true) : true;
        layer.setStyle({{fillOpacity:show?layer.feature.properties.fill_opacity:0,
                         opacity:show?1:0,interactive:show}});
      }});
    }};
    window.searchNegocios = function(q) {{
      if (!window.LYR_BIZ) return;
      q = q.toLowerCase().trim();
      var vis=0, tot=0;
      window.LYR_BIZ.eachLayer(function(layer) {{
        tot++;
        var show = q==='' || (layer._p && layer._p.nombre.toLowerCase().indexOf(q)>=0);
        layer.setStyle({{fillOpacity:show?0.8:0,opacity:show?1:0,interactive:show}});
        if(show) vis++;
      }});
      var el=document.getElementById('biz-count');
      if(el){{el.textContent=q===''?'':vis+' de '+tot+' negocios';
              el.style.color=vis===0?'#dc2626':'#64748b';}}
    }};
    window.updateHexTT = function() {{
      if(!window.LYR_HEX) return;
      window.LYR_HEX.eachLayer(function(l){{if(l._p)l.setTooltipContent(buildHexTT(l._p));}});
    }};
    window.updateBizTT = function() {{
      if(!window.LYR_BIZ) return;
      window.LYR_BIZ.eachLayer(function(l){{if(l._p)l.setTooltipContent(buildBizTT(l._p));}});
    }};

    // Draggable panels
    function draggable(el, hdr) {{
      if (!el || !hdr) return;
      hdr.addEventListener('mousedown', function(e) {{
        e.preventDefault();
        var ox=e.clientX-el.getBoundingClientRect().left;
        var oy=e.clientY-el.getBoundingClientRect().top;
        function drag(e2){{el.style.left=(e2.clientX-ox)+'px';el.style.top=(e2.clientY-oy)+'px';el.style.right='auto';el.style.bottom='auto';}}
        document.addEventListener('mousemove',drag);
        document.addEventListener('mouseup',function(){{document.removeEventListener('mousemove',drag);}},{{once:true}});
      }});
    }}
    draggable(document.getElementById('kpi-dash'),document.getElementById('kpi-dash-header'));
    draggable(document.getElementById('bmap-panel'),document.getElementById('bmap-header'));
    draggable(document.getElementById('hunter-panel'),document.getElementById('hunter-header'));

    console.log('✅ Bizne Map v5 loaded · HEX:{len(hex_features)} · BIZ:{len(biz_features)} · HUNTER:{len(hunter_features)} · SD:{len(sd_features)}');
  }}, 600);
}});
</script>"""

# ══════════════════════════════════════════════════════════════════════
# ASSEMBLE FINAL HTML
# ══════════════════════════════════════════════════════════════════════
BASE = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bizne PA — Mapa de Demanda · Mayo 2026</title>
{HEAD}
<style>
html,body{{margin:0;padding:0;width:100%;height:100%;background:#0f172a;}}
#map{{width:100%;height:100vh;}}
</style>
</head>
<body>
{KPI_HTML}
{HUNTER_HTML}
{PANEL_HTML}
{GUIDE_HTML}
<div id="map"></div>
<script>
var _bizneMap = L.map('map', {{center:[19.42,-99.13],zoom:11,zoomControl:true}});
</script>
{JS}
</body>
</html>"""

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(BASE)

size_kb = len(BASE)//1024
print(f"\n✅ Output: {OUT}")
print(f"   Size: {size_kb} KB")

# Verify
checks = [
    ('kpi-dash', 'Dashboard KPIs'),
    ('bmap-panel', 'Cartboard'),
    ('hunter-panel', 'Panel Hunter'),
    ('mode-btn', 'Modo oscuro'),
    ('guide-btn-wrap', 'Guía ?'),
    ('switchTab', 'Tabs guía'),
    ('LYR_SESSION_DEMAND', 'Capa sesiones'),
    ('LYR_HUNTER', 'Capa hunter'),
    ('LYR_METRO', 'Metro'),
    ('LYR_UPCS', 'UPCs'),
    ('LYR_HEAT_OK', 'Heat OK'),
    ('toggleMode', 'toggleMode'),
    ('filterHunters', 'filterHunters'),
    ('filterSessionDemand', 'filterSessionDemand'),
    ('flyToHunter', 'flyToHunter'),
    ('draggable', 'Draggable'),
    ('CartoDB', 'CartoDB tiles'),
    ('gpanel-kpis', 'Guía KPIs tab'),
    ('gpanel-zonas', 'Guía zonas tab'),
    ('gpanel-uso', 'Guía uso tab'),
]
all_ok = True
for pattern, label in checks:
    ok = pattern in BASE
    if not ok: all_ok = False
    print(f"  {'✅' if ok else '❌'} {label}")
print(f"\n{'✅ ALL OK' if all_ok else '❌ ISSUES FOUND'}")
