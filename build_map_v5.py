"""
build_map_v5.py — Rebuilds the full Bizne interactive map (Mayo 25 data, Búfalo fix)
Features: Dashboard KPIs · Dashboard Hunter · Modo Oscuro · Guía · Session Demand · Heat Maps
"""
import json, math
import pandas as pd
import numpy as np
from math import ceil as import_ceil
import h3
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────
import os as _os
_CI  = _os.environ.get('GITHUB_ACTIONS_RUN') == 'true'
_DIR = _os.path.dirname(_os.path.abspath(__file__)) if _CI else None

if _CI:
    # CI: archivos generados por bizne_model_ci.py en el mismo directorio del repo
    OUT      = _os.path.join(_DIR, 'index.html')
    ANALYTICS= _os.path.join(_DIR, 'pg_usuarios_cache.csv')
    HEX_CSV  = _os.path.join(_DIR, 'kepler_real_hex_demanda.csv')
    NEG_CSV  = _os.path.join(_DIR, 'kepler_real_negocios.csv')
    DORM_CSV = _os.path.join(_DIR, 'kepler_real_dormidas.csv')
    QS_CSV   = None   # CI: campos de calidad vienen directo en NEG_CSV
    METRO_CSV= _os.path.join(_DIR, 'kepler_real_metro.csv')
    SEC_CSV  = _os.path.join(_DIR, 'kepler_real_sectores.csv')
    UPC_CSV  = _os.path.join(_DIR, 'data', 'upcs.csv')   # actualizado por bizne_model_ci.py desde BD
    TRX_CSV  = _os.path.join(_DIR, 'pg_transacciones_cache.csv')
    TRX_HIST_CSVS = []   # CI: solo pg_transacciones_cache.csv es la fuente — histórico viene de BD
    ACTIV_CSV= _os.path.join(_DIR, 'data', 'puntos_activacion.csv')
    UPC_SWAPPED = False   # data/upcs.csv tiene coords correctas (lat=Y, lng=X)
else:
    # Local dev paths
    OUT      = '/sessions/confident-jolly-pasteur/mnt/outputs/bizne_mapa_v5.html'
    ANALYTICS= '/sessions/confident-jolly-pasteur/mnt/outputs/pg_usuarios_cache.csv'
    HEX_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_hex_demanda.csv'
    NEG_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_negocios.csv'
    DORM_CSV = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_dormidas.csv'
    QS_CSV   = '/sessions/confident-jolly-pasteur/mnt/uploads/Quality_Socre_-_Gamification_(+_carga_a_Menus)_2026_06_02.csv'
    METRO_CSV= '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_metro.csv'
    SEC_CSV  = '/sessions/confident-jolly-pasteur/mnt/outputs/kepler_real_sectores.csv'
    UPC_CSV  = '/sessions/confident-jolly-pasteur/mnt/uploads/Policía_UPCs_Data_Maps_2025_12_15.csv'
    TRX_CSV  = '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_-_Last_30_days_2026_06_02.csv'
    TRX_HIST_CSVS = [
        '/sessions/confident-jolly-pasteur/mnt/uploads/data_transacciones.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_2026_05_04.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_2026_05_11.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_2026_05_12 (2).csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_2026_05_13.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_-_Last_30_days_2026_05_24.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_-_Last_30_days_2026_05_28.csv',
        '/sessions/confident-jolly-pasteur/mnt/uploads/Coordinates_Trxs_-_Last_30_days_2026_06_02.csv',
    ]
    ACTIV_CSV= '/sessions/confident-jolly-pasteur/mnt/uploads/PA_Proyeccion_13sem - Puntos de Activación (3).csv'
    UPC_SWAPPED = True   # CSV original tiene lat/lng intercambiados
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
# 1. ANALYTICS DATA — May 28
# ══════════════════════════════════════════════════════════════════════
print("Loading analytics…")
df_u = pd.read_csv(ANALYTICS, encoding='utf-8')
df_u.columns = df_u.columns.str.strip()
# Normalize column names — Analytics CSV may use *_last_session suffix
if 'latitude' not in df_u.columns and 'latitude_last_session' in df_u.columns:
    df_u['latitude']  = df_u['latitude_last_session']
    df_u['longitude'] = df_u['longitude_last_session']
df_u['lat'] = pd.to_numeric(df_u['latitude'], errors='coerce')
df_u['lng'] = pd.to_numeric(df_u['longitude'], errors='coerce')
df_u['transacciones'] = pd.to_numeric(df_u['transacciones'], errors='coerce').fillna(0)
df_u['consumo_total'] = pd.to_numeric(df_u['consumo_total'], errors='coerce').fillna(0)
df_u['days_to_first_trx'] = pd.to_numeric(df_u['days_to_first_trx'], errors='coerce')
df_u['created_date'] = pd.to_datetime(df_u['created_date'], utc=True, errors='coerce').dt.tz_localize(None)

df_aprov = df_u[df_u['kyc_status']=='APPROVED'].copy()

# KPI calculations
signups_total = len(df_u)
aprobados = len(df_aprov)
ap_pct = round(aprobados/signups_total*100,1) if signups_total > 0 else 0

# Transactions
df_trx = pd.read_csv(TRX_CSV, encoding='utf-8')
df_trx.columns = df_trx.columns.str.strip()
df_trx['status_trx'] = df_trx['status_trx'].fillna('')
trx_fail = (df_trx['status_trx'].str.contains('incompleta', case=False)).sum()
trx_ok   = (df_trx['status_trx'].str.contains('completa', case=False) &
            ~df_trx['status_trx'].str.contains('incompleta', case=False)).sum()

# Tx Policía Auxiliar por negocio (últimos 30 días, sólo completas)
_trx_pa = df_trx[
    df_trx['organizacion'].str.contains('Policia Auxiliar|Policía Auxiliar', case=False, na=False) &
    df_trx['status_trx'].str.contains('completa', case=False) &
    ~df_trx['status_trx'].str.contains('incompleta', case=False)
]
tx_pa_by_service = _trx_pa.groupby('service_id').size().to_dict()

# Tx PA históricas — combinar todos los CSVs disponibles, deduplicar por id
_hist_dfs = []
for _f in TRX_HIST_CSVS:
    try:
        _d = pd.read_csv(_f, encoding='utf-8')
        _d.columns = _d.columns.str.strip()
        _hist_dfs.append(_d)
    except Exception:
        pass
if not _hist_dfs:
    _hist_dfs = [df_trx]   # fallback: solo el CSV actual
_df_hist_all = pd.concat(_hist_dfs).drop_duplicates(subset='id')
_trx_pa_hist = _df_hist_all[
    _df_hist_all['organizacion'].str.contains('Policia Auxiliar|Policía Auxiliar', case=False, na=False) &
    _df_hist_all['status_trx'].str.contains('completa', case=False) &
    ~_df_hist_all['status_trx'].str.contains('incompleta', case=False)
]
tx_pa_hist_by_service = _trx_pa_hist.groupby('service_id').size().to_dict()
print(f"  Tx PA históricas: {len(_trx_pa_hist)} (dedup) en {_trx_pa_hist['service_id'].nunique()} negocios")

# Tasa aceptación
tasa_aceptacion = round(trx_ok/(trx_ok+trx_fail)*100, 1) if (trx_ok+trx_fail) > 0 else 0

# Tiempo promedio de aceptación — calculado del QS CSV o NEG_CSV (mediana negocios con dato válido)
try:
    _src_t = QS_CSV if (QS_CSV and _os.path.exists(QS_CSV)) else NEG_CSV
    _df_qs_t = pd.read_csv(_src_t)
    _df_qs_t.columns = _df_qs_t.columns.str.strip()
    _tcol = 'tiempo_p50_aceptacion_min_ultimos_30_dias' if 'tiempo_p50_aceptacion_min_ultimos_30_dias' in _df_qs_t.columns else 'tiempo_acepta'
    _t = pd.to_numeric(_df_qs_t[_tcol], errors='coerce').dropna()
    _t_clean = _t[(_t > 0) & (_t < 60)]
    tiempo_prom_accept = round(_t_clean.median(), 1) if len(_t_clean) > 0 else 0.0
except Exception:
    tiempo_prom_accept = 0.0

# Conversión primer consumo (approved with at least 1 tx)
aprov_con_tx = (df_aprov['transacciones'] > 0).sum()
conv_primer = round(aprov_con_tx/aprobados*100,1) if aprobados > 0 else 0
aprov_sin_conv = round(100 - conv_primer, 1)

# Conversión registrados → 1ª transacción (sobre total de signups)
conv_registrados_tx = round(aprov_con_tx/signups_total*100,1) if signups_total > 0 else 0

# Días promedio al primer consumo (post April 20)
cutoff = pd.Timestamp('2026-04-20')
df_post = df_aprov[(df_aprov['created_date'] >= cutoff) & (df_aprov['transacciones'] > 0)]
dias_prom = round(df_post['days_to_first_trx'].dropna().mean(), 1) if len(df_post) > 0 else 0

# % last session sin supply (users with location but no nearby businesses)
df_aprov_loc = df_aprov[df_aprov['lat'].notna() & (df_aprov['lat'] != 0)].copy()
df_aprov_loc['hex_id'] = df_aprov_loc.apply(lambda r: safe_h3(r['lat'], r['lng']), axis=1)

# Businesses per hex
df_neg = pd.read_csv(NEG_CSV, encoding='utf-8')
df_neg['hex_id'] = df_neg.apply(lambda r: safe_h3(r['lat'], r['lng']), axis=1)
biz_per_hex = df_neg.groupby('hex_id').size().to_dict()
def biz_nearby(hex_id):
    if not hex_id: return 0
    return sum(biz_per_hex.get(h,0) for h in h3.grid_disk(hex_id, 1))

df_aprov_loc['n_biz'] = df_aprov_loc['hex_id'].apply(biz_nearby)
sin_supply = (df_aprov_loc['n_biz'] == 0).sum()
pct_sin_supply = round(sin_supply/len(df_aprov_loc)*100,1) if len(df_aprov_loc) > 0 else 0

# ── KPI por organización × rango de fecha ─────────────────────────────────────
_ORG_COL   = 'organization_name' if 'organization_name' in df_u.columns else None
_orgs_list = sorted(df_u[_ORG_COL].dropna().unique().tolist()) if _ORG_COL else []
_all_orgs  = ['Todas'] + _orgs_list

# Rangos de fecha: filtro por created_date del usuario
_now_ts = pd.Timestamp.now().tz_localize(None)
_DATE_RANGES = {
    '7d':   _now_ts - pd.Timedelta(days=7),
    '30d':  _now_ts - pd.Timedelta(days=30),
    '90d':  _now_ts - pd.Timedelta(days=90),
    '180d': _now_ts - pd.Timedelta(days=180),
    'todo': None,
}

def _kpi_for_group(df_grp):
    """Calcula métricas de usuarios para un subset ya filtrado (org × fecha)."""
    df_ap = df_grp[df_grp['kyc_status'] == 'APPROVED']
    n_sig = len(df_grp)
    n_ap  = len(df_ap)
    n_tx  = int((df_ap['transacciones'] > 0).sum())
    conv_p = round(n_tx / n_ap * 100, 1) if n_ap > 0 else 0.0
    cutoff_first = pd.Timestamp('2026-04-20')
    df_post = df_ap[(df_ap['created_date'] >= cutoff_first) & (df_ap['transacciones'] > 0)] \
              if 'created_date' in df_ap.columns else pd.DataFrame()
    dias_v = float(round(df_post['days_to_first_trx'].dropna().mean(), 1)) if len(df_post) > 0 else 0.0
    if pd.isna(dias_v): dias_v = 0.0
    df_loc = df_ap[df_ap['lat'].notna() & (df_ap['lat'] != 0)].copy()
    if len(df_loc) > 0:
        df_loc['_hx'] = df_loc.apply(lambda r: safe_h3(r['lat'], r['lng']), axis=1)
        df_loc['_nb'] = df_loc['_hx'].apply(biz_nearby)
        pct_sup = round((df_loc['_nb'] == 0).sum() / len(df_loc) * 100, 1)
    else:
        pct_sup = 0.0
    return {
        'signups':        n_sig,
        'aprobados':      n_ap,
        'ap_pct':         round(n_ap / n_sig * 100, 1) if n_sig > 0 else 0.0,
        'conv_primer':    conv_p,
        'aprov_sin':      round(100 - conv_p, 1),
        'conv_reg':       round(n_tx / n_sig * 100, 1) if n_sig > 0 else 0.0,
        'dias_prom':      dias_v,
        'pct_sin_supply': pct_sup,
    }

# Pre-computar ORG_DATE_KPI_DATA[org][dateRange]
_org_date_kpi = {}
for _org in _all_orgs:
    _df_org = df_u if _org == 'Todas' else df_u[df_u[_ORG_COL] == _org]
    _org_date_kpi[_org] = {}
    for _rng, _cutoff in _DATE_RANGES.items():
        if _cutoff is not None and 'created_date' in _df_org.columns:
            _df_rng = _df_org[_df_org['created_date'] >= _cutoff]
        else:
            _df_rng = _df_org
        _org_date_kpi[_org][_rng] = _kpi_for_group(_df_rng)

ORG_DATE_KPI_DATA = json.dumps(_org_date_kpi, ensure_ascii=False)
ORG_LIST_JSON     = json.dumps(_all_orgs, ensure_ascii=False)
print(f"  Orgs: {_all_orgs} | Rangos: {list(_DATE_RANGES.keys())}")

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
    'conv_registrados_tx': conv_registrados_tx,
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
# Ordenar por hex_id SIEMPRE antes de asignar HEX-XXXX
# — hex_id es un string H3 estable y único, así los códigos nunca cambian entre builds
df_hex = df_hex.sort_values('hex_id').reset_index(drop=True)

hex_features = []
_hex_seq = 1   # contador global para HEX-XXXX
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
            "hex_id":   str(row['hex_id']),
            "hex_code": f"HEX-{_hex_seq:04d}",
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
    _hex_seq += 1

HEX_DATA = json.dumps({"type":"FeatureCollection","features":hex_features}, ensure_ascii=False)
print(f"  {len(hex_features)} hexes")

# ══════════════════════════════════════════════════════════════════════
# 2b. PUNTOS DE ACTIVACIÓN — capa nueva + boost demanda en hexes
# ══════════════════════════════════════════════════════════════════════
print("Building ACTIV_DATA…")
ACTIV_ELEMENTOS = 150
ACTIV_CONV      = 0.30
ACTIV_TX_DAY    = 6 / 30          # 0.20 tx/usuario/día
ACTIV_DEM_BASE  = ACTIV_ELEMENTOS * ACTIV_CONV * ACTIV_TX_DAY  # 9 tx/día
ACTIV_DECAY     = {0: 1.0, 1: 0.65, 2: 0.35}                   # ~2 km radio total

activ_features = []
activ_dem_hex  = defaultdict(float)   # hex_id → demanda_activacion acumulada

try:
    df_activ = pd.read_csv(ACTIV_CSV)
    for _, row in df_activ.iterrows():
        lat_a, lng_a = float(row['Lat']), float(row['Long'])
        sector_a = str(row.get('Sector', '')).strip()
        is_admin = sector_a.lower() == 'admin'
        try:
            center_hex = h3.latlng_to_cell(lat_a, lng_a, H3_RES)
        except Exception:
            continue
        dem_punto = round(ACTIV_DEM_BASE, 1)
        # Solo inyectar demanda en puntos de campo (no Admin)
        if not is_admin:
            for ring_k, decay in ACTIV_DECAY.items():
                ring = [center_hex] if ring_k == 0 else list(h3.grid_ring(center_hex, ring_k))
                for hx in ring:
                    activ_dem_hex[hx] += ACTIV_DEM_BASE * decay
        # Feature para la capa visual
        activ_features.append({
            "type": "Feature",
            "geometry": {"type":"Point","coordinates":[lng_a, lat_a]},
            "properties": {
                "nombre":      str(row['Nombre']),
                "sector":      sector_a,
                "direccion":   str(row.get('Dirección', row.get('Direccion',''))),
                "es_admin":    is_admin,
                "elementos_est": 0 if is_admin else ACTIV_ELEMENTOS,
                "dem_dia_est": 0.0 if is_admin else dem_punto,
                "radio_km":    0.0 if is_admin else 2.0,
            }
        })
    # Boost a hex_features ya construidos
    hex_by_id = {f['properties']['hex_id']: f for f in hex_features}
    boosted = 0
    for hx, dem in activ_dem_hex.items():
        if hx in hex_by_id:
            hex_by_id[hx]['properties']['demanda_activacion'] = round(dem, 1)
            hex_by_id[hx]['properties']['demanda_dia'] = round(
                hex_by_id[hx]['properties']['demanda_dia'] + dem, 1)
            boosted += 1
    print(f"  {len(activ_features)} puntos de activación · {boosted} hexes boosteados")
except Exception as e:
    print(f"  ⚠ ACTIV_CSV no cargado: {e}")

ACTIV_DATA = json.dumps({"type":"FeatureCollection","features":activ_features}, ensure_ascii=False)
# Rebuild HEX_DATA con boost incorporado
HEX_DATA = json.dumps({"type":"FeatureCollection","features":hex_features}, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════════════
# 3. BIZ DATA — active businesses (colored by kitchen_quality_score)
# ══════════════════════════════════════════════════════════════════════
print("Building BIZ_DATA…")

def _safe_bool(val):
    if pd.isna(val): return False
    if isinstance(val, bool): return val
    return str(val).strip().lower() in ('true','1','yes','sí','si')

def _qs_nivel(score):
    if score >= 80: return 'Excelente'
    if score >= 60: return 'Alta'
    if score >= 40: return 'Media'
    if score >= 20: return 'Baja'
    return 'Crítica'

# Load quality scores — from separate QS CSV (local) or from NEG_CSV (CI)
qs_lookup = {}
if QS_CSV and _os.path.exists(QS_CSV):
    df_qs = pd.read_csv(QS_CSV)
    df_qs.columns = df_qs.columns.str.strip()
    for _, r in df_qs.iterrows():
        name_key = str(r.get('name','')).strip().lower()
        qs_lookup[name_key] = {
            'score':           float(r.get('kitchen_quality_score', 0) or 0),
            'nivel':           str(r.get('kitchen_quality_nivel','') or ''),
            'etapa':           str(r.get('etapa_negocio','') or ''),
            'service_cohort':  str(r.get('service_cohort','') or ''),
            'tasa_acepta':     round(float(r.get('tasa_aceptacion_ultimos_30_dias', 0) or 0)*100, 1),
            'tx_30d':          int(float(r.get('transacciones_ultimos_30_dias', 0) or 0)),
            'tx_90d':          int(float(r.get('transacciones_ultimos_90_dias', 0) or 0)),
            'tx_historicas':   int(float(r.get('transacciones_historicas', 0) or 0)),
            'tiempo_acepta':   round(float(r.get('tiempo_p50_aceptacion_min_ultimos_30_dias', 0) or 0), 1),
            'menu_bizne':      _safe_bool(r.get('menu_bizne')),
            'menu_dia':        _safe_bool(r.get('menu_de_dia')),
            'menu_carta':      _safe_bool(r.get('menu_a_la_carta')),
            # Campos de exportación CSV
            'service_id':      int(float(r.get('service_id', 0) or 0)),
            'phone_number':    str(r.get('phone_number','') or ''),
            'owner_name':      str(r.get('owner_name','') or ''),
            'hunter':          str(r.get('hunter','') or ''),
            'address':         str(r.get('address','') or ''),
            'colonia':         str(r.get('colonia','') or ''),
            'creation_date':   str(r.get('bizne_creation_date','') or ''),
            'dias_creacion':   int(float(r.get('dias_desde_creacion', 0) or 0)),
            'food_types':      str(r.get('food_types','') or ''),
            'horario':         str(r.get('schedule', r.get('horario','')) or ''),
        }
    print(f"  QS lookup from QS_CSV: {len(qs_lookup)} negocios")
else:
    # CI mode: build QS lookup from NEG_CSV (campos de calidad ya incluidos por bizne_model_ci.py)
    # Columnas en kepler_real_negocios.csv ya están renombradas por bizne_model_ci.py:
    #   tx_30d, tx_90d, tx_historicas, tasa_aceptacion, tiempo_acepta, dias_creacion, creation_date
    print("  QS_CSV not available — building QS lookup from NEG_CSV")
    for _, r in df_neg.iterrows():
        name_key = str(r.get('name','')).strip().lower()
        score = float(r.get('kitchen_quality_score', 0) or 0)
        nivel = str(r.get('kitchen_quality_nivel', '') or '') or _qs_nivel(score)
        qs_lookup[name_key] = {
            'score':          score,
            'nivel':          nivel,
            'etapa':          str(r.get('etapa_negocio','') or ''),
            'service_cohort': str(r.get('service_cohort','') or ''),
            'tasa_acepta':    round(float(r.get('tasa_aceptacion', 0) or 0)*100, 1),
            'tx_30d':         int(float(r.get('tx_30d', 0) or 0)),
            'tx_historicas':  int(float(r.get('tx_historicas', 0) or 0)),
            'tx_hist_real':   int(float(r.get('tx_hist_real', r.get('tx_historicas', 0)) or 0)),
            'tx_90d':         int(float(r.get('tx_90d', 0) or 0)),
            'tiempo_acepta':  round(float(r.get('tiempo_acepta', 0) or 0), 1),
            'menu_bizne':     _safe_bool(r.get('menu_bizne')),
            'menu_dia':       _safe_bool(r.get('menu_de_dia')),
            'menu_carta':     _safe_bool(r.get('menu_a_la_carta')),
            'dias_creacion':  int(float(r.get('dias_creacion', 9999) or 9999)),
            'creation_date':  str(r.get('creation_date', '') or ''),
            'hunter':         str(r.get('hunter', '') or ''),
            'service_id':     int(float(r.get('service_id', 0) or 0)),
            'phone_number':   str(r.get('phone_number', '') or ''),
            'owner_name':     str(r.get('owner_name', '') or ''),
            'address':        str(r.get('address', '') or ''),
            'colonia':        str(r.get('colonia', '') or ''),
            'food_types':     str(r.get('food_types', '') or ''),
            'horario':        str(r.get('horario', r.get('schedule', '')) or ''),
        }
    print(f"  QS lookup from NEG_CSV: {len(qs_lookup)} negocios")

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
            "tx_pa_30d":      int(tx_pa_by_service.get(int(qs_data.get('service_id', 0) or 0), 0)),
            "tx_pa_hist":     int(tx_pa_hist_by_service.get(int(qs_data.get('service_id', 0) or 0), 0)),
            "dormida":        bool(qs_data.get('dormida', False)),
            "tx_historicas":  qs_data.get('tx_historicas', 0),
            "tx_hist_real":   qs_data.get('tx_hist_real', qs_data.get('tx_historicas', 0)),
            "ventas_30d":     round(float(row.get('ventas_30d', 0) or 0), 0),
            "tasa_acepta":    qs_data.get('tasa_acepta', round(float(row.get('tasa_aceptacion', 0) or 0)*100, 1)),
            "tiempo_acepta":  qs_data.get('tiempo_acepta', 0),
            "menu_bizne":    qs_data.get('menu_bizne', False),
            "menu_dia":      qs_data.get('menu_dia', False),
            "menu_carta":    qs_data.get('menu_carta', False),
            "fill_color":    fill,
            # Campos de exportación CSV
            "service_id":    qs_data.get('service_id', 0),
            "phone_number":  qs_data.get('phone_number', ''),
            "owner_name":    qs_data.get('owner_name', ''),
            "hunter":        qs_data.get('hunter', ''),
            "address":       qs_data.get('address', ''),
            "colonia":       qs_data.get('colonia', ''),
            "tx_90d":        qs_data.get('tx_90d', 0),
            "creation_date": qs_data.get('creation_date', ''),
            "dias_creacion": qs_data.get('dias_creacion', 0),
            "food_types":    qs_data.get('food_types', str(row.get('food_types',''))),
            "horario":       qs_data.get('horario', str(row.get('horario', row.get('schedule','')))),
            "lat":           round(float(row['lat']), 5),
            "lng":           round(float(row['lng']), 5),
        }
    }
    biz_features.append(feat)

BIZ_DATA = json.dumps({"type":"FeatureCollection","features":biz_features}, ensure_ascii=False)
# Score distribution
from collections import Counter
niveles = Counter(f['properties']['quality_nivel'] for f in biz_features)
print(f"  {len(biz_features)} negocios activos · scores: {dict(niveles)}")

# ── Métricas de Quality Score ──────────────────────────────────────────────
_QS_ORDER  = ['Excelente', 'Alta', 'Media', 'Baja', 'Crítica']
_QS_COLORS = {'Excelente':'#16a34a','Alta':'#00897B','Media':'#d97706','Baja':'#f97316','Crítica':'#dc2626'}
_qs_scores  = [float(f['properties'].get('quality_score', 0) or 0) for f in biz_features]
_qs_total   = len(_qs_scores)
_qs_avg     = round(sum(_qs_scores) / _qs_total, 1) if _qs_total > 0 else 0
_qs_median  = round(sorted(_qs_scores)[_qs_total//2], 1) if _qs_total > 0 else 0
_qs_dist    = {n: niveles.get(n, 0) for n in _QS_ORDER}  # count per tier
_qs_pct     = {n: round(_qs_dist[n] / _qs_total * 100, 1) for n in _QS_ORDER} if _qs_total > 0 else {n:0 for n in _QS_ORDER}
# Negocios excelentes+altos (score ≥ 60)
_qs_saludables = _qs_dist.get('Excelente',0) + _qs_dist.get('Alta',0)
_qs_pct_saludables = round(_qs_saludables / _qs_total * 100, 1) if _qs_total > 0 else 0
print(f"  Quality avg:{_qs_avg} | median:{_qs_median} | saludables:{_qs_saludables} ({_qs_pct_saludables}%)")

# ══════════════════════════════════════════════════════════════════════
# 4. DORM DATA — dormant businesses
# ══════════════════════════════════════════════════════════════════════
print("Building DORM_DATA…")
dorm_features = []
for _, row in df_dorm.iterrows():
    name_key  = str(row.get('name','')).strip().lower()
    qs_data   = qs_lookup.get(name_key, {})
    sid       = int(qs_data.get('service_id', 0) or 0)
    feat = {
        "type":"Feature",
        "geometry":{"type":"Point","coordinates":[float(row['lng']),float(row['lat'])]},
        "properties":{
            "nombre":        str(row.get('name','')),
            "delegacion":    str(row.get('delegacion','')),
            "rating":        float(row.get('rating', 0)),
            "tx_historicas": int(row.get('tx_historicas', 0)),
            "tx_hist_real":  qs_data.get('tx_hist_real', int(row.get('tx_historicas', 0))),
            "tx_90d":        qs_data.get('tx_90d', 0),
            "tx_30d":        qs_data.get('tx_30d', 0),
            "tx_pa_30d":     int(tx_pa_by_service.get(sid, 0)),
            "tx_pa_hist":    int(tx_pa_hist_by_service.get(sid, 0)),
            "dormida":       True,
            "dias_sin_trx":  0 if pd.isna(row.get('dias_sin_trx')) else int(float(row.get('dias_sin_trx', 0))),
            "quality_score": 0 if pd.isna(row.get('quality_score')) else int(float(row.get('quality_score', 0))),
            "quality_nivel": qs_data.get('nivel', ''),
            "service_cohort":qs_data.get('service_cohort', ''),
            "etapa":         qs_data.get('etapa', str(row.get('etapa_negocio', ''))),
            "capacidad":     int(float(row.get('capacidad_si_reactiva', 0) or 0)),
            "ventas_30d":    round(float(qs_data.get('ventas_30d', 0) or 0), 0),
            "tasa_acepta":   qs_data.get('tasa_acepta', 0),
            "tiempo_acepta": qs_data.get('tiempo_acepta', 0),
            "menu_bizne":    qs_data.get('menu_bizne', False),
            "menu_dia":      qs_data.get('menu_dia', False),
            "menu_carta":    qs_data.get('menu_carta', False),
            "service_id":    sid,
            "phone_number":  qs_data.get('phone_number', ''),
            "owner_name":    qs_data.get('owner_name', ''),
            "hunter":        qs_data.get('hunter', ''),
            "address":       qs_data.get('address', ''),
            "colonia":       qs_data.get('colonia', ''),
            "food_types":    qs_data.get('food_types', str(row.get('food_types', ''))),
            "horario":       qs_data.get('horario', ''),
            "creation_date": qs_data.get('creation_date', ''),
            "dias_creacion": qs_data.get('dias_creacion', 0),
            "lat":           round(float(row['lat']), 5),
            "lng":           round(float(row['lng']), 5),
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
    if UPC_SWAPPED:
        # CSV original tiene lat/lng intercambiados: latitude≈-99, longitude≈19
        real_lat = float(row['longitude'])
        real_lng = float(row['latitude'])
    else:
        # data/upcs.csv del repo ya tiene coords correctas
        real_lat = float(row['latitude'])
        real_lng = float(row['longitude'])
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

# ── Boost df_hex con demanda de activación + recálculo completo D90→gap ──────
# Parámetros del CI model (deben mantenerse sincronizados con bizne_model_ci.py)
_U_UTIL   = 0.9588
_C_CAP    = 55
_SAFETY   = 1.15
from scipy.stats import poisson as _poisson

if activ_dem_hex:
    df_hex = df_hex.set_index('hex_id')
    for _hx, _dem in activ_dem_hex.items():
        if _hx in df_hex.index:
            # Sumar demanda de activación
            df_hex.at[_hx, 'demanda_estimada_dia'] = round(
                float(df_hex.at[_hx, 'demanda_estimada_dia']) + _dem, 1)
        else:
            # Hex nuevo (fuera del rango previo del modelo) — crear fila mínima
            _lat_hx, _lng_hx = h3.cell_to_latlng(_hx)
            df_hex.loc[_hx] = {
                'hex_lat': round(_lat_hx, 4), 'hex_lng': round(_lng_hx, 4),
                'zone_tier': 'D_BAJA', 'DI': 0.0, 'elementos_sector_total': 0.0,
                'demanda_fijo': 0.0, 'demanda_ruta': 0.0, 'demanda_patrulla': 0.0,
                'demanda_metro': 0.0, 'usuarios_approved': 0.0,
                'transacciones_8d': 0.0, 'tx_incompletas': 0.0,
                'demanda_estimada_dia': round(_dem, 1), 'D90_diario': 0.0,
                'negocios_actuales': 0.0, 'negocios_necesarios': 0.0,
                'gap': 0.0, 'cobertura': 0.0, 'rating_promedio': 0.0,
                'priority_score': 0.0, 'tier_value': 1,
            }
    # Recalcular D90 → N_needed → gap para todos los hexes boosteados
    for _hx in list(activ_dem_hex.keys()):
        if _hx not in df_hex.index:
            continue
        _mu = max(float(df_hex.at[_hx, 'demanda_estimada_dia']), 0.01)
        _d90 = float(_poisson.ppf(0.90, _mu))
        _n_need = float(import_ceil(_d90 * _SAFETY / (_U_UTIL * _C_CAP)))
        _n_act  = float(df_hex.at[_hx, 'negocios_actuales'])
        _gap    = max(0, int(_n_need - _n_act))
        df_hex.at[_hx, 'D90_diario']          = round(_d90, 2)
        df_hex.at[_hx, 'negocios_necesarios'] = _n_need
        df_hex.at[_hx, 'gap']                 = _gap
        df_hex.at[_hx, 'priority_score']      = round(_mu / max(_n_act, 1), 3)
    df_hex = df_hex.reset_index()

# Incluir hexes con gap>0 O con señal de activación (zonas de desarrollo potencial)
_activ_hex_set = set(activ_dem_hex.keys()) if activ_dem_hex else set()
df_hunt = df_hex[(df_hex['gap'] > 0) | (df_hex['hex_id'].isin(_activ_hex_set))].copy()
df_hunt = df_hunt.merge(user_hex, on='hex_id', how='left')
df_hunt['usuarios'] = df_hunt['usuarios'].fillna(0).astype(int)
df_hunt['sin_compras'] = df_hunt['sin_compras'].fillna(0).astype(int)
df_hunt['tasa_conv_pct'] = df_hunt['tasa_conv_pct'].fillna(0).round(1)
df_hunt['neg_dormidos'] = df_hunt['hex_id'].map(dorm_per_hex).fillna(0).astype(int)

# Señal de activación por hex (cuánta demanda de activación cae en cada hunter hex)
df_hunt['activ_demand'] = df_hunt['hex_id'].map(activ_dem_hex).fillna(0)

# ── Usuarios SIN supply cercano por hex ──────────────────────────────────────
# df_aprov_loc ya tiene n_biz (negocios en radio H3 ring-1). Si n_biz == 0
# el usuario está en zona sin oferta → señal de demanda real no atendida.
_uns_hex = (
    df_aprov_loc[df_aprov_loc['n_biz'] == 0]
    .groupby('hex_id').size()
    .reset_index(name='users_no_supply')
)
df_hunt = df_hunt.merge(_uns_hex, on='hex_id', how='left')
df_hunt['users_no_supply'] = df_hunt['users_no_supply'].fillna(0).astype(int)

# ── Nuevo modelo de scoring basado en gap + demanda real ──────────────────────
# Componentes:
#   gap_norm     — cocinas que faltan (driver principal)
#   uns_norm     — usuarios sin cocina cercana (demanda real sin supply)
#   demand_norm  — demanda estructural absoluta (tx esperadas/día)
#   activ_norm   — puntos de activación (señal de campo)
#   user_norm    — presencia total de usuarios (señal complementaria)
#
# Pesos: gap 40% | sin-supply 30% | demanda 20% | activación 10%
# Eliminamos el ratio demanda/negocios (priority_score) como driver principal
# porque penalizaba zonas con muchos negocios aunque el gap fuera grande.

max_gap   = max(float(df_hunt['gap'].max()), 1.0)
max_uns   = max(float(df_hunt['users_no_supply'].max()), 1.0)
max_dem   = max(float(df_hunt['demanda_estimada_dia'].max()), 1.0)
max_activ = max(float(df_hunt['activ_demand'].max()), 1.0)
max_us    = max(float(df_hunt['usuarios'].max()), 1.0)

df_hunt['gap_norm']    = df_hunt['gap'].astype(float) / max_gap
df_hunt['uns_norm']    = df_hunt['users_no_supply'].astype(float) / max_uns
df_hunt['demand_norm'] = df_hunt['demanda_estimada_dia'].astype(float) / max_dem
df_hunt['activ_norm']  = df_hunt['activ_demand'].astype(float) / max_activ
df_hunt['user_norm']   = df_hunt['usuarios'].astype(float) / max_us

df_hunt['combined_score'] = (
    0.40 * df_hunt['gap_norm']    +   # Cocinas que faltan (prioridad principal)
    0.30 * df_hunt['uns_norm']    +   # Usuarios sin cocina cercana (demanda real sin supply)
    0.20 * df_hunt['demand_norm'] +   # Demanda estructural absoluta (tx/día)
    0.10 * df_hunt['activ_norm']       # Puntos de activación en campo
).round(3)

df_hunt = df_hunt.sort_values('combined_score', ascending=False).reset_index(drop=True)
df_hunt['rank'] = df_hunt.index + 1

print(f"  Scoring Hunter — max_gap:{max_gap:.0f} | max_uns:{max_uns:.0f} | max_dem:{max_dem:.1f}")
print(f"  Score range: {df_hunt['combined_score'].min():.3f} – {df_hunt['combined_score'].max():.3f}")

HUNTER_TIER_DEFS = [
    ('A+ Máxima prioridad', '#7f1d1d', 0.85),
    ('A Alta demanda sin supply', '#dc2626', 0.70),
    ('B Señal mixta', '#f97316', 0.55),
    ('C Zona activa', '#22c55e', 0.40),
    ('D Desarrollo', '#3b82f6', 0.25),
    ('E Monitoreo', '#94a3b8', 0.10),
]
def hunter_tier(score):
    # Umbrales recalibrados para nueva distribución basada en gap+uns
    if score >= 0.55:  return HUNTER_TIER_DEFS[0]
    if score >= 0.38:  return HUNTER_TIER_DEFS[1]
    if score >= 0.25:  return HUNTER_TIER_DEFS[2]
    if score >= 0.15:  return HUNTER_TIER_DEFS[3]
    if score >= 0.07:  return HUNTER_TIER_DEFS[4]
    return HUNTER_TIER_DEFS[5]

# Lookup hex_code por hex_id (asignados al construir hex_features)
_hex_code_lookup = {f['properties']['hex_id']: f['properties']['hex_code'] for f in hex_features}

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
            "hex_id":   str(row['hex_id']),
            "hex_code": _hex_code_lookup.get(str(row['hex_id']), ''),
            "rank": int(row['rank']),
            "zona": zona_lbl,
            "delegacion": "CDMX",
            "combined_score": float(row['combined_score']),
            "gap_norm":    round(float(row.get('gap_norm', 0)), 3),
            "uns_norm":    round(float(row.get('uns_norm', 0)), 3),
            "demand_norm": round(float(row['demand_norm']), 3),
            "user_norm":   round(float(row['user_norm']), 3),
            "activ_norm":  round(float(row.get('activ_norm', 0)), 3),
            "activ_demand": round(float(row.get('activ_demand', 0)), 1),
            "users_no_supply": int(row.get('users_no_supply', 0)),
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
            "lat": round(h3.cell_to_latlng(str(row['hex_id']))[0], 7),
            "lng": round(h3.cell_to_latlng(str(row['hex_id']))[1], 7),
        }
    }
    hunter_features.append(feat)

HUNTER_DATA = json.dumps({"type":"FeatureCollection","features":hunter_features}, ensure_ascii=False)
print(f"  {len(hunter_features)} hunter zones")

# ══════════════════════════════════════════════════════════════════════
# GAP GLOBAL — cobertura del modelo estructural
# ══════════════════════════════════════════════════════════════════════
_df_hex_gap = df_hex if isinstance(df_hex, pd.DataFrame) else pd.DataFrame()
_gap_ok = 'gap' in _df_hex_gap.columns and 'negocios_necesarios' in _df_hex_gap.columns and 'negocios_actuales' in _df_hex_gap.columns
if _gap_ok:
    total_gap_global        = int(_df_hex_gap['gap'].fillna(0).clip(lower=0).sum())
    total_necesarios_global = max(1, int(_df_hex_gap['negocios_necesarios'].fillna(0).sum()))
    total_actuales_global   = int(_df_hex_gap['negocios_actuales'].fillna(0).sum())
    cobertura_global_pct    = round(min(100.0, total_actuales_global / total_necesarios_global * 100), 1)
else:
    total_gap_global = 0
    total_necesarios_global = 1
    total_actuales_global = k['negocios_activos']
    cobertura_global_pct = 0.0
print(f"  Gap global: {total_gap_global} | Cobertura: {cobertura_global_pct}%")

# ══════════════════════════════════════════════════════════════════════
# NEGOCIOS NUEVOS — últimos 7 y 30 días + por hunter
# ══════════════════════════════════════════════════════════════════════
def _safe_dias(p):
    try: return int(p.get('dias_creacion', 9999) or 9999)
    except: return 9999

_nuevos_7  = [f for f in biz_features if _safe_dias(f['properties']) <= 7]
_nuevos_30 = [f for f in biz_features if _safe_dias(f['properties']) <= 30]

neg_nuevos_7  = len(_nuevos_7)
neg_nuevos_30 = len(_nuevos_30)

# Negocios nuevos en el mes calendario en curso (desde el día 1 del mes actual)
from datetime import datetime as _dt
_hoy = _dt.now()
_dias_del_mes = (_hoy - _hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).days
_nuevos_mes = [f for f in biz_features if _safe_dias(f['properties']) <= _dias_del_mes]
neg_nuevos_mes = len(_nuevos_mes)
_mes_nombre = _hoy.strftime('%B %Y').capitalize()

# Negocios con transacción: si dias_creacion<=7 y tx_historicas>0 → tuvieron tx en sus primeros 7 días
def _has_tx(p):
    # Usa tx_hist_real (excluye usuarios internos 108608,109497,108604,108609,108585)
    # Fallback a tx_historicas si tx_hist_real no está disponible
    real = int(p.get('tx_hist_real', p.get('tx_historicas', 0)) or 0)
    return real > 0

_n7_con_tx  = sum(1 for f in _nuevos_7  if _has_tx(f['properties']))
_n30_con_tx = sum(1 for f in _nuevos_30 if _has_tx(f['properties']))

pct_nuevos_7_activos = round(_n7_con_tx  / neg_nuevos_7  * 100, 1) if neg_nuevos_7  > 0 else 0.0
pct_nuevos_7_tx_7d   = pct_nuevos_7_activos   # misma lógica: son ≤7d, cualquier tx es en primeros 7 días
pct_nuevos_30_con_tx = round(_n30_con_tx / neg_nuevos_30 * 100, 1) if neg_nuevos_30 > 0 else 0.0

# Lista maestra de hunters activos en el sistema (Team Bizne - Hunter Bizne)
# Incluye hunters sin negocios asignados aún
HUNTERS_SISTEMA = [
    'Amir', 'Anel', 'Oscar', 'Jose Luis',
    'Emma', 'Leonardo', 'Mahithe',
]

# Hunters a excluir del panel (inactivos, campañas, cuentas especiales)
HUNTERS_EXCLUIR_RAW = {
    'Omar', 'Fernanda Hunter', 'Campañas', 'Campanas', 'Campaña', 'Campana',
    'CampaÃ±a',  # variante mojibake por si llega de un cache viejo
    'Fernanda G',
    'Dori La Dorali', 'Dori la Dorali', 'Jorge', 'Sin asignar',
}
import unicodedata as _ud
def _norm_h(s):
    """Normaliza nombre de hunter: NFC + strip + lower para comparación."""
    return _ud.normalize('NFC', str(s).strip()).lower()
HUNTERS_EXCLUIR_NORM = {_norm_h(h) for h in HUNTERS_EXCLUIR_RAW}

# Negocios por hunter en 7d y 30d + todos los hunters con cualquier negocio
_hunter_7   = defaultdict(int)
_hunter_30  = defaultdict(int)
_hunter_all = set(HUNTERS_SISTEMA)  # siempre incluir lista maestra
_hunter_display = {}  # nombre normalizado → nombre display original del CSV
for f in biz_features:
    p  = f['properties']
    h  = str(p.get('hunter', '') or '').strip()
    if not h or h in ('nan', 'None'): continue
    h_norm = _norm_h(h)
    if h_norm in HUNTERS_EXCLUIR_NORM: continue
    _hunter_all.add(h)
    d  = _safe_dias(p)
    if d <= 7:  _hunter_7[h]  += 1
    if d <= 30: _hunter_30[h] += 1

# Incluir TODOS los hunters (sistema + negocios), excluyendo inactivos
_all_hunters = sorted(
    {h for h in (_hunter_all | set(_hunter_7.keys()) | set(_hunter_30.keys()))
     if _norm_h(h) not in HUNTERS_EXCLUIR_NORM},
    key=lambda h: (_hunter_7.get(h, 0) * 10 + _hunter_30.get(h, 0)),
    reverse=True
)
hunter_actividad_rows = ''
for h in _all_hunters:
    n7  = _hunter_7.get(h, 0)
    n30 = _hunter_30.get(h, 0)
    clr7 = '#22c55e' if n7 > 0 else '#475569'
    hunter_actividad_rows += (
        f'<tr>'
        f'<td style="padding:3px 8px;color:#e2e8f0">{h}</td>'
        f'<td style="padding:3px 8px;text-align:right;color:{clr7};font-weight:700">{n7}</td>'
        f'<td style="padding:3px 8px;text-align:right;color:#94a3b8">{n30}</td>'
        f'</tr>\n'
    )
print(f"  Nuevos 7d: {neg_nuevos_7} | 30d: {neg_nuevos_30} | %tx7d: {pct_nuevos_7_tx_7d}%")

# Lista de hunters para el panel de asignación
_hunters_for_assign = [h for h in _all_hunters if h != 'Sin asignar']
if not _hunters_for_assign:
    # Fallback: extraer de biz_features si _all_hunters vacío
    _hunters_for_assign = sorted(set(
        f['properties'].get('hunter','') for f in biz_features
        if f['properties'].get('hunter','') not in ('','nan','None','Sin asignar')
    ))
HUNTERS_LIST_JSON = json.dumps(_hunters_for_assign, ensure_ascii=False)

# Hunter table top 30
hunt_rows_json = []
for feat in hunter_features[:30]:
    p = feat['properties']
    center = h3.cell_to_latlng(p['hex_id'])
    hunt_rows_json.append({
        'tier': 'A_PRIORIDAD_ALTA' if p['combined_score']>=0.55 else 'B_PRIORIDAD_MEDIA',
        'zona': p['zona'],
        'lat': round(center[0],7),
        'lng': round(center[1],7),
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

# ── Heat users por organización (Session Demand no se duplica por org — demasiado pesado) ──
_heat_users_by_org = {'Todas': heat_users_pts}

if _ORG_COL and _orgs_list:
    for _org in _orgs_list:
        _df_org_loc = df_aprov_loc2[df_aprov_loc2[_ORG_COL] == _org] \
            if _ORG_COL in df_aprov_loc2.columns else pd.DataFrame()
        _heat_org = [[round(float(r['lat']),5), round(float(r['lng']),5), 1.0]
                     for _, r in _df_org_loc.iterrows()
                     if pd.notna(r.get('lat')) and abs(float(r.get('lat',0)))>5]
        _heat_users_by_org[_org] = _heat_org

# SESSION_DEMAND_BY_ORG: solo 'Todas' — evita duplicar ~340KB de GeoJSON por cada org
HEAT_USERS_BY_ORG     = json.dumps(_heat_users_by_org, ensure_ascii=False)
SESSION_DEMAND_BY_ORG = json.dumps({'Todas': json.loads(SESSION_DEMAND_DATA)}, ensure_ascii=False)
print(f"  Org heat layers: {list(_heat_users_by_org.keys())} | SD: solo Todas")

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
#kpi-dash{position:fixed;top:10px;left:10px;z-index:1005;background:#f8fafc;
  border-radius:12px;box-shadow:0 4px 22px rgba(0,0,0,.18);
  font-family:system-ui,sans-serif;width:360px;max-height:90vh;
  display:flex;flex-direction:column;user-select:none;overflow:hidden;}
#kpi-dash-header{background:#00BFA5;color:#fff;padding:7px 12px;font-size:11px;
  font-weight:700;letter-spacing:.6px;display:flex;justify-content:space-between;
  align-items:center;cursor:move;flex-shrink:0;}
#kpi-body{padding:8px 10px 10px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;
  overflow-y:auto;flex:1;}
.kc{background:#e2e8f0;border-radius:6px;padding:5px 8px;display:flex;flex-direction:column;gap:1px;}
.kc.full{grid-column:1/-1;}.kc.half{grid-column:span 2;}
.kl{font-size:7.5px;color:#475569;font-weight:600;letter-spacing:.2px;text-transform:uppercase;line-height:1.2;}
.kv{font-size:15px;font-weight:700;color:#0f172a;line-height:1.1;}
.kv.g{color:#16a34a;}.kv.r{color:#dc2626;}.kv.y{color:#d97706;}
.kv.t{color:#00897B;}.kv.s{font-size:11px;}
.ks{font-size:7.5px;color:#64748b;line-height:1.2;}
.kdiv{grid-column:1/-1;height:1px;background:#cbd5e1;margin:1px 0;}
.ksec{grid-column:1/-1;background:none;padding:2px 1px 0;display:flex;align-items:center;gap:6px;}
.ksec-lbl{font-size:8.5px;color:#00897B;font-weight:700;letter-spacing:.5px;text-transform:uppercase;}
#kpi-tb{background:none;border:none;color:#fff;cursor:pointer;font-size:14px;padding:0;}
/* ── Date range buttons ─────────────────────────────────────── */
.dr-btn{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);color:#fff;
  font-size:8px;padding:2px 5px;border-radius:3px;cursor:pointer;font-weight:600;}
.dr-btn.active{background:#fff;color:#00897B;}
.dr-btn:hover:not(.active){background:rgba(255,255,255,.25);}
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
/* ── Refresh button ─────────────────────────────────────────── */
#refresh-btn{position:fixed;top:10px;right:160px;z-index:1006;background:#0f4c81;
  color:#fff;border:none;border-radius:8px;padding:8px 14px;font-size:12px;
  cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.3);font-family:system-ui,sans-serif;
  display:flex;align-items:center;gap:6px;}
#refresh-btn:hover{background:#1a65a3;}
#refresh-btn.spinning svg{animation:spin .8s linear infinite;}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
/* ── Hunter panel ──────────────────────────────────────────── */
#hunter-panel{position:fixed;bottom:20px;left:10px;z-index:1004;background:#0f172a;
  border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,.5);font-family:system-ui,sans-serif;
  width:420px;height:300px;min-height:140px;max-height:85vh;
  display:flex;flex-direction:column;user-select:none;overflow:hidden;
  transition:height .2s ease;}
#hunter-header{background:#dc2626;color:#fff;padding:8px 12px;cursor:move;
  display:flex;justify-content:space-between;align-items:center;font-size:11px;font-weight:700;
  flex-shrink:0;}
#hunter-body{overflow-y:auto;flex:1;min-height:0;}
#hunter-body table{width:100%;border-collapse:collapse;font-size:10px;}
#hunter-body th{background:#1e293b;color:#94a3b8;padding:5px 8px;text-align:right;
  font-weight:600;letter-spacing:.3px;font-size:9px;position:sticky;top:0;}
#hunter-body th:first-child{text-align:center;}
#hunter-body td{padding:4px 8px;border-bottom:1px solid #1e293b;color:#e2e8f0;}
#hunter-body tr:hover td{background:#1e3a52!important;}
#hunter-expand-btn{background:none;border:none;color:#fff;cursor:pointer;font-size:12px;
  padding:0 4px;opacity:.8;line-height:1;}
#hunter-expand-btn:hover{opacity:1;}
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
/* ── Puntos de Activación marker ──────────────────────────────*/
.activ-marker{position:relative;width:22px;height:22px;}
.activ-pulse{position:absolute;inset:0;border-radius:50%;
  background:rgba(232,121,249,.25);animation:activPulse 2s ease-in-out infinite;}
.activ-core{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:14px;height:14px;border-radius:50%;background:#E879F9;
  border:2.5px solid #fff;box-shadow:0 0 8px #E879F9;}
.activ-label{position:absolute;top:-18px;left:50%;transform:translateX(-50%);
  background:#1a0d1e;color:#E879F9;font-size:9px;font-weight:700;
  white-space:nowrap;padding:1px 5px;border-radius:4px;
  border:1px solid #E879F9;pointer-events:none;}
@keyframes activPulse{0%,100%{transform:scale(1);opacity:.6;}50%{transform:scale(1.7);opacity:0;}}
/* ── Edificios Administrativos marker ─────────────────────────*/
.admin-marker{position:relative;width:26px;height:26px;}
.admin-icon{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:22px;height:22px;border-radius:4px;background:#1e3a8a;
  border:2.5px solid #60a5fa;box-shadow:0 0 8px rgba(96,165,250,.5);
  display:flex;align-items:center;justify-content:center;
  font-size:12px;line-height:1;}
.admin-label{position:absolute;top:-18px;left:50%;transform:translateX(-50%);
  background:#0f172a;color:#60a5fa;font-size:9px;font-weight:700;
  white-space:nowrap;padding:1px 5px;border-radius:4px;
  border:1px solid #60a5fa;pointer-events:none;}
/* ── Marquee selector ───────────────────────────────────────── */
#marquee-rect{position:fixed;border:2px dashed #60a5fa;background:rgba(96,165,250,.08);
  pointer-events:none;z-index:9999;display:none;box-sizing:border-box;
  box-shadow:0 0 0 1px rgba(96,165,250,.2);
  animation:marqueeAnim .6s linear infinite;}
@keyframes marqueeAnim{
  0%{border-color:#60a5fa;}50%{border-color:#93c5fd;}100%{border-color:#60a5fa;}}
#marquee-panel{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
  z-index:9998;background:#0f172a;border:1.5px solid #60a5fa;border-radius:10px;
  padding:10px 16px;display:none;align-items:center;gap:10px;
  box-shadow:0 4px 24px rgba(0,0,0,.6);white-space:nowrap;}
#marquee-panel .mp-count{color:#60a5fa;font-weight:700;font-size:13px;}
#marquee-panel .mp-btn{background:#1e40af;color:#fff;border:none;padding:7px 16px;
  border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;
  transition:all .2s;display:flex;align-items:center;gap:6px;}
#marquee-panel .mp-btn:hover{background:#2563eb;transform:scale(1.03);}
#marquee-panel .mp-btn.downloading{background:#15803d;border-color:#16a34a;animation:dlPulse .5s ease;}
@keyframes dlPulse{0%{transform:scale(1);}50%{transform:scale(1.08);}100%{transform:scale(1);}}
#marquee-panel .mp-btn.done{background:#15803d;}
#marquee-panel .mp-fields{color:#475569;font-size:10px;max-width:200px;line-height:1.4;}
#marquee-panel .mp-clear{background:transparent;color:#64748b;border:1px solid #334155;
  padding:6px 10px;border-radius:6px;cursor:pointer;font-size:11px;}
#marquee-panel .mp-clear:hover{color:#ef4444;border-color:#ef4444;}
/* ── Panel de asignación de zonas hunter ────────────────────── */
#assign-tool-btn{position:fixed;bottom:162px;right:24px;z-index:2002;
  background:#151A4F;border:2px solid #334155;color:#94a3b8;width:36px;height:36px;
  border-radius:8px;cursor:pointer;font-size:15px;display:flex;align-items:center;
  justify-content:center;transition:all .2s;box-shadow:0 2px 8px rgba(0,0,0,.5);}
#assign-tool-btn:hover,#assign-tool-btn.active{border-color:#f97316;color:#f97316;background:#1a0d00;}
#assign-panel{display:none;position:fixed;top:60px;right:70px;
  z-index:3000;width:400px;max-height:80vh;background:#0f172a;border-radius:14px;
  box-shadow:0 8px 32px rgba(0,0,0,.7);font-family:system-ui,sans-serif;
  flex-direction:column;overflow:hidden;border:1px solid #1e3a52;}
#assign-panel.open{display:flex;}
#assign-head{background:#7c2d12;color:#fff;padding:10px 14px;display:flex;
  justify-content:space-between;align-items:center;font-size:12px;font-weight:700;cursor:move;}
#assign-body{padding:12px 14px;overflow-y:auto;flex:1;}
.az-hunter-row{display:flex;align-items:center;gap:8px;padding:6px 8px;
  border-radius:6px;margin-bottom:4px;cursor:pointer;transition:background .15s;}
.az-hunter-row:hover{background:#1e293b;}
.az-hunter-row.selected{background:#1e293b;outline:2px solid #f97316;}
.az-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0;}
.az-zone-item{display:flex;align-items:center;gap:6px;padding:3px 6px;font-size:10px;
  color:#e2e8f0;background:#1e293b;border-radius:4px;margin-bottom:2px;}
.az-zone-item .az-rm{background:none;border:none;color:#ef4444;cursor:pointer;
  font-size:11px;padding:0 2px;margin-left:auto;}
#assign-mode-bar{display:none;position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  z-index:2999;background:#7c2d12;color:#fff;padding:8px 20px;border-radius:20px;
  font-size:12px;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.5);
  pointer-events:none;white-space:nowrap;}
#marquee-tool-btn{position:fixed;bottom:118px;right:24px;z-index:2002;
  background:#151A4F;border:2px solid #334155;color:#94a3b8;width:36px;height:36px;
  border-radius:8px;cursor:pointer;font-size:15px;display:flex;align-items:center;
  justify-content:center;transition:all .2s;box-shadow:0 2px 8px rgba(0,0,0,.5);}
#marquee-tool-btn:hover,#marquee-tool-btn.active{border-color:#60a5fa;color:#60a5fa;background:#0f1e38;}
/* ── Chat panel ─────────────────────────────────────────────── */
#chat-wrap{position:fixed;bottom:76px;right:24px;z-index:1010;}
#chat-toggle-btn{background:#151A4F;color:#6EE9B3;border:2px solid #6EE9B3;
  border-radius:50%;width:44px;height:44px;font-size:18px;cursor:pointer;
  box-shadow:0 3px 10px rgba(0,0,0,.4);}
#chat-panel{display:none;position:fixed;bottom:134px;right:20px;z-index:2001;
  width:370px;height:500px;background:#0f172a;border-radius:14px;
  box-shadow:0 8px 32px rgba(0,0,0,.7);font-family:system-ui,sans-serif;
  flex-direction:column;overflow:hidden;border:1px solid #1e3a52;}
#chat-panel.open{display:flex;}
#chat-head{background:#151A4F;color:#fff;padding:10px 14px;display:flex;
  justify-content:space-between;align-items:center;font-size:12px;font-weight:700;
  border-bottom:2px solid #6EE9B3;cursor:move;}
#chat-head-title{color:#6EE9B3;display:flex;align-items:center;gap:6px;}
#chat-close-btn{background:none;border:none;color:#6EE9B3;font-size:16px;cursor:pointer;
  padding:0;line-height:1;}
#chat-msgs{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;
  gap:8px;scroll-behavior:smooth;}
#chat-msgs::-webkit-scrollbar{width:4px;}
#chat-msgs::-webkit-scrollbar-track{background:#0a0f1e;}
#chat-msgs::-webkit-scrollbar-thumb{background:#1e3a52;border-radius:4px;}
.cm-user{align-self:flex-end;background:#1e3a52;color:#e2e8f0;padding:8px 12px;
  border-radius:12px 12px 2px 12px;max-width:85%;font-size:11px;line-height:1.5;}
.cm-bot{align-self:flex-start;background:#111827;color:#e2e8f0;padding:8px 12px;
  border-radius:12px 12px 12px 2px;max-width:96%;font-size:11px;line-height:1.6;
  border-left:2px solid #6EE9B3;}
.cm-bot b{color:#6EE9B3;}
.cm-tag{display:inline-block;background:#0e1530;color:#a78bfa;
  padding:1px 5px;border-radius:4px;font-size:9px;margin:1px 0;}
.cm-chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px;}
.cm-chip{background:#0e1530;border:1px solid #1e3a52;color:#00BFA5;
  padding:3px 9px;border-radius:10px;font-size:9px;cursor:pointer;transition:.15s;}
.cm-chip:hover{background:#1e3a52;border-color:#00BFA5;}
#chat-input-row{padding:8px 10px;border-top:1px solid #1e3a52;display:flex;
  gap:6px;background:#0a0f1e;flex-shrink:0;}
#chat-input{flex:1;background:#1e293b;border:1px solid #334155;border-radius:8px;
  padding:7px 10px;font-size:11px;color:#e2e8f0;outline:none;font-family:system-ui;}
#chat-input:focus{border-color:#6EE9B3;}
#chat-input::placeholder{color:#475569;}
#chat-send{background:#151A4F;border:1.5px solid #6EE9B3;color:#6EE9B3;
  border-radius:8px;padding:6px 13px;font-size:14px;cursor:pointer;transition:.15s;}
#chat-send:hover{background:#6EE9B3;color:#151A4F;}
.cm-count-table{width:100%;border-collapse:collapse;margin-top:5px;font-size:10px;}
.cm-count-table td{padding:3px 4px;border-bottom:1px solid #1e3a52;}
.cm-count-table td:last-child{text-align:right;color:#6EE9B3;font-weight:700;}
.cm-typing{display:flex;gap:4px;align-items:center;padding:4px 0;}
.cm-typing span{width:6px;height:6px;background:#6EE9B3;border-radius:50%;
  animation:cmBounce .9s infinite;}
.cm-typing span:nth-child(2){animation-delay:.2s;}
.cm-typing span:nth-child(3){animation-delay:.4s;}
@keyframes cmBounce{0%,80%,100%{transform:translateY(0);}40%{transform:translateY(-5px);}}
</style>
"""

_org_options_html = ''.join(f'<option value="{o}">{o}</option>' for o in _all_orgs)
_cv_color = '#16a34a' if k['conv_registrados_tx']>=20 else '#d97706'
_qs_color = 'g' if _qs_avg>=60 else ('y' if _qs_avg>=40 else 'r')
_qs_sal_color = 'g' if _qs_pct_saludables>=50 else 'y'

KPI_HTML = f"""
<div id="kpi-dash">
  <div id="kpi-dash-header">
    <div style="display:flex;flex-direction:column;gap:3px">
      <span style="font-size:11px">📊 BIZNE PA · KPIs</span>
      <div style="display:flex;gap:3px">
        <button class="dr-btn active" data-range="todo"   onclick="switchDateRange('todo')">Todo</button>
        <button class="dr-btn"        data-range="180d"   onclick="switchDateRange('180d')">6m</button>
        <button class="dr-btn"        data-range="90d"    onclick="switchDateRange('90d')">90d</button>
        <button class="dr-btn"        data-range="30d"    onclick="switchDateRange('30d')">30d</button>
        <button class="dr-btn"        data-range="7d"     onclick="switchDateRange('7d')">7d</button>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:5px">
      <select id="org-select" onchange="switchOrg(this.value)"
        style="font-size:9px;background:#00766C;color:#fff;border:1px solid rgba(255,255,255,.3);
               border-radius:4px;padding:2px 5px;cursor:pointer;max-width:105px">
        {_org_options_html}
      </select>
      <button id="kpi-tb" onclick="var b=document.getElementById('kpi-body');b.style.display=b.style.display==='none'?'grid':'none';this.textContent=b.style.display==='none'?'▼':'▲'">▲</button>
    </div>
  </div>
  <div id="kpi-body">

    <!-- ── USUARIOS ─────────────────────────────────────── -->
    <div class="ksec"><span class="ksec-lbl">👤 Usuarios</span><span id="kpi-org-label" style="font-size:8px;color:#94a3b8"></span></div>
    <div class="kc"><div class="kl">Signups</div><div class="kv t" id="kpi-signups">{k['signups_totales']}</div></div>
    <div class="kc"><div class="kl">Aprobados</div><div class="kv" id="kpi-aprobados">{k['usuarios_aprobados']}</div><div class="ks" id="kpi-ap-pct">{ap_pct}%</div></div>
    <div class="kc"><div class="kl">Sin supply</div><div class="kv {ss_col}" id="kpi-sin-supply">{k['pct_sin_supply']}%</div><div class="ks">sin negocio cerca</div></div>
    <!-- Embudo compacto -->
    <div class="kc full">
      <div class="kl" style="margin-bottom:3px">Embudo conversión <span style="font-weight:400;color:#94a3b8">(excluye membresías)</span></div>
      <div style="display:flex;gap:3px;align-items:flex-end;height:32px;margin-bottom:2px">
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:1px">
          <div style="width:100%;background:#64748b;border-radius:2px" id="kpi-bar-reg-v" style="height:32px"></div>
          <div style="font-size:7px;color:#475569">Reg.</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:1px">
          <div id="kpi-bar-ap-v" style="width:100%;background:#00897B;border-radius:2px"></div>
          <div style="font-size:7px;color:#475569">Apro.</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:1px">
          <div id="kpi-bar-conv-v" style="width:100%;background:{_cv_color};border-radius:2px"></div>
          <div style="font-size:7px;color:#475569">1ª tx</div>
        </div>
        <div style="flex:2;padding-left:6px;font-size:9px;color:#475569;display:flex;flex-direction:column;gap:2px;justify-content:center">
          <div><span style="font-weight:700;color:#00897B" id="kpi-ap-pct2">{ap_pct}%</span> aprobados</div>
          <div><span style="font-weight:700;color:{_cv_color}" id="kpi-conv-reg">{k['conv_registrados_tx']}%</span> 1ª compra</div>
          <div style="font-size:8px;color:#94a3b8">Ap→compra: <b id="kpi-conv-primer">{k['conv_primer_consumo']}%</b></div>
        </div>
      </div>
      <div class="ks">Sin convertir: <b style="color:#dc2626" id="kpi-aprov-sin">{k['aprobados_sin_convertir']}%</b> · T. 1ª compra: <b id="kpi-dias-prom">{k['dias_prom_primer_consumo']}d</b></div>
    </div>
    <div class="kc"><div class="kl">Tx completadas</div><div class="kv g">{k['trx_completadas']}</div></div>
    <div class="kc"><div class="kl">Tx incompletas</div><div class="kv r">{k['trx_incompletas']}</div></div>
    <div class="kc"><div class="kl">Tasa aceptación</div><div class="kv {tc_col}">{k['tasa_aceptacion']}%</div></div>

    <div class="kdiv"></div>

    <!-- ── NEGOCIOS ────────────────────────────────────── -->
    <div class="ksec"><span class="ksec-lbl">🏪 Negocios</span></div>
    <div class="kc"><div class="kl">Activos</div><div class="kv t">{k['negocios_activos']}</div></div>
    <div class="kc"><div class="kl">Dormidos</div><div class="kv y">{k['negocios_dormidos']}</div><div class="ks">{k['dormidos_pct_total']}%</div></div>
    <div class="kc"><div class="kl">Sin tx 30d</div><div class="kv {st_col}">{k['pct_sin_tx']}%</div><div class="ks">{k['sin_tx_n']} neg.</div></div>
    <div class="kc"><div class="kl">🆕 Nuevos mes</div><div class="kv t">{neg_nuevos_mes}</div><div class="ks">{_mes_nombre[:3]}</div></div>
    <div class="kc"><div class="kl">🆕 Nuevos 7d</div><div class="kv">{neg_nuevos_7}</div><div class="ks">{pct_nuevos_7_activos}% con tx</div></div>
    <div class="kc"><div class="kl">1ªTx ≤7d</div><div class="kv {('g' if pct_nuevos_7_tx_7d>=50 else 'y')}">{pct_nuevos_7_tx_7d}%</div></div>
    <div class="kc half">
      <div class="kl">📐 Cobertura estructural</div>
      <div style="display:flex;align-items:center;gap:5px;margin-top:3px">
        <div style="flex:1;height:7px;background:#cbd5e1;border-radius:3px;overflow:hidden">
          <div style="width:{cobertura_global_pct}%;height:100%;background:linear-gradient(90deg,#16a34a,#00897B);border-radius:3px"></div>
        </div>
        <span style="font-size:11px;color:#16a34a;font-weight:700">{cobertura_global_pct}%</span>
      </div>
      <div class="ks">Gap: <span style="color:#dc2626;font-weight:700">{total_gap_global} 🍽</span> · {total_actuales_global}/{total_necesarios_global}</div>
    </div>
    <div class="kc"><div class="kl">Mediana tx</div><div class="kv s">{k['mediana_tx_negocio']}</div><div class="ks">tx/mes</div></div>

    <div class="kdiv"></div>

    <!-- ── QUALITY SCORE ──────────────────────────────── -->
    <div class="ksec"><span class="ksec-lbl">⭐ Quality Score</span></div>
    <div class="kc"><div class="kl">Promedio</div><div class="kv {_qs_color}">{_qs_avg}</div><div class="ks">/ 100 pts</div></div>
    <div class="kc half">
      <div class="kl" style="margin-bottom:3px">Distribución por nivel</div>
      {''.join(
        f'<div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">'
        f'<div style="font-size:7.5px;color:{_QS_COLORS[n]};width:50px;font-weight:600">{n}</div>'
        f'<div style="flex:1;height:9px;background:#cbd5e1;border-radius:2px;overflow:hidden;position:relative">'
        f'<div style="width:{_qs_pct[n]}%;height:100%;background:{_QS_COLORS[n]};border-radius:2px"></div>'
        f'<span style="position:absolute;right:3px;top:50%;transform:translateY(-50%);font-size:7px;color:#0f172a;font-weight:700">{_qs_dist[n]}</span>'
        f'</div>'
        f'<div style="font-size:7.5px;color:{_QS_COLORS[n]};font-weight:700;min-width:24px;text-align:right">{_qs_pct[n]}%</div>'
        f'</div>'
        for n in _QS_ORDER
      )}
    </div>

  </div>
</div>
"""

HUNTER_HTML = f"""
<div id="hunter-panel">
  <div id="hunter-header">
    <span>🎯 ZONAS HUNTER — Top 30</span>
    <div style="display:flex;align-items:center;gap:6px">
      <button id="hunter-expand-btn" title="Expandir/Colapsar"
        onclick="(function(){{var p=document.getElementById('hunter-panel');var exp=p._expanded;p.style.height=exp?'300px':'80vh';p._expanded=!exp;document.getElementById('hunter-expand-btn').textContent=exp?'⤢':'⤡';}})()"
        >⤢</button>
      <button onclick="document.getElementById('hunter-panel').style.display='none';document.getElementById('hunter-toggle').style.display='block'"
        style="border:none;background:none;cursor:pointer;color:#fff;font-size:14px">✕</button>
    </div>
  </div>
  <!-- Barra de actividad nuevos negocios -->
  <div style="background:#0d1117;padding:5px 10px;display:flex;gap:12px;align-items:center;
    font-size:10px;border-bottom:1px solid #1e2d40;flex-wrap:wrap">
    <span style="color:#94a3b8">{_mes_nombre[:3]}: <b style="color:#a78bfa;font-size:11px">{neg_nuevos_mes}</b></span>
    <span style="color:#94a3b8">7d: <b style="color:#22c55e">{neg_nuevos_7}</b></span>
    <span style="color:#94a3b8">30d: <b style="color:#00BFA5">{neg_nuevos_30}</b></span>
    <span style="color:#94a3b8">1ªTx: <b style="color:{'#22c55e' if pct_nuevos_7_tx_7d>=50 else '#f59e0b'}">{pct_nuevos_7_tx_7d}%</b></span>
    <button onclick="var s=document.getElementById('h-act');s.style.display=s.style.display==='none'?'block':'none';this.textContent=s.style.display==='none'?'📊 Por hunter':'▲ Cerrar'"
      style="margin-left:auto;background:#1e3a52;border:none;color:#94a3b8;padding:2px 8px;
      border-radius:4px;cursor:pointer;font-size:9px;white-space:nowrap">📊 Por hunter</button>
  </div>
  <!-- Desglose negocios por hunter -->
  <div id="h-act" style="display:none;max-height:130px;overflow-y:auto;background:#080d14;border-bottom:1px solid #1e2d40">
    <table style="width:100%;border-collapse:collapse;font-size:10px">
      <thead><tr style="position:sticky;top:0;background:#0f172a">
        <th style="padding:4px 8px;color:#64748b;text-align:left;font-size:9px;letter-spacing:.3px">HUNTER</th>
        <th style="padding:4px 8px;color:#22c55e;text-align:right;font-size:9px">7d</th>
        <th style="padding:4px 8px;color:#00BFA5;text-align:right;font-size:9px">30d</th>
      </tr></thead>
      <tbody style="color:#e2e8f0">
{hunter_actividad_rows}
      </tbody>
    </table>
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
<button id="refresh-btn" onclick="refreshMap()" title="Recargar datos">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="23 4 23 10 17 10"></polyline>
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
  </svg>
  Actualizar
</button>
<button id="mode-btn" onclick="toggleMode()">🌙 Modo oscuro</button>

<div id="bmap-panel">
  <div id="bmap-header">
    <span>⚙️ Configuración del mapa</span>
    <button onclick="document.getElementById('bmap-panel').style.display='none';document.getElementById('bmap-toggle').style.display='block'"
      style="border:none;background:none;cursor:pointer;color:#fff;font-size:15px;line-height:1">✕</button>
  </div>
  <div id="bmap-body">

    <div class="bs">
      <div class="bs-title">📍 Capas del mapa</div>
      <label class="bchk"><input type="checkbox" id="ly_hexes"    onchange="toggleLayer('hexes',this.checked)">
        <span class="bdot" style="background:#3b82f6"></span> Hexágonos demanda PA</label>
      <label class="bchk"><input type="checkbox" id="ly_activos"  checked onchange="toggleLayer('activos',this.checked)">
        <span style="display:inline-flex;gap:2px;margin-right:2px"><span class="bdot" style="background:#22c55e"></span><span class="bdot" style="background:#00BFA5"></span><span class="bdot" style="background:#f59e0b"></span><span class="bdot" style="background:#ef4444"></span></span> Negocios Activos</label>
      <label class="bchk"><input type="checkbox" id="ly_dormidas" onchange="toggleLayer('dormidas',this.checked)">
        <span class="bdot" style="background:#9ca3af"></span> Negocios Dormidos</label>
      <label class="bchk"><input type="checkbox" id="ly_hunter"   onchange="toggleLayer('hunter',this.checked)">
        <span class="bdot" style="background:#f97316;border-radius:50%"></span> Zonas Hunter</label>
      <label class="bchk"><input type="checkbox" id="ly_sdemand"  onchange="toggleLayer('sdemand',this.checked)">
        <span class="bdot" style="background:#7c3aed;border-radius:50%"></span> Demanda por Sesiones</label>
      <label class="bchk"><input type="checkbox" id="ly_metro"    onchange="toggleLayer('metro',this.checked)">
        <span class="bdot" style="background:#e91e63"></span> Estaciones Metro</label>
      <label class="bchk"><input type="checkbox" id="ly_upcs"     onchange="toggleLayer('upcs',this.checked)">
        <span class="bdot" style="background:#7C3AED"></span> UPCs Policía</label>
      <label class="bchk"><input type="checkbox" id="ly_sec"      onchange="toggleLayer('sec',this.checked)">
        <span class="bdot" style="background:#06b6d4"></span> Sectores PA</label>
      <label class="bchk"><input type="checkbox" id="ly_activ"    onchange="toggleLayer('activ',this.checked)">
        <span class="bdot" style="background:#E879F9;box-shadow:0 0 5px #E879F9"></span> Puntos de Activación</label>
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
      <div class="bs-title">⬡ Buscar hexágono</div>
      <div style="display:flex;gap:4px">
        <input id="hex-search" type="text" placeholder="HEX-0042 o ID H3..."
          oninput="searchHex(this.value)"
          style="flex:1;padding:5px 8px;border:1px solid #e2e8f0;border-radius:5px;
                 font-size:11px;box-sizing:border-box;outline:none;color:#1e293b;background:#f8fafc">
        <button onclick="searchHex(document.getElementById('hex-search').value)"
          style="padding:4px 8px;background:#1e293b;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px">
          🔍</button>
      </div>
      <div id="hex-search-result" style="font-size:9px;color:#94a3b8;margin-top:3px"></div>
    </div>

    <hr class="bhr">

    <div class="bs">
      <div class="bs-title">🔍 Buscar negocio</div>
      <input id="biz-search" type="text" placeholder="Nombre del negocio..."
        oninput="searchNegocios(this.value)"
        style="width:100%;padding:5px 8px;border:1px solid #e2e8f0;border-radius:5px;
               font-size:11px;box-sizing:border-box;outline:none;color:#1e293b;background:#f8fafc">
      <div id="biz-count" style="font-size:9px;color:#94a3b8;margin-top:3px;text-align:right"></div>
      <div style="display:flex;gap:6px;margin-top:5px">
        <label class="bchk" style="flex:1">
          <input type="checkbox" id="biz-nuevos-7d" onchange="filterBizNuevos(this.checked?7:(document.getElementById('biz-nuevos-30d').checked?30:0))">
          <span style="font-size:10px;color:#0f172a">🆕 ≤ 7 días</span>
        </label>
        <label class="bchk" style="flex:1">
          <input type="checkbox" id="biz-nuevos-30d" onchange="filterBizNuevos(this.checked?30:(document.getElementById('biz-nuevos-7d').checked?7:0))">
          <span style="font-size:10px;color:#0f172a">🆕 ≤ 30 días</span>
        </label>
      </div>
      <button onclick="searchNegocios('');document.getElementById('biz-search').value='';document.getElementById('biz-nuevos-7d').checked=false;document.getElementById('biz-nuevos-30d').checked=false;filterBizNuevos(0)"
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
      <label class="bchk"><input type="checkbox" id="hf_activ"        onchange="updateHexTT()">
        <span style="color:#E879F9;font-size:10px">⚡</span> Dem. Activación</label>
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

CHAT_HTML = """
<div id="chat-wrap">
  <button id="chat-toggle-btn" onclick="toggleChat()" title="Consultar negocios">💬</button>
</div>
<div id="chat-panel">
  <div id="chat-head">
    <div id="chat-head-title">🤖 <span>Consultor Bizne</span></div>
    <button id="chat-close-btn" onclick="toggleChat()">✕</button>
  </div>
  <div id="chat-msgs">
    <div class="cm-bot">
      <b>Hola!</b> Puedo consultarte los negocios del mapa por cualquier criterio.<br>
      <div class="cm-chips">
        <span class="cm-chip" onclick="chatSendText('Top 10 negocios por score')">Top 10 score</span>
        <span class="cm-chip" onclick="chatSendText('Contar por delegación')">Por delegación</span>
        <span class="cm-chip" onclick="chatSendText('Negocios sin transacciones')">Sin tx 30d</span>
        <span class="cm-chip" onclick="chatSendText('Negocios con menú del día')">Con menú día</span>
        <span class="cm-chip" onclick="chatSendText('Negocios nivel crítica')">Nivel crítica</span>
        <span class="cm-chip" onclick="chatSendText('Negocios dormidos')">Dormidos</span>
        <span class="cm-chip" onclick="chatSendText('Contar por nivel')">Por nivel</span>
        <span class="cm-chip" onclick="chatSendText('Top 10 en Iztapalapa')">Iztapalapa</span>
      </div>
    </div>
  </div>
  <div id="chat-input-row">
    <input id="chat-input" type="text"
      placeholder="Ej: top 5 en GAM con menú Bizne…"
      onkeydown="if(event.key==='Enter')chatSend()">
    <button id="chat-send" onclick="chatSend()">↵</button>
  </div>
</div>
"""

CHAT_JS = """<script>
// ── CHAT ENGINE ───────────────────────────────────────────────────────
(function() {

function removeAccents(s) {
  return (s||'').normalize('NFD').replace(/[̀-ͯ]/g,'');
}

window.toggleChat = function() {
  var panel = document.getElementById('chat-panel');
  panel.classList.toggle('open');
  if (panel.classList.contains('open'))
    setTimeout(function(){document.getElementById('chat-input').focus();},50);
};

window.chatSendText = function(txt) {
  var inp = document.getElementById('chat-input');
  inp.value = txt;
  chatSend();
};

window.chatSend = function() {
  var inp = document.getElementById('chat-input');
  var q = inp.value.trim();
  if (!q) return;
  inp.value = '';
  chatAddMsg(q, 'user');
  // Show typing indicator
  var typingId = 'cm-typing-' + Date.now();
  chatAddMsg('<div class="cm-typing"><span></span><span></span><span></span></div>', 'bot', typingId);
  setTimeout(function() {
    var typingEl = document.getElementById(typingId);
    if (typingEl) typingEl.remove();
    var response = chatProcess(q);
    chatAddMsg(response, 'bot');
  }, 300);
};

function chatAddMsg(content, type, id) {
  var msgs = document.getElementById('chat-msgs');
  var div = document.createElement('div');
  div.className = type === 'user' ? 'cm-user' : 'cm-bot';
  if (id) div.id = id;
  div.innerHTML = content;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function chatProcess(q) {
  var qn = removeAccents(q.toLowerCase().trim());

  // Help
  if (/^(ayuda|help|que puedo|como se usa|que haces|comandos)/.test(qn)) {
    return 'Puedo filtrar y resumir los negocios del mapa. Ejemplos:<br>' +
      '<b>Filtros:</b> delegación, nivel (excelente/alta/media/baja/crítica), sin tx, con menú Bizne/día/carta<br>' +
      '<b>Top N:</b> "top 10 en Iztapalapa por tasa de aceptación"<br>' +
      '<b>Conteos:</b> "contar por delegación", "contar por nivel", "contar por etapa"<br>' +
      '<b>Dormidos:</b> "negocios dormidos en Gustavo A. Madero"<br>' +
      '<b>Score:</b> "score >= 80", "score menor a 40"';
  }

  var isDorm = /dormi/.test(qn);
  var baseData = isDorm
    ? DORM_DATA.features.map(function(f){return f.properties;})
    : BIZ_DATA.features.map(function(f){return f.properties;});

  // COUNT BY GROUP
  if (/conta[r]?\s+por|distribuci[oó]n|por\s+(delegac|nivel|etapa|cohort|estado)/.test(qn)) {
    var field = 'delegacion';
    if (/nivel/.test(qn)) field = 'quality_nivel';
    else if (/etapa/.test(qn)) field = 'etapa';
    else if (/cohort/.test(qn)) field = 'service_cohort';
    return buildCountResponse(baseData, field, isDorm);
  }

  // HOW MANY
  if (/^(cuantos?|cuantas?|total de|hay )/.test(qn)) {
    var filtered = applyFilters(baseData, qn);
    return '📊 <b>' + filtered.length + '</b> de ' + baseData.length +
      ' negocios' + (isDorm?' dormidos':' activos') + ' con ese criterio.';
  }

  // DEFAULT: filter + sort + list
  var filtered = applyFilters(baseData, qn);

  // Sort
  var sortField = isDorm ? 'tx_historicas' : 'quality_score';
  var sortLabel = isDorm ? 'tx hist.' : 'score';
  if (/tasa|aceptac/.test(qn)) { sortField='tasa_acepta'; sortLabel='tasa aceptación'; }
  if (/transacc|\\btx\\b|ventas|pedidos|compras/.test(qn) && !isDorm) { sortField='tx_30d'; sortLabel='Tx 30d'; }

  // Limit
  var topMatch = qn.match(/top\\s+(\\d+)/);
  var limit = topMatch ? Math.min(parseInt(topMatch[1]),30) : 10;

  filtered.sort(function(a,b){return (b[sortField]||0)-(a[sortField]||0);});
  var shown = filtered.slice(0,limit);

  return buildListResponse(shown, filtered.length, sortField, sortLabel, isDorm);
}

function applyFilters(props, qn) {
  var result = props.slice();

  // Delegacion
  var delegaciones = [
    ['iztapalapa','iztapalapa'],
    ['alvaro obregon','alvaro obregon'],['obregon','alvaro obregon'],
    ['gustavo a madero','gustavo a. madero'],['gam','gustavo a'],
    ['coyoacan','coyoacan'],
    ['benito juarez','benito juarez'],['bdj','benito juarez'],
    ['miguel hidalgo','miguel hidalgo'],
    ['cuauhtemoc','cuauhtemoc'],
    ['venustiano carranza','venustiano carranza'],
    ['tlalpan','tlalpan'],
    ['xochimilco','xochimilco'],
    ['azcapotzalco','azcapotzalco'],
    ['iztacalco','iztacalco'],
    ['milpa alta','milpa alta'],
    ['magdalena contreras','magdalena contreras'],
    ['cuajimalpa','cuajimalpa'],
    ['tlahuac','tlahuac'],
  ];
  delegaciones.forEach(function(pair) {
    if (qn.includes(pair[0])) {
      result = result.filter(function(p) {
        return removeAccents((p.delegacion||'').toLowerCase()).includes(pair[1]);
      });
    }
  });

  // Quality nivel
  ['excelente','alta','media','baja','critica'].forEach(function(n) {
    if (qn.includes(n)) {
      result = result.filter(function(p) {
        return removeAccents((p.quality_nivel||'').toLowerCase()).includes(n);
      });
    }
  });

  // Score threshold
  var scoreMatch = qn.match(/(score|calidad)\\s*(>=|<=|>|<|mayor|menor)\\s*(\\d+)/);
  if (scoreMatch) {
    var op=scoreMatch[2], val=parseInt(scoreMatch[3]);
    result = result.filter(function(p) {
      var s=p.quality_score||0;
      if(op==='>='||op==='mayor') return s>=val;
      if(op==='<='||op==='menor') return s<=val;
      if(op==='>') return s>val;
      if(op==='<') return s<val;
      return true;
    });
  }

  // Menu filters
  if (/menu\\s+bizne|con\\s+bizne/.test(qn)) result=result.filter(function(p){return p.menu_bizne;});
  if (/menu\\s+(del?\\s+)?dia|menu.*dia/.test(qn)) result=result.filter(function(p){return p.menu_dia;});
  if (/menu\\s+(a\\s+la\\s+)?carta|con\\s+carta/.test(qn)) result=result.filter(function(p){return p.menu_carta;});
  if (/sin\\s+menu/.test(qn)) result=result.filter(function(p){return !p.menu_bizne&&!p.menu_dia&&!p.menu_carta;});
  if (/sin\\s+(tx|transacc|compras|pedidos|ventas)/.test(qn)) result=result.filter(function(p){return (p.tx_30d||0)===0;});

  // Etapa
  var etapaMatch = qn.match(/etapa\\s+([a-z0-9]+)/);
  if (etapaMatch) {
    var eq = etapaMatch[1];
    result = result.filter(function(p){return removeAccents((p.etapa||'').toLowerCase()).includes(eq);});
  }

  // Service cohort
  var cohortMatch = qn.match(/cohort\\s+([a-z0-9\\-_]+)/);
  if (cohortMatch) {
    var cq = cohortMatch[1];
    result = result.filter(function(p){return removeAccents((p.service_cohort||'').toLowerCase()).includes(cq);});
  }

  return result;
}

function scoreColor(s) {
  if (s>=80) return '#22c55e';
  if (s>=60) return '#00BFA5';
  if (s>=40) return '#f59e0b';
  if (s>=20) return '#f97316';
  return '#ef4444';
}

function buildListResponse(items, total, sortField, sortLabel, isDorm) {
  if (items.length===0)
    return '😕 <b>Sin resultados</b> con ese criterio. Prueba otro filtro o escribe "ayuda" para ver ejemplos.';

  var header = '📋 <b>' + items.length + '</b>' +
    (total>items.length ? ' de <b>'+total+'</b>' : '') +
    ' negocio' + (items.length===1?'':'s') + (isDorm?' dormido':'') +
    ' · por <b>' + sortLabel + '</b>:<br><br>';

  var rows = items.map(function(p,i) {
    var del = p.delegacion
      ? ' <span class="cm-tag">'+p.delegacion+'</span>' : '';
    var sc = isDorm ? '' : ' <b style="color:'+scoreColor(p.quality_score||0)+'">'+Math.round(p.quality_score||0)+'</b>';
    var txVal = isDorm ? (p.tx_historicas||0)+' tx hist.' : (p.tx_30d||0)+' tx';
    var extra = ' <span style="color:#475569;font-size:9px">'+txVal+'</span>';
    var menus = [];
    if (p.menu_bizne) menus.push('<span style="color:#22c55e;font-size:9px">Bizne</span>');
    if (p.menu_dia)   menus.push('<span style="color:#00BFA5;font-size:9px">Día</span>');
    if (p.menu_carta) menus.push('<span style="color:#a78bfa;font-size:9px">Carta</span>');
    var menuStr = menus.length ? ' '+menus.join(' ') : '';
    return '<span style="color:#475569">'+(i+1)+'.</span> <b>'+p.nombre+'</b>'+del+sc+extra+menuStr;
  }).join('<br>');

  var footer = total>items.length
    ? '<br><span style="color:#334155;font-size:9px">→ escribe "top '+Math.min(total,30)+'" para ver más</span>'
    : '';

  return header + rows + footer;
}

function buildCountResponse(props, field, isDorm) {
  var counts = {};
  props.forEach(function(p) {
    var val = p[field] || 'Sin dato';
    if (!val || val==='nan' || val==='None') val='Sin dato';
    counts[val] = (counts[val]||0) + 1;
  });

  var sorted = Object.keys(counts).sort(function(a,b){return counts[b]-counts[a];});
  var fieldLabels = {
    delegacion:'delegación', quality_nivel:'nivel de calidad',
    etapa:'etapa', service_cohort:'cohort'
  };

  var header = '📊 <b>'+props.length+'</b> negocio'+(props.length===1?'':'s')+
    (isDorm?' dormidos':'') + ' por <b>'+(fieldLabels[field]||field)+'</b>:<br>';

  var rows = '<table class="cm-count-table">';
  sorted.forEach(function(k) {
    var pct = Math.round(counts[k]/props.length*100);
    var bar = '';
    for(var b=0;b<Math.round(pct/5);b++) bar+='▪';
    rows += '<tr><td>'+k+'</td><td>'+bar+' '+counts[k]+
      ' <span style="color:#334155">('+pct+'%)</span></td></tr>';
  });
  rows += '</table>';

  return header + rows;
}

// Draggable chat panel
document.addEventListener('DOMContentLoaded', function() {
  var panel = document.getElementById('chat-panel');
  var head  = document.getElementById('chat-head');
  if (!panel || !head) return;
  head.addEventListener('mousedown', function(e) {
    if (e.target.id==='chat-close-btn') return;
    e.preventDefault();
    var rect = panel.getBoundingClientRect();
    var ox = e.clientX - rect.left, oy = e.clientY - rect.top;
    function drag(e2) {
      panel.style.left   = (e2.clientX-ox)+'px';
      panel.style.top    = (e2.clientY-oy)+'px';
      panel.style.right  = 'auto';
      panel.style.bottom = 'auto';
    }
    document.addEventListener('mousemove', drag);
    document.addEventListener('mouseup', function(){document.removeEventListener('mousemove',drag);},{once:true});
  });
});

})();
</script>"""

JS = f"""<script>
var HEX_DATA            = {HEX_DATA};
var BIZ_DATA            = {BIZ_DATA};
var DORM_DATA           = {DORM_DATA};
var METRO_DATA          = {METRO_DATA};
var UPC_DATA            = {UPC_DATA};
var SEC_DATA            = {SEC_DATA};
var HUNTER_DATA         = {HUNTER_DATA};
var SESSION_DEMAND_DATA = {SESSION_DEMAND_DATA};
var ACTIV_DATA          = {ACTIV_DATA};
var HEAT_TRX_OK         = {HEAT_TRX_OK};
var HEAT_TRX_FAIL       = {HEAT_TRX_FAIL};
var HEAT_USERS          = {HEAT_USERS};
var HEX_HEAT_OK         = {HEX_HEAT_OK};
var HEX_HEAT_FAIL       = {HEX_HEAT_FAIL};
var HEX_HEAT_USERS      = {HEX_HEAT_USERS};

// ── Hunters para asignación ─────────────────────────────────────
var HUNTERS_LIST = {HUNTERS_LIST_JSON};
// ── Datos por organización × fecha ──────────────────────────────
var ORG_DATE_KPI_DATA   = {ORG_DATE_KPI_DATA};
var HEAT_USERS_BY_ORG   = {HEAT_USERS_BY_ORG};
var SESSION_DEMAND_BY_ORG = {SESSION_DEMAND_BY_ORG};
var _currentOrg         = 'Todas';
var _currentRange       = 'todo';

var IS_DARK  = false;
var TILE_DARK  = "https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png";
var TILE_LIGHT = "https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png";

function switchTab(name, btn) {{
  document.querySelectorAll('.gpanel').forEach(function(p){{p.classList.remove('active');}});
  document.querySelectorAll('.gtab').forEach(function(t){{t.classList.remove('active');}});
  var panel = document.getElementById('gpanel-'+name);
  if(panel) panel.classList.add('active');
  if(btn) btn.classList.add('active');
}}

// ── Hunter popup fijo (click) con reverse geocode ────────────────
var _hunterPopup = L.popup({{maxWidth:320, className:'hunter-popup'}});

function _copyBtn(value, label) {{
  return '<button class="hpop-copy" data-val="'+encodeURIComponent(value)+'" data-lbl="'+encodeURIComponent(label)+'" '+
    'style="background:none;border:1px solid #334155;border-radius:4px;color:#94a3b8;'+
    'cursor:pointer;font-size:10px;padding:1px 6px;margin-left:4px;">'+label+'</button>';
}}

function openHunterPopup(p, latlng) {{
  var coordStr = p.lat.toFixed(7)+', '+p.lng.toFixed(7);
  var html =
    '<div style="font-family:system-ui,sans-serif;font-size:12px;color:#e2e8f0;min-width:240px">'+
    '<b style="color:'+p.fill_color+'">'+p.zona+'</b> · <b>Rank #'+p.rank+'</b>'+
    '<hr style="border:none;border-top:1px solid #334155;margin:6px 0">'+
    '<div style="margin-bottom:4px">'+
    '<span style="color:#94a3b8">📍 Coordenadas:</span><br>'+
    '<span style="font-family:monospace;font-size:11px">'+coordStr+'</span>'+
    _copyBtn(coordStr, '📋 Copiar coords')+
    '</div>'+
    '<div id="hunter-addr-'+p.hex_code+'" style="color:#94a3b8;font-size:11px;margin-top:4px">'+
    '🔍 Buscando dirección...</div>'+
    '</div>';
  _hunterPopup.setLatLng(latlng).setContent(html).openOn(window.THE_MAP);

  // Reverse geocode con Nominatim
  fetch('https://nominatim.openstreetmap.org/reverse?lat='+p.lat+'&lon='+p.lng+'&format=json&addressdetails=1')
    .then(function(r){{ return r.json(); }})
    .then(function(d){{
      var addr = d.display_name || 'Dirección no disponible';
      // Versión corta: colonia + delegación
      var a = d.address || {{}};
      var short = [a.neighbourhood||a.suburb||a.quarter, a.city_district||a.borough, a.city||a.town]
        .filter(Boolean).join(', ') || addr;
      var el = document.getElementById('hunter-addr-'+p.hex_code);
      if (el) {{
        el.innerHTML = '<span style="color:#94a3b8">🏘 Dirección:</span><br>'+
          '<span style="font-size:11px">'+short+'</span>'+
          _copyBtn(short, '📋 Copiar dir.')+
          '<br><span style="font-size:9px;color:#475569">'+addr+'</span>';
      }}
    }})
    .catch(function(){{
      var el = document.getElementById('hunter-addr-'+p.hex_code);
      if (el) el.textContent = 'Dirección no disponible';
    }});
}}

// Copiar — delegación de eventos para .hpop-copy y .copy-coord-btn
document.addEventListener('click', function(e){{
  var btn = e.target.closest('.hpop-copy');
  if (btn) {{
    var val = decodeURIComponent(btn.getAttribute('data-val'));
    var lbl = decodeURIComponent(btn.getAttribute('data-lbl'));
    navigator.clipboard.writeText(val).then(function(){{
      btn.textContent = '✅';
      setTimeout(function(){{ btn.textContent = lbl; }}, 1400);
    }});
    return;
  }}
  var btn2 = e.target.closest('.copy-coord-btn');
  if (btn2) {{
    navigator.clipboard.writeText(btn2.getAttribute('data-coord')).then(function(){{
      btn2.textContent = '✅';
      setTimeout(function(){{ btn2.textContent = '📋'; }}, 1200);
    }});
    return;
  }}
  // Botón remover zona del pending
  var btn3 = e.target.closest('.az-rm');
  if (btn3) {{
    var hid = decodeURIComponent(btn3.getAttribute('data-hid') || '');
    if (hid) removePendingZone(hid);
  }}
  var btn4 = e.target.closest('.az-copy-link');
  if (btn4) {{
    var rawUrl = decodeURIComponent(btn4.getAttribute('data-url') || '');
    if (rawUrl) {{
      navigator.clipboard.writeText(rawUrl).then(function() {{
        btn4.textContent = '✅ Copiado';
        setTimeout(function(){{ btn4.textContent = '📋 Copiar link'; }}, 2000);
      }}).catch(function() {{
        prompt('Copia este link:', rawUrl);
      }});
    }}
  }}
}});
function refreshMap(){{
  var btn = document.getElementById('refresh-btn');
  btn.classList.add('spinning');
  btn.disabled = true;
  setTimeout(function(){{ location.reload(true); }}, 300);
}}
// ── Helpers de DOM ────────────────────────────────────────────────
function _setText(id, val) {{ var e=document.getElementById(id); if(e) e.textContent=val; }}
function _setHtml(id, val) {{ var e=document.getElementById(id); if(e) e.innerHTML=val; }}
function _setColor(id, val) {{ var e=document.getElementById(id); if(e) e.style.color=val; }}
function _setWidth(id, w) {{ var e=document.getElementById(id); if(e) e.style.width=Math.min(100,Math.max(0,w))+'%'; }}
function _setBg(id, val) {{ var e=document.getElementById(id); if(e) e.style.background=val; }}

// ── Actualizar KPI metrics de usuarios ────────────────────────────
function updateKPIMetrics() {{
  try {{
    var orgData = ORG_DATE_KPI_DATA[_currentOrg];
    if (!orgData) {{ console.warn('Org no encontrada:', _currentOrg, Object.keys(ORG_DATE_KPI_DATA)); return; }}
    var d = orgData[_currentRange] || orgData['todo'];
    if (!d) {{ console.warn('Rango no encontrado:', _currentRange); return; }}
    console.log('updateKPIMetrics:', _currentOrg, _currentRange, d);

    var convColor = (d.conv_reg||0) >= 20 ? '#16a34a' : '#d97706';
    var supColor  = (d.pct_sin_supply||0) < 10 ? '#16a34a' : '#dc2626';

    _setText('kpi-signups',     d.signups);
    _setText('kpi-aprobados',   d.aprobados);
    _setText('kpi-ap-pct',      d.ap_pct + '%');
    _setText('kpi-ap-pct2',     d.ap_pct + '%');
    _setColor('kpi-ap-pct2',    '#00897B');
    _setText('kpi-conv-reg',    d.conv_reg + '%');
    _setColor('kpi-conv-reg',   convColor);
    _setText('kpi-conv-primer', d.conv_primer + '%');
    _setText('kpi-aprov-sin',   d.aprov_sin + '%');
    _setText('kpi-dias-prom',   d.dias_prom + 'd');
    _setText('kpi-sin-supply',  d.pct_sin_supply + '%');
    _setColor('kpi-sin-supply', supColor);

    // Barras verticales del embudo
    var H = 32, total = Math.max(d.signups||1, 1);
    var apH   = Math.max(2, Math.round((d.aprobados||0) / total * H));
    var convH = Math.max(2, Math.round((d.conv_reg||0) / 100 * H));
    var apEl  = document.getElementById('kpi-bar-ap-v');
    var cvEl  = document.getElementById('kpi-bar-conv-v');
    if (apEl) apEl.style.height = apH + 'px';
    if (cvEl) {{ cvEl.style.height = convH + 'px'; cvEl.style.background = convColor; }}

    // Indicador visual en el header
    var hdr = document.getElementById('kpi-dash-header');
    var lbl = document.getElementById('kpi-org-label');
    var parts = [];
    if (_currentOrg !== 'Todas') parts.push(_currentOrg);
    if (_currentRange !== 'todo') parts.push(_currentRange);
    var isFiltered = parts.length > 0;
    if (hdr) hdr.style.background = isFiltered ? '#0077b6' : '#00BFA5';
    if (lbl) lbl.textContent = isFiltered ? '· ' + parts.join(' · ') : '';

    // Resaltar botón de rango activo
    document.querySelectorAll('.dr-btn').forEach(function(b) {{
      b.classList.toggle('active', b.getAttribute('data-range') === _currentRange);
    }});
  }} catch(e) {{ console.error('updateKPIMetrics error:', e); }}
}}

// ── Cambiar organización ──────────────────────────────────────────
function switchOrg(org) {{
  console.log('switchOrg called:', org);
  _currentOrg = org;
  updateKPIMetrics();

  // Heat users por org
  try {{
    var pts = (HEAT_USERS_BY_ORG && HEAT_USERS_BY_ORG[org]) || HEAT_USERS_BY_ORG['Todas'] || [];
    if (window.THE_MAP) {{
      if (window.LYR_HEAT_USERS) window.THE_MAP.removeLayer(window.LYR_HEAT_USERS);
      window.LYR_HEAT_USERS = L.heatLayer(pts, {{radius:20,blur:15,maxZoom:14,
        gradient:{{0.2:'#5b21b6',0.5:'#7c3aed',0.8:'#a78bfa',1:'#c4b5fd'}}}});
      var htCb = document.getElementById('ht_users');
      if (htCb && htCb.checked) window.LYR_HEAT_USERS.addTo(window.THE_MAP);
    }}
  }} catch(e) {{ console.error('switchOrg heat error:', e); }}

  // Session demand por org
  try {{ _rebuildSessionDemand(org); }} catch(e) {{ console.error('switchOrg SD error:', e); }}
}}

// ── Cambiar rango de fecha ────────────────────────────────────────
function switchDateRange(range) {{
  console.log('switchDateRange:', range);
  _currentRange = range;
  updateKPIMetrics();
}}

// ── Reconstruir capa session demand ──────────────────────────────
function _rebuildSessionDemand(org) {{
  if (!window.THE_MAP || typeof SESSION_DEMAND_BY_ORG === 'undefined') return;
  var sdData = SESSION_DEMAND_BY_ORG[org] || SESSION_DEMAND_BY_ORG['Todas'];
  if (!sdData) return;
  var wasVisible = window.LYR_SESSION_DEMAND && window.THE_MAP.hasLayer(window.LYR_SESSION_DEMAND);
  if (window.LYR_SESSION_DEMAND) window.THE_MAP.removeLayer(window.LYR_SESSION_DEMAND);
  window.LYR_SESSION_DEMAND = L.geoJSON(sdData, {{
    pane:'heatHexPane',
    style:function(f){{
      return {{color:f.properties.fill_color,weight:0.5,
               fillColor:f.properties.fill_color,
               fillOpacity:f.properties.fill_opacity,dashArray:"3 2"}};
    }},
    onEachFeature:function(f,l){{
      var p=f.properties, c=p.fill_color;
      var nc = p.n_cercanos===0 ? "Sin negocios cerca" : p.n_cercanos+" cerca";
      l.bindTooltip(
        "<b style='color:"+c+"'>"+p.tier_label+"</b><br>"+
        "Usuarios: "+p.n_users+" · Con tx: "+p.n_con_tx+"<br>"+
        nc+"<br>Signal: "+p.score_norm_pct+"%",
        {{sticky:true,opacity:0.97}});
    }}
  }});
  if (wasVisible) window.LYR_SESSION_DEMAND.addTo(window.THE_MAP);
}}

// ════════════════════════════════════════════════════════════════
// RUTAS DE HUNTING — Asignación de zonas a hunters
// ════════════════════════════════════════════════════════════════
var _ASSIGN_COLORS = [
  '#f97316','#3b82f6','#22c55e','#a855f7','#ec4899',
  '#14b8a6','#f59e0b','#6366f1','#ef4444','#84cc16'
];
var _assignMode      = false;        // si el modo de selección está activo
var _pendingZones    = [];           // [{{hex_id, rank, zona, gap, lat, lng, score, ...}}]
var _selectedHunter  = null;         // hunter actualmente seleccionado en el panel
var _assignments     = {{}};           // {{hunter_name: [{{hex_id, rank, zona, ...}}]}}
var _hunterColorMap  = {{}};           // {{hunter_name: color}}
var LYR_ASSIGNED     = null;
var LYR_ROUTES       = null;

// ── Inicializar panel de hunters ─────────────────────────────────
function _initAssignPanel() {{
  var list = document.getElementById('az-hunter-list');
  if (!list || list.children.length > 0) return;
  HUNTERS_LIST.forEach(function(h, i) {{
    _hunterColorMap[h] = _ASSIGN_COLORS[i % _ASSIGN_COLORS.length];
    var row = document.createElement('div');
    row.className = 'az-hunter-row';
    row.setAttribute('data-hunter', h);
    row.innerHTML =
      '<div class="az-dot" style="background:'+_hunterColorMap[h]+'"></div>'+
      '<span style="font-size:11px;color:#e2e8f0;flex:1">'+h+'</span>'+
      '<span class="az-h-count" style="font-size:9px;color:#94a3b8">0 zonas</span>';
    row.onclick = function() {{
      document.querySelectorAll('.az-hunter-row').forEach(function(r){{r.classList.remove('selected');}});
      row.classList.add('selected');
      _selectedHunter = h;
    }};
    list.appendChild(row);
  }});
  // Auto-seleccionar el primero
  if (list.firstChild) list.firstChild.click();
}}

// ── Abrir/cerrar panel ───────────────────────────────────────────
function toggleAssignPanel() {{
  var p = document.getElementById('assign-panel');
  if (p.classList.toggle('open')) {{
    _initAssignPanel();
    // Mostrar semana actual
    var wLabel = document.getElementById('az-current-week');
    if (wLabel) wLabel.textContent = weekLabel(getISOWeek());
    refreshWeekDropdown();
    // Auto-cargar asignaciones de la semana actual si existen
    try {{
      var saved = localStorage.getItem('bizne_assign_' + getISOWeek());
      if (saved) {{
        _assignments = JSON.parse(saved);
        renderAssignedLayer();
        renderRoutes();
        updateAssignedSummary();
      }}
    }} catch(e) {{}}
  }} else {{
    if (_assignMode) toggleAssignMode(); // apagar modo selección al cerrar
  }}
}}

// ── Activar/desactivar modo de selección ────────────────────────
function toggleAssignMode() {{
  _assignMode = !_assignMode;
  var btn = document.getElementById('az-mode-btn');
  var bar = document.getElementById('assign-mode-bar');
  var tb  = document.getElementById('assign-tool-btn');
  if (btn) btn.textContent = _assignMode ? '⏹ Desactivar' : 'Activar selección';
  if (btn) btn.style.background = _assignMode ? '#7c2d12' : 'none';
  if (bar) bar.style.display = _assignMode ? 'block' : 'none';
  if (tb)  tb.classList.toggle('active', _assignMode);
  // Cambiar cursor del mapa — pointer (no crosshair) para indicar que se hace clic en hexes
  var mapEl = document.getElementById('map');
  if (mapEl) mapEl.style.cursor = _assignMode ? 'pointer' : '';
  // Si el marquee está activo, apagarlo — su mousedown haría stopPropagation y tragaría los clicks
  if (_assignMode && typeof _mqActive !== 'undefined' && _mqActive) {{
    toggleMarqueeTool();
  }}
  // En assign mode: deshabilitar pointer-events de TODOS los panes excepto hunterPane.
  // IMPORTANTE: overlayPane (z-index 400) es el pane default de Leaflet donde viven
  // LYR_BIZ, LYR_DORM, LYR_METRO, LYR_UPCS, LYR_SEC — sus circleMarkers interceptan
  // clicks por encima del hunterPane si no se deshabilitan explícitamente.
  var _disablePanes = ['hexPane','sessionDemandPane','heatHexPane',
                       'overlayPane','shadowPane','markerPane'];
  if (window.THE_MAP) {{
    _disablePanes.forEach(function(name) {{
      var p = window.THE_MAP.getPane(name);
      if (!p) return;
      if (_assignMode) {{
        p._prevPE = p.style.pointerEvents;   // guardar estado previo
        p.style.pointerEvents = 'none';
      }} else {{
        p.style.pointerEvents = p._prevPE !== undefined ? p._prevPE : '';
      }}
    }});
    // hunterPane: garantizar pointer-events auto y z-index arriba
    var hp = window.THE_MAP.getPane('hunterPane');
    if (hp) {{
      hp.style.pointerEvents = 'auto';
      hp.style.zIndex = _assignMode ? '395' : '330';
    }}
  }}
  if (_assignMode && window.LYR_HUNTER) {{
    window.LYR_HUNTER.bringToFront();
  }}
}}

// ── Agregar zona al pending (llamado desde el click del hunter hex) ──
function addZoneToPending(p) {{
  if (!_assignMode) return false;
  // Verificar si ya está asignada
  var alreadyAssigned = Object.values(_assignments).some(function(list) {{
    return list.some(function(z) {{ return z.hex_id === p.hex_id; }});
  }});
  if (alreadyAssigned) {{ alert('Esta zona ya está asignada a un hunter.'); return false; }}
  // Verificar si ya está en pending
  var idx = _pendingZones.findIndex(function(z) {{ return z.hex_id === p.hex_id; }});
  if (idx >= 0) {{
    // Remover si ya estaba
    _pendingZones.splice(idx, 1);
  }} else {{
    _pendingZones.push(p);
  }}
  renderPendingList();
  return true;
}}

// ── Renderizar lista de zonas pendientes ─────────────────────────
function renderPendingList() {{
  var list = document.getElementById('az-pending-list');
  var cnt  = document.getElementById('az-count');
  if (!list) return;
  if (cnt) cnt.textContent = _pendingZones.length;
  list.innerHTML = '';
  _pendingZones.forEach(function(z) {{
    var item = document.createElement('div');
    item.className = 'az-zone-item';
    item.innerHTML =
      '<span style="color:#f97316;font-weight:700">#'+z.rank+'</span>'+
      '<span style="flex:1">'+z.zona+'</span>'+
      '<span style="color:#ef4444;font-size:9px">Gap '+z.gap+'</span>'+
      '<button class="az-rm" data-hid="'+encodeURIComponent(z.hex_id)+'">✕</button>';
    list.appendChild(item);
  }});
}}

function removePendingZone(hex_id) {{
  _pendingZones = _pendingZones.filter(function(z) {{ return z.hex_id !== hex_id; }});
  renderPendingList();
  renderAssignedLayer();
}}

function clearPendingZones() {{
  _pendingZones = [];
  renderPendingList();
}}

// ── Asignar zonas pendientes al hunter seleccionado ──────────────
function assignZonesToHunter() {{
  if (!_selectedHunter) {{ alert('Selecciona un hunter primero.'); return; }}
  if (!_pendingZones.length) {{ alert('No hay zonas seleccionadas.'); return; }}
  if (!_assignments[_selectedHunter]) _assignments[_selectedHunter] = [];
  _pendingZones.forEach(function(z) {{
    if (!_assignments[_selectedHunter].find(function(a){{ return a.hex_id === z.hex_id; }}))
      _assignments[_selectedHunter].push(z);
  }});
  _pendingZones = [];
  renderPendingList();
  renderAssignedLayer();
  renderRoutes();
  updateAssignedSummary();
  saveAssignmentsToStorage();
}}

// ── Helpers de semana ISO ────────────────────────────────────────
function getISOWeek(d) {{
  var date = d ? new Date(d) : new Date();
  var day = date.getDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  var yearStart = new Date(Date.UTC(date.getUTCFullYear(),0,1));
  var w = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
  return date.getUTCFullYear() + '-W' + String(w).padStart(2,'0');
}}
function weekLabel(key) {{
  // key = "2026-W23" → "Semana 23 (Jun 1–7, 2026)"
  var parts = key.split('-W');
  if (parts.length !== 2) return key;
  var year = parseInt(parts[0]), week = parseInt(parts[1]);
  // Lunes de esa semana ISO
  var jan4 = new Date(year, 0, 4);
  var monday = new Date(jan4.getTime() + (week - 1) * 7 * 86400000);
  monday.setDate(monday.getDate() - (monday.getDay() || 7) + 1);
  var sunday = new Date(monday.getTime() + 6 * 86400000);
  var months = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
  return 'Sem '+week+' · '+months[monday.getMonth()]+' '+monday.getDate()+'–'+sunday.getDate()+' '+year;
}}
function saveAssignmentsToStorage() {{
  try {{
    var key = 'bizne_assign_' + getISOWeek();
    localStorage.setItem(key, JSON.stringify(_assignments));
    refreshWeekDropdown();
  }} catch(e) {{ console.warn('localStorage no disponible:', e); }}
}}
function loadAssignmentsFromStorage(weekKey) {{
  try {{
    var raw = localStorage.getItem('bizne_assign_' + weekKey);
    if (!raw) {{ alert('No hay asignaciones guardadas para esa semana.'); return; }}
    _assignments = JSON.parse(raw);
    renderPendingList();
    renderAssignedLayer();
    renderRoutes();
    updateAssignedSummary();
  }} catch(e) {{ console.warn('Error al cargar semana:', e); }}
}}
function refreshWeekDropdown() {{
  var sel = document.getElementById('az-week-select');
  if (!sel) return;
  var currentVal = sel.value;
  // Listar todas las semanas guardadas
  var weeks = [];
  for (var i = 0; i < localStorage.length; i++) {{
    var k = localStorage.key(i);
    if (k && k.startsWith('bizne_assign_')) weeks.push(k.replace('bizne_assign_',''));
  }}
  weeks.sort().reverse();
  sel.innerHTML = '<option value="">— cargar semana guardada —</option>';
  weeks.forEach(function(w) {{
    var opt = document.createElement('option');
    opt.value = w; opt.textContent = weekLabel(w);
    if (w === currentVal) opt.selected = true;
    sel.appendChild(opt);
  }});
}}
// ── Generar links por hunter (base64 encoded) ────────────────────
function generateHunterLinks() {{
  var container = document.getElementById('az-links-container');
  if (!container) return;
  container.innerHTML = '';
  var base = window.location.origin + window.location.pathname;
  var semana = getISOWeek();
  Object.keys(_assignments).forEach(function(h) {{
    var zones = _assignments[h];
    if (!zones.length) return;
    var payload = JSON.stringify({{hunter:h, semana:semana, zonas:zones}});
    var encoded = btoa(unescape(encodeURIComponent(payload)));
    var url = base + '?hunter=' + encodeURIComponent(h) + '&semana=' + semana + '&data=' + encoded;
    var row = document.createElement('div');
    row.style.cssText = 'margin-bottom:6px;display:flex;align-items:center;gap:6px;';
    var color = _hunterColorMap[h] || '#94a3b8';
    row.innerHTML =
      '<div style="width:8px;height:8px;border-radius:50%;background:'+color+';flex-shrink:0"></div>'+
      '<span style="font-size:10px;color:#e2e8f0;flex:1">'+h+'</span>'+
      '<button class="az-copy-link" data-url="'+encodeURIComponent(url)+'" '+
      'style="font-size:9px;padding:2px 8px;background:#1e3a52;border:1px solid #334155;'+
      'color:#94a3b8;border-radius:4px;cursor:pointer">📋 Copiar link</button>';
    container.appendChild(row);
  }});
  if (!container.innerHTML) container.innerHTML = '<div style="font-size:10px;color:#64748b">No hay asignaciones todavía.</div>';
}}

// ── Actualizar resumen de asignaciones ───────────────────────────
function updateAssignedSummary() {{
  var total = Object.values(_assignments).reduce(function(s,v){{ return s+v.length; }},0);
  var el = document.getElementById('az-total-assigned');
  if (el) el.textContent = total;
  var summary = document.getElementById('az-assigned-summary');
  if (!summary) return;
  summary.innerHTML = '';
  Object.keys(_assignments).forEach(function(h) {{
    var zones = _assignments[h];
    if (!zones.length) return;
    var color = _hunterColorMap[h] || '#94a3b8';
    var row = document.createElement('div');
    row.style.cssText = 'margin-bottom:6px';
    row.innerHTML =
      '<div style="display:flex;align-items:center;gap:5px;margin-bottom:3px">'+
      '<div style="width:10px;height:10px;border-radius:50%;background:'+color+'"></div>'+
      '<span style="font-weight:700;font-size:10px;color:'+color+'">'+h+'</span>'+
      '<span style="font-size:9px;color:#94a3b8;margin-left:auto">'+zones.length+' zonas</span></div>'+
      zones.map(function(z, i) {{
        return '<div style="font-size:9px;color:#94a3b8;padding:1px 4px">'+
          (i+1)+'. Rank#'+z.rank+' · '+z.zona+' · Gap '+z.gap+'</div>';
      }}).join('');
    summary.appendChild(row);
    // Actualizar contador en la lista de hunters
    var hRows = document.querySelectorAll('[data-hunter="'+h+'"] .az-h-count');
    hRows.forEach(function(el){{ el.textContent = zones.length+' zonas'; }});
  }});
}}

// ── Renderizar capa de zonas asignadas ───────────────────────────
function renderAssignedLayer() {{
  if (!window.THE_MAP) return;
  if (LYR_ASSIGNED) window.THE_MAP.removeLayer(LYR_ASSIGNED);
  if (LYR_ROUTES)   window.THE_MAP.removeLayer(LYR_ROUTES);

  var features = [];
  // Zonas asignadas (sólidas)
  Object.keys(_assignments).forEach(function(h) {{
    var color = _hunterColorMap[h] || '#94a3b8';
    _assignments[h].forEach(function(z, i) {{
      // Buscar geometría en HUNTER_DATA
      var match = HUNTER_DATA.features.find(function(f){{ return f.properties.hex_id === z.hex_id; }});
      if (!match) return;
      var feat = JSON.parse(JSON.stringify(match));
      feat.properties._az_hunter = h;
      feat.properties._az_color  = color;
      feat.properties._az_order  = i + 1;
      features.push(feat);
    }});
  }});
  // Zonas pendientes (punteadas)
  _pendingZones.forEach(function(z) {{
    var match = HUNTER_DATA.features.find(function(f){{ return f.properties.hex_id === z.hex_id; }});
    if (!match) return;
    var feat = JSON.parse(JSON.stringify(match));
    feat.properties._az_hunter = 'pending';
    feat.properties._az_color  = '#ffffff';
    features.push(feat);
  }});

  if (!features.length) return;
  LYR_ASSIGNED = L.geoJSON({{type:'FeatureCollection',features:features}}, {{
    pane:'heatHexPane',
    style: function(f) {{
      var p = f.properties;
      var isPending = p._az_hunter === 'pending';
      return {{
        color:       p._az_color,
        weight:      isPending ? 2.5 : 3,
        fillColor:   p._az_color,
        fillOpacity: isPending ? 0.15 : 0.45,
        dashArray:   isPending ? '6 3' : null,
        opacity:     0.9,
      }};
    }},
    onEachFeature: function(f, l) {{
      var p = f.properties;
      if (p._az_hunter !== 'pending') {{
        l.bindTooltip(
          '<b style="color:'+p._az_color+'">🗺 '+p._az_hunter+'</b> · Ruta #'+p._az_order+'<br>'+
          'Rank #'+p.rank+' · Gap: '+p.gap+' 🍽<br>'+
          '<span style="font-size:9px;color:#94a3b8">Click para quitar</span>',
          {{sticky:true, opacity:0.97}});
        l.on('click', function() {{
          if (_assignMode) return;
          var h = p._az_hunter;
          if (_assignments[h]) {{
            _assignments[h] = _assignments[h].filter(function(z){{ return z.hex_id !== p.hex_id; }});
            if (!_assignments[h].length) delete _assignments[h];
          }}
          renderAssignedLayer();
          renderRoutes();
          updateAssignedSummary();
        }});
      }}
    }}
  }}).addTo(window.THE_MAP);
}}

// ── Renderizar rutas (polylines conectando centroides) ───────────
function renderRoutes() {{
  if (!window.THE_MAP) return;
  if (LYR_ROUTES) window.THE_MAP.removeLayer(LYR_ROUTES);
  var routeLayers = [];
  Object.keys(_assignments).forEach(function(h) {{
    var zones = _assignments[h];
    if (zones.length < 2) return;
    var color = _hunterColorMap[h] || '#94a3b8';
    // Ordenar por rank (mayor prioridad primero)
    var sorted = zones.slice().sort(function(a,b){{ return a.rank - b.rank; }});
    var latlngs = sorted.map(function(z){{ return [z.lat, z.lng]; }});
    var line = L.polyline(latlngs, {{
      color: color, weight: 2.5, opacity: 0.7, dashArray: '8 4',
      lineJoin: 'round'
    }});
    // Añadir marcadores de orden
    sorted.forEach(function(z, i) {{
      L.marker([z.lat, z.lng], {{
        icon: L.divIcon({{
          className: '',
          html: '<div style="background:'+color+';color:#fff;border-radius:50%;'+
                'width:18px;height:18px;display:flex;align-items:center;justify-content:center;'+
                'font-size:10px;font-weight:700;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5)">'+
                (i+1)+'</div>',
          iconSize: [18,18], iconAnchor: [9,9]
        }})
      }}).bindTooltip(h+' · Parada #'+(i+1)+': Rank#'+z.rank, {{sticky:true}})
        .addTo(window.THE_MAP);
      routeLayers.push(line);
    }});
    line.addTo(window.THE_MAP);
  }});
}}

// ── Limpiar todas las asignaciones ───────────────────────────────
function clearAllAssignments() {{
  if (!confirm('¿Limpiar todas las asignaciones?')) return;
  _assignments = {{}};
  _pendingZones = [];
  renderPendingList();
  renderAssignedLayer();
  if (LYR_ROUTES) {{ window.THE_MAP.removeLayer(LYR_ROUTES); LYR_ROUTES = null; }}
  updateAssignedSummary();
  saveAssignmentsToStorage();
  document.querySelectorAll('.az-h-count').forEach(function(el){{ el.textContent = '0 zonas'; }});
}}

// ── Exportar asignaciones a CSV ───────────────────────────────────
function exportAssignments() {{
  var rows = ['hunter,orden_ruta,hex_id,rank,zona,gap,demanda_dia,usuarios,lat,lng,score'];
  Object.keys(_assignments).forEach(function(h) {{
    var sorted = _assignments[h].slice().sort(function(a,b){{ return a.rank-b.rank; }});
    sorted.forEach(function(z, i) {{
      rows.push([h, i+1, z.hex_id, z.rank, '"'+z.zona+'"', z.gap,
                 z.demanda_dia||'', z.usuarios||'',
                 z.lat, z.lng, z.combined_score||''].join(','));
    }});
  }});
  if (rows.length === 1) {{ alert('No hay asignaciones para exportar.'); return; }}
  var blob = new Blob([rows.join('\\n')], {{type:'text/csv;charset=utf-8;'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rutas_hunting_' + new Date().toISOString().slice(0,10) + '.csv';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
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
  {{key:"demanda_activacion",id:"hf_activ",label:"Dem. Activación"}},
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
  var s = "<div style='display:flex;justify-content:space-between;align-items:center;gap:12px'>"+
          "<b style='color:"+p.fill_color+"'>"+p.zone_tier.replace(/_/g," ")+"</b>"+
          "<span style='font-family:monospace;font-size:9px;background:#0f172a;color:#60a5fa;padding:1px 6px;border-radius:4px;border:1px solid #1e3a52'>"+p.hex_code+"</span>"+
          "</div>";
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
  var dormColor = p.neg_dormidos > 0 ? '#f59e0b' : '#64748b';
  var unsColor  = p.users_no_supply > 0 ? '#f97316' : '#64748b';
  // Barra de score desglosada
  var gPct  = Math.round((p.gap_norm  ||0)*100);
  var uPct  = Math.round((p.uns_norm  ||0)*100);
  var dPct  = Math.round((p.demand_norm||0)*100);
  var aPct  = Math.round((p.activ_norm ||0)*100);
  var scoreBar =
    "<div style='margin:4px 0 2px;font-size:9px;color:#94a3b8'>Score: <b style='color:#f1f5f9'>"+Math.round(p.combined_score*100)+"/100</b></div>"+
    "<div style='display:flex;gap:1px;height:5px;border-radius:3px;overflow:hidden;margin-bottom:3px'>"+
      "<div style='width:"+(gPct*0.40)+"%;background:#ef4444' title='Gap'></div>"+
      "<div style='width:"+(uPct*0.30)+"%;background:#f97316' title='Sin supply'></div>"+
      "<div style='width:"+(dPct*0.20)+"%;background:#3b82f6' title='Demanda'></div>"+
      "<div style='width:"+(aPct*0.10)+"%;background:#E879F9' title='Activación'></div>"+
    "</div>"+
    "<div style='display:flex;gap:8px;font-size:8px;color:#64748b'>"+
      "<span style='color:#ef4444'>🍽 gap "+gPct+"%</span>"+
      "<span style='color:#f97316'>🚫 supply "+uPct+"%</span>"+
      "<span style='color:#3b82f6'>📊 dem "+dPct+"%</span>"+
      "<span style='color:#E879F9'>⚡ activ "+aPct+"%</span>"+
    "</div>";
  return "<b style='color:"+p.fill_color+"'>"+p.zona+"</b> · <b>Rank #"+p.rank+"</b><br>"+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    scoreBar+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    "<b>🏪 Activos:</b> <span style='color:#00BFA5'>"+p.neg_activos+"</span>  "+
    "<b style='color:#ef4444'>Gap:</b> <span style='color:#ef4444;font-weight:700'>"+p.gap+" 🍽</span>  "+
    "<span style='color:"+dormColor+"'>😴 "+p.neg_dormidos+" dorm.</span><br>"+
    "<b>Demanda est.:</b> "+p.demanda_dia+" tx/día<br>"+
    "<b>👤 Usuarios:</b> "+p.usuarios+
    (p.users_no_supply > 0
      ? " · <span style='color:"+unsColor+";font-weight:700'>⚠ "+p.users_no_supply+" sin cocina cercana</span>"
      : "")+
    "<br>"+p.sin_compras+" sin comprar · Conv: "+p.tasa_conv_pct+"%<br>"+
    (p.activ_demand > 0
      ? "<span style='color:#E879F9;font-size:9px'>⚡ Activación: +"+p.activ_demand+" tx/día</span><br>"
      : "")+
    "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>"+
    "<span style='color:#94a3b8;font-size:10px'>📍 "+
    p.lat.toFixed(7)+", "+p.lng.toFixed(7)+
    " <button class='copy-coord-btn' data-coord='"+p.lat.toFixed(7)+", "+p.lng.toFixed(7)+"' "+
    "style='background:none;border:1px solid #334155;border-radius:4px;color:#94a3b8;cursor:pointer;"+
    "font-size:10px;padding:1px 5px;margin-left:2px;'>📋</button></span>";
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
  if (window.THE_MAP) window.THE_MAP.flyTo([lat,lng], 15, {{animate:true,duration:0.8}});
}};

document.addEventListener("DOMContentLoaded", function() {{
  setTimeout(function() {{
    try {{
    // window.THE_MAP ya está seteado en el inline <script> cuando se creó _bizneMap
    var theMap = window.THE_MAP || window._bizneMap;
    if (!theMap) {{
      console.error('❌ No se encontró el mapa Leaflet');
      return;
    }}
    window.THE_MAP = theMap;

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
        l.bindTooltip(buildHunterTT(f.properties),{{sticky:true,opacity:0.97}});
        l.on('click', function(e){{
          L.DomEvent.stopPropagation(e);
          var p = f.properties;
          // Modo asignación: agregar al pending sin abrir popup
          if (_assignMode) {{
            var added = addZoneToPending(p);
            if (added !== false) {{
              // Pulso visual en el hexágono
              var origStyle = {{color:p.fill_color,fillColor:p.fill_color,fillOpacity:p.fill_opacity}};
              l.setStyle({{color:'#fff',fillColor:'#fff',fillOpacity:0.6}});
              setTimeout(function(){{ l.setStyle(origStyle); }}, 300);
              renderAssignedLayer();
            }}
            return;
          }}
          window.THE_MAP.flyTo([p.lat, p.lng], 15, {{animate:true, duration:0.8}});
          setTimeout(function(){{ openHunterPopup(p, e.latlng); }}, 400);
        }});
        // Label centrado en el hex
        if (f.properties.hex_code) {{
          var center = l.getBounds().getCenter();
          var lbl = L.marker(center, {{
            pane:'hunterPane',
            icon: L.divIcon({{
              className:'',
              html:"<div style='font-family:monospace;font-size:8px;font-weight:700;"+
                   "color:#fff;background:rgba(0,0,0,.55);padding:1px 4px;border-radius:3px;"+
                   "white-space:nowrap;pointer-events:none;text-align:center;"+
                   "transform:translateX(-50%)'>" + f.properties.hex_code + "</div>",
              iconSize:[0,0], iconAnchor:[0,0]
            }})
          }});
          lbl._hunterLabel = true;
          lbl.addTo(theMap);
          l._codeLabel = lbl;
        }}
      }}
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

    // Puntos de Activación
    window.LYR_ACTIV = L.geoJSON(ACTIV_DATA, {{
      pointToLayer: function(f, ll) {{
        var p = f.properties;
        var icon;
        if (p.es_admin) {{
          icon = L.divIcon({{
            className: '',
            html: '<div class="admin-marker">' +
                    '<div class="admin-icon">🏢</div>' +
                    '<div class="admin-label">' + p.nombre + '</div>' +
                  '</div>',
            iconSize: [26, 26],
            iconAnchor: [13, 13],
          }});
        }} else {{
          icon = L.divIcon({{
            className: '',
            html: '<div class="activ-marker">' +
                    '<div class="activ-pulse"></div>' +
                    '<div class="activ-core"></div>' +
                    '<div class="activ-label">' + p.nombre + '</div>' +
                  '</div>',
            iconSize: [22, 22],
            iconAnchor: [11, 11],
          }});
        }}
        return L.marker(ll, {{icon: icon}});
      }},
      onEachFeature: function(f, l) {{
        var p = f.properties;
        if (p.es_admin) {{
          l.bindTooltip(
            "<b style='color:#60a5fa'>🏢 " + p.nombre + "</b><br>" +
            "<span style='color:#94a3b8;font-size:9px'>Edificio Administrativo PA</span><br>" +
            "<i style='color:#64748b;font-size:9px'>" + p.direccion + "</i>",
            {{sticky: true, opacity: 0.97, maxWidth: 260}}
          );
        }} else {{
          l.bindTooltip(
            "<b style='color:#E879F9'>⚡ " + p.nombre + "</b><br>" +
            "<span style='color:#94a3b8;font-size:9px'>" + p.sector + "</span><br>" +
            "<i style='color:#64748b;font-size:9px'>" + p.direccion + "</i>" +
            "<hr style='border:none;border-top:1px solid #1e3a52;margin:4px 0'>" +
            "<b>Elementos estimados:</b> <span style='color:#E879F9'>" + p.elementos_est + "</span><br>" +
            "<b>Demanda est./día:</b> <span style='color:#E879F9;font-weight:700'>" + p.dem_dia_est + " tx</span><br>" +
            "<b>Radio de influencia:</b> " + p.radio_km + " km" +
            "<br><span style='font-size:9px;color:#475569'>Hex vecinos boosteados con decay 65%/35%</span>",
            {{sticky: true, opacity: 0.97, maxWidth: 260}}
          );
        }}
      }}
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
                 metro:window.LYR_METRO,upcs:window.LYR_UPCS,sec:window.LYR_SEC,
                 activ:window.LYR_ACTIV}};
      var lyr = map[name]; if (!lyr) return;
      show ? (!m.hasLayer(lyr) && m.addLayer(lyr)) : (m.hasLayer(lyr) && m.removeLayer(lyr));
      // Sync hunter hex code labels
      if (name === 'hunter' && window.LYR_HUNTER) {{
        window.LYR_HUNTER.eachLayer(function(l) {{
          if (l._codeLabel) {{ show ? l._codeLabel.addTo(m) : m.removeLayer(l._codeLabel); }}
        }});
      }}
    }};
    // ── Inicializar barras verticales del embudo ──────────────────
    (function() {{
      var d = (ORG_DATE_KPI_DATA['Todas'] || {{}})['todo'] || {{}};
      var total = d.signups || 1;
      var H = 32;
      var apH   = Math.round((d.aprobados||0) / total * H);
      var convH = Math.round((d.conv_reg||0)  / 100 * H);
      var regEl  = document.getElementById('kpi-bar-reg-v');
      var apEl   = document.getElementById('kpi-bar-ap-v');
      var convEl = document.getElementById('kpi-bar-conv-v');
      if (regEl)  regEl.style.height  = H + 'px';
      if (apEl)   apEl.style.height   = Math.max(2, apH) + 'px';
      if (convEl) convEl.style.height = Math.max(2, convH) + 'px';
    }})();

    // ── Aplicar visibilidad inicial según checkboxes ───────────────
    ['hexes','dormidas','hunter','sdemand','metro','upcs','sec','activ'].forEach(function(name) {{
      var el = document.getElementById('ly_'+name);
      if (!el || !el.checked) window.toggleLayer(name, false);
    }});

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
    window._bizNuevosDays = 0;  // 0 = sin filtro, 7 = ≤7d, 30 = ≤30d
    window.filterBizNuevos = function(days) {{
      window._bizNuevosDays = parseInt(days) || 0;
      // Sincronizar checkboxes
      var cb7  = document.getElementById('biz-nuevos-7d');
      var cb30 = document.getElementById('biz-nuevos-30d');
      if (cb7)  cb7.checked  = (window._bizNuevosDays === 7);
      if (cb30) cb30.checked = (window._bizNuevosDays === 30);
      var q = (document.getElementById('biz-search')||{{}}).value||'';
      window.searchNegocios(q);
    }};
    // ── Buscar hexágono por código o ID H3 ────────────────────────
    window.searchHex = function(q) {{
      var resultEl = document.getElementById('hex-search-result');
      if (!q || !q.trim()) {{
        if (resultEl) resultEl.textContent = '';
        return;
      }}
      q = q.trim().toUpperCase();
      var found = null;
      var foundProps = null;
      var foundLng, foundLat;

      // 1. Buscar en LYR_HEX (hexes de demanda)
      if (window.LYR_HEX) {{
        window.LYR_HEX.eachLayer(function(l) {{
          if (found) return;
          var p = l.feature ? l.feature.properties : (l._p || {{}});
          if (!p.hex_code && !p.hex_id) return;
          var code = (p.hex_code || '').toUpperCase();
          var hid  = (p.hex_id  || '').toUpperCase();
          if (code === q || hid === q || code.indexOf(q) === 0) {{
            found = l;
            foundProps = p;
          }}
        }});
      }}
      // 2. Buscar en LYR_HUNTER si no encontró
      if (!found && window.LYR_HUNTER) {{
        window.LYR_HUNTER.eachLayer(function(l) {{
          if (found) return;
          var p = l.feature ? l.feature.properties : (l._p || {{}});
          if (!p.hex_code && !p.hex_id) return;
          var code = (p.hex_code || '').toUpperCase();
          var hid  = (p.hex_id  || '').toUpperCase();
          if (code === q || hid === q || code.indexOf(q) === 0) {{
            found = l;
            foundProps = p;
          }}
        }});
      }}
      // 3. Si tiene lat/lng en properties → usar directo
      if (found) {{
        if (foundProps && foundProps.lat && foundProps.lng) {{
          foundLat = foundProps.lat;
          foundLng = foundProps.lng;
        }} else {{
          try {{
            var c = found.getBounds().getCenter();
            foundLat = c.lat; foundLng = c.lng;
          }} catch(e) {{}}
        }}
      }}
      // 4. Fallback: buscar en datos crudos HEX_DATA si LYR_HEX off
      if (!found || (!foundLat && !foundLng)) {{
        var datasets = [HEX_DATA, HUNTER_DATA];
        for (var di = 0; di < datasets.length; di++) {{
          if (foundLat) break;
          var features = datasets[di].features || [];
          for (var fi = 0; fi < features.length; fi++) {{
            var fp = features[fi].properties || {{}};
            var code2 = (fp.hex_code || '').toUpperCase();
            var hid2  = (fp.hex_id  || '').toUpperCase();
            if (code2 === q || hid2 === q || code2.indexOf(q) === 0) {{
              foundProps = fp;
              // Centroide del polígono
              if (fp.lat && fp.lng) {{
                foundLat = fp.lat; foundLng = fp.lng;
              }} else {{
                var coords = (features[fi].geometry || {{}}).coordinates;
                if (coords && coords[0]) {{
                  var ring = coords[0];
                  var slat = 0, slng = 0;
                  ring.forEach(function(c){{ slat+=c[1]; slng+=c[0]; }});
                  foundLat = slat/ring.length; foundLng = slng/ring.length;
                }}
              }}
              break;
            }}
          }}
        }}
      }}

      if (foundLat && foundLng) {{
        window.THE_MAP.flyTo([foundLat, foundLng], 15, {{animate:true, duration:0.8}});
        if (resultEl) resultEl.innerHTML =
          '<span style="color:#22c55e">✓ ' + (foundProps ? (foundProps.hex_code || foundProps.hex_id || q) : q) + '</span>' +
          (foundProps && foundProps.gap !== undefined ? ' · Gap: <b>' + foundProps.gap + '</b> 🍽' : '') +
          (foundProps && foundProps.zona ? ' · ' + foundProps.zona : '');
        // Mostrar tooltip si está en el mapa
        if (found) {{ try {{ found.openTooltip(); }} catch(e){{}} }}
      }} else {{
        if (resultEl) resultEl.innerHTML = '<span style="color:#ef4444">No encontrado: ' + q + '</span>';
      }}
    }};

    window.searchNegocios = function(q) {{
      if (!window.LYR_BIZ) return;
      q = q.toLowerCase().trim();
      var vis=0, tot=0;
      var daysFilter = window._bizNuevosDays || 0;
      window.LYR_BIZ.eachLayer(function(layer) {{
        tot++;
        var p = layer._p || {{}};
        var matchQ   = q==='' || (p.nombre && p.nombre.toLowerCase().indexOf(q)>=0);
        var matchNew = daysFilter === 0 || (parseInt(p.dias_creacion||9999) <= daysFilter);
        var show = matchQ && matchNew;
        layer.setStyle({{fillOpacity:show?0.8:0,opacity:show?1:0,interactive:show}});
        if(show) vis++;
      }});
      var el=document.getElementById('biz-count');
      var suffix = daysFilter ? ' (≤'+daysFilter+'d)' : '';
      if(el){{el.textContent=vis+' de '+tot+' negocios'+suffix;
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
    draggable(document.getElementById('assign-panel'),document.getElementById('assign-head'));

    console.log('✅ Bizne Map v5 loaded · HEX:{len(hex_features)} · BIZ:{len(biz_features)} · HUNTER:{len(hunter_features)} · SD:{len(sd_features)} · ACTIV:{len(activ_features)}');

    // ── Hunter View: detectar ?hunter= en la URL ─────────────────────────────
    (function() {{
      var params = new URLSearchParams(window.location.search);
      var hunterName = params.get('hunter');
      var semana     = params.get('semana');
      var dataB64    = params.get('data');
      if (!hunterName || !dataB64) return;

      // Ocultar todos los paneles del dashboard y controles
      ['kpi-dash','bmap-panel','hunter-panel','hunter-toggle',
       'assign-tool-btn','marquee-tool-btn','dr-panel',
       'guide-btn','mode-btn'].forEach(function(id) {{
        var el = document.getElementById(id);
        if (el) el.style.display = 'none';
      }});

      // Mostrar banner de hunter
      var banner = document.createElement('div');
      banner.style.cssText =
        'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:4000;'+
        'background:#0f172a;border:1px solid #f97316;border-radius:10px;'+
        'padding:10px 20px;font-family:system-ui,sans-serif;color:#e2e8f0;'+
        'box-shadow:0 4px 20px rgba(0,0,0,.7);text-align:center;min-width:260px;';
      banner.innerHTML =
        '<div style="font-size:11px;color:#f97316;font-weight:700;letter-spacing:.5px">RUTAS DE HUNTING</div>'+
        '<div style="font-size:16px;font-weight:800;color:#fff;margin:2px 0">'+hunterName+'</div>'+
        (semana ? '<div style="font-size:10px;color:#94a3b8">'+weekLabel(semana)+'</div>' : '');
      document.body.appendChild(banner);

      // Decodificar zonas — usar centroides (lat/lng ya vienen en el payload)
      try {{
        var payload = JSON.parse(decodeURIComponent(escape(atob(dataB64))));
        var zonas   = payload.zonas || [];
        if (!zonas.length) return;

        var color = _ASSIGN_COLORS[0];
        // Ordenar por rank
        var sorted = zonas.slice().sort(function(a,b){{ return a.rank - b.rank; }});
        var latlngs = sorted.map(function(z){{ return [z.lat, z.lng]; }});

        // Polyline de ruta
        L.polyline(latlngs, {{color:color,weight:3,opacity:.8,dashArray:'8 4'}}).addTo(window.THE_MAP);

        // Marcadores numerados
        sorted.forEach(function(z, i) {{
          L.marker([z.lat, z.lng], {{
            icon: L.divIcon({{
              className: '',
              html: '<div style="background:'+color+';color:#fff;border-radius:50%;width:24px;height:24px;'+
                    'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;'+
                    'border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.6)">'+(i+1)+'</div>',
              iconSize: [24,24], iconAnchor: [12,12]
            }})
          }})
          .bindPopup(
            '<b>Parada #'+(i+1)+'</b><br>'+
            'Zona: '+(z.zona||'')+'<br>'+
            'Rank: #'+z.rank+'<br>'+
            'Gap: '+(z.gap||'')+
            (z.demanda_dia ? '<br>Demanda/día: '+z.demanda_dia : '')
          )
          .bindTooltip('#'+(i+1)+' · '+(z.zona||''), {{sticky:true}})
          .addTo(window.THE_MAP);
        }});

        // Zoom al conjunto de zonas
        if (latlngs.length) {{
          window.THE_MAP.fitBounds(L.latLngBounds(latlngs), {{padding:[60,60]}});
        }}
      }} catch(e) {{
        console.error('Hunter view error:', e);
        banner.innerHTML += '<div style="color:#ef4444;font-size:10px;margin-top:4px">Error: '+e.message+'</div>';
      }}
    }})();

    }} catch(err) {{
      console.error('❌ Error en initMap:', err.message, err.stack);
    }}
  }}, 600);
}});
</script>"""

MARQUEE_JS = """<script>
// ── Marquee selector ─────────────────────────────────────────────────────────
var _mqActive   = false;
var _mqDragging = false;
var _mqStart    = null;
var _mqSelected = [];
var _mqHighlightedLayers = [];

function _mqMap() { return window.THE_MAP || null; }

function toggleMarqueeTool() {
  // No activar el marquee si el asignador de zonas está en modo selección
  if (typeof _assignMode !== 'undefined' && _assignMode && !_mqActive) return;
  var m = _mqMap();
  _mqActive = !_mqActive;
  var btn = document.getElementById('marquee-tool-btn');
  if (btn) btn.classList.toggle('active', _mqActive);
  if (_mqActive) {
    if (m) m.dragging.disable();
    document.getElementById('map').style.cursor = 'crosshair';
  } else {
    if (m) m.dragging.enable();
    document.getElementById('map').style.cursor = '';
    clearMarqueeSelection();
  }
}

function clearMarqueeSelection() {
  _mqSelected = [];
  _mqHighlightedLayers.forEach(function(l) {
    try { l.setStyle({weight:1.5, color:'#fff', fillOpacity: l._origFillOp !== undefined ? l._origFillOp : 0.85}); } catch(e){}
  });
  _mqHighlightedLayers = [];
  document.getElementById('marquee-rect').style.display = 'none';
  document.getElementById('marquee-panel').style.display = 'none';
}

// Mouse events on map container
var mapEl = null;
document.addEventListener('DOMContentLoaded', function() {
  mapEl = document.getElementById('map');
  if (!mapEl) return;

  mapEl.addEventListener('mousedown', function(e) {
    if (!_mqActive) return;
    if (e.button !== 0) return;
    _mqDragging = true;
    _mqStart = {x: e.clientX, y: e.clientY};
    var rect = document.getElementById('marquee-rect');
    rect.style.left   = e.clientX + 'px';
    rect.style.top    = e.clientY + 'px';
    rect.style.width  = '0px';
    rect.style.height = '0px';
    rect.style.display = 'block';
    document.body.style.overflow = 'hidden';
    document.body.style.userSelect = 'none';
    e.preventDefault();
    e.stopPropagation();
  });

  document.addEventListener('mousemove', function(e) {
    if (!_mqDragging || !_mqStart) return;
    e.preventDefault();
    var rect = document.getElementById('marquee-rect');
    var x1 = Math.min(e.clientX, _mqStart.x);
    var y1 = Math.min(e.clientY, _mqStart.y);
    var w  = Math.abs(e.clientX - _mqStart.x);
    var h  = Math.abs(e.clientY - _mqStart.y);
    rect.style.left   = x1 + 'px';
    rect.style.top    = y1 + 'px';
    rect.style.width  = w  + 'px';
    rect.style.height = h  + 'px';
  }, {passive: false});

  document.addEventListener('mouseup', function(e) {
    if (!_mqDragging) return;
    _mqDragging = false;
    document.body.style.overflow = '';
    document.body.style.userSelect = '';
    var rect = document.getElementById('marquee-rect');
    rect.style.display = 'none';

    if (!_mqStart) return;
    var x1 = Math.min(e.clientX, _mqStart.x);
    var y1 = Math.min(e.clientY, _mqStart.y);
    var x2 = Math.max(e.clientX, _mqStart.x);
    var y2 = Math.max(e.clientY, _mqStart.y);
    _mqStart = null;
    if ((x2-x1) < 5 || (y2-y1) < 5) return;  // ignorar clicks

    // Convertir esquinas a LatLng
    var m2 = _mqMap(); if (!m2) return;
    var mapRect = mapEl.getBoundingClientRect();
    var pt1 = m2.containerPointToLatLng([x1 - mapRect.left, y1 - mapRect.top]);
    var pt2 = m2.containerPointToLatLng([x2 - mapRect.left, y2 - mapRect.top]);
    var latMin = Math.min(pt1.lat, pt2.lat);
    var latMax = Math.max(pt1.lat, pt2.lat);
    var lngMin = Math.min(pt1.lng, pt2.lng);
    var lngMax = Math.max(pt1.lng, pt2.lng);

    // Limpiar selección anterior
    _mqHighlightedLayers.forEach(function(l) {
      try { l.setStyle({weight:1.5, color:'#fff', fillOpacity: l._origFillOp || 0.85}); } catch(e){}
    });
    _mqHighlightedLayers = [];
    _mqSelected = [];

    // Iterar sobre capas visibles y seleccionar los que caen dentro del bounds
    var activosOn  = !document.getElementById('ly_activos')  || document.getElementById('ly_activos').checked;
    var dormidasOn = !document.getElementById('ly_dormidas') || document.getElementById('ly_dormidas').checked;

    function _mqCheckLayer(lyr, defaultFillOp) {
      if (!lyr) return;
      lyr.eachLayer(function(layer) {
        var latlng = layer.getLatLng ? layer.getLatLng() : null;
        if (!latlng) return;
        if (latlng.lat >= latMin && latlng.lat <= latMax &&
            latlng.lng >= lngMin && latlng.lng <= lngMax) {
          var p = layer.feature && layer.feature.properties;
          if (p) {
            _mqSelected.push(p);
            layer._origFillOp = p.fill_opacity || defaultFillOp || 0.85;
            try { layer.setStyle({weight:3, color:'#60a5fa', fillOpacity:1.0}); } catch(ex) {}
            _mqHighlightedLayers.push(layer);
          }
        }
      });
    }

    if (activosOn)  _mqCheckLayer(window.LYR_BIZ,  0.85);
    if (dormidasOn) _mqCheckLayer(window.LYR_DORM, 0.5);

    var panel = document.getElementById('marquee-panel');
    var lbl   = document.getElementById('mp-count-lbl');
    var dlBtn = document.getElementById('mp-dl-btn');
    if (_mqSelected.length > 0) {
      var n = _mqSelected.length;
      lbl.textContent = n + ' negocio' + (n !== 1 ? 's' : '') + ' seleccionado' + (n !== 1 ? 's' : '');
      dlBtn.innerHTML = '⬇ Descargar CSV (' + n + ')';
      dlBtn.classList.remove('done','downloading');
      panel.style.display = 'flex';
    } else {
      panel.style.display = 'none';
    }
  });

  // Escape para limpiar
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      if (_mqActive) { toggleMarqueeTool(); }
      else { clearMarqueeSelection(); }
    }
  });
});

function downloadSelectionCSV() {
  if (!_mqSelected.length) return;
  var btn = document.getElementById('mp-dl-btn');
  if (btn) { btn.classList.add('downloading'); btn.innerHTML = '⏳ Preparando…'; }
  setTimeout(function() {
    var headers = [
      'service_id','nombre','phone_number','owner_name','hunter',
      'address','colonia','delegacion',
      'lat','lng',
      'tipo_negocio','horario',
      'tx_historicas','tx_90d','tx_30d','tx_pa_30d','tx_pa_hist','dormida',
      'rating','quality_score','quality_nivel',
      'tasa_acepta','tiempo_acepta',
      'etapa','service_cohort',
      'menu_bizne','menu_dia','menu_carta',
      'creation_date','dias_creacion'
    ];
    var rows = [headers.join(',')];
    _mqSelected.forEach(function(p) {
      function esc(v) {
        if (v === null || v === undefined) return '';
        var s = String(v);
        if (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\\n') >= 0) {
          return '"' + s.replace(/"/g, '""') + '"';
        }
        return s;
      }
      rows.push([
        esc(p.service_id), esc(p.nombre),   esc(p.phone_number), esc(p.owner_name),
        esc(p.hunter),     esc(p.address),  esc(p.colonia),      esc(p.delegacion),
        esc(p.lat),        esc(p.lng),
        esc(p.food_types), esc(p.horario),
        esc(p.tx_historicas), esc(p.tx_90d), esc(p.tx_30d), esc(p.tx_pa_30d), esc(p.tx_pa_hist),
        esc(p.dormida !== undefined ? (p.dormida ? 'TRUE' : 'FALSE') : ''),
        esc(p.rating),     esc(p.quality_score), esc(p.quality_nivel),
        esc(p.tasa_acepta), esc(p.tiempo_acepta),
        esc(p.etapa),      esc(p.service_cohort),
        esc(p.menu_bizne), esc(p.menu_dia), esc(p.menu_carta),
        esc(p.creation_date), esc(p.dias_creacion)
      ].join(','));
    });
    var blob = new Blob([rows.join('\\n')], {type:'text/csv;charset=utf-8;'});
    var url  = URL.createObjectURL(blob);
    var a    = document.createElement('a');
    a.href   = url;
    var fname = 'bizne_seleccion_' + new Date().toISOString().slice(0,10) + '_' + _mqSelected.length + 'neg.csv';
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    if (btn) {
      btn.classList.remove('downloading');
      btn.classList.add('done');
      btn.innerHTML = '✓ Descargado (' + _mqSelected.length + ')';
      setTimeout(function() {
        btn.classList.remove('done');
        btn.innerHTML = '⬇ Descargar CSV (' + _mqSelected.length + ')';
      }, 3000);
    }
  }, 80);
}
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
html,body{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}}
#map{{width:100%;height:100vh;background:#e8e8e8;z-index:0;}}
</style>
</head>
<body>
{KPI_HTML}
{HUNTER_HTML}
{PANEL_HTML}
{GUIDE_HTML}
{CHAT_HTML}
<!-- Assign zones tool -->
<div id="assign-mode-bar">🎯 Modo asignación activo — haz clic en zonas Hunter para seleccionarlas</div>
<button id="assign-tool-btn" title="Planear rutas de hunting" onclick="toggleAssignPanel()">🗺</button>
<div id="assign-panel">
  <div id="assign-head">
    <span>🗺 Planear Rutas de Hunting</span>
    <button onclick="toggleAssignPanel()" style="background:none;border:none;color:#fff;cursor:pointer;font-size:16px">✕</button>
  </div>
  <div id="assign-body">
    <!-- Semana actual + cargar semana guardada -->
    <div style="margin-bottom:10px;background:#1e293b;border-radius:6px;padding:7px 8px">
      <div style="font-size:9px;color:#94a3b8;font-weight:600;letter-spacing:.3px;margin-bottom:5px">SEMANA DE TRABAJO</div>
      <div style="font-size:11px;color:#f97316;font-weight:700;margin-bottom:5px" id="az-current-week"></div>
      <select id="az-week-select" onchange="if(this.value)loadAssignmentsFromStorage(this.value)"
        style="width:100%;background:#0f172a;border:1px solid #334155;color:#94a3b8;
        font-size:10px;padding:4px 6px;border-radius:4px;cursor:pointer">
        <option value="">— cargar semana guardada —</option>
      </select>
    </div>
    <div style="margin-bottom:10px">
      <div style="font-size:10px;color:#94a3b8;margin-bottom:6px;font-weight:600;letter-spacing:.3px">SELECCIONAR HUNTER</div>
      <div id="az-hunter-list" style="display:flex;flex-direction:column;gap:2px"></div>
    </div>
    <div style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div style="font-size:10px;color:#94a3b8;font-weight:600;letter-spacing:.3px">ZONAS SELECCIONADAS <span id="az-count" style="color:#f97316">0</span></div>
        <button onclick="toggleAssignMode()" id="az-mode-btn"
          style="font-size:9px;padding:3px 8px;border:1px solid #f97316;background:none;color:#f97316;border-radius:4px;cursor:pointer">
          Activar selección</button>
      </div>
      <div id="az-pending-list" style="max-height:100px;overflow-y:auto"></div>
    </div>
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button onclick="assignZonesToHunter()"
        style="flex:1;background:#7c2d12;color:#fff;border:none;padding:7px;border-radius:6px;cursor:pointer;font-size:11px;font-weight:600">
        ✅ Asignar zonas</button>
      <button onclick="clearPendingZones()"
        style="background:none;border:1px solid #334155;color:#94a3b8;padding:7px 10px;border-radius:6px;cursor:pointer;font-size:11px">
        🗑</button>
    </div>
    <div style="border-top:1px solid #1e3a52;padding-top:10px">
      <div style="font-size:10px;color:#94a3b8;font-weight:600;letter-spacing:.3px;margin-bottom:6px">
        ZONAS ASIGNADAS <span id="az-total-assigned" style="color:#22c55e">0</span></div>
      <div id="az-assigned-summary" style="font-size:10px;color:#e2e8f0;max-height:100px;overflow-y:auto"></div>
    </div>
    <div style="display:flex;gap:6px;margin-top:8px">
      <button onclick="exportAssignments()"
        style="flex:1;background:#1e3a52;color:#e2e8f0;border:none;padding:6px;border-radius:6px;cursor:pointer;font-size:10px">
        ⬇ CSV</button>
      <button onclick="clearAllAssignments()"
        style="background:none;border:1px solid #334155;color:#94a3b8;padding:6px 10px;border-radius:6px;cursor:pointer;font-size:10px">
        🗑 Todo</button>
    </div>
    <!-- Links por hunter -->
    <div style="border-top:1px solid #1e3a52;padding-top:10px;margin-top:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div style="font-size:10px;color:#94a3b8;font-weight:600;letter-spacing:.3px">🔗 LINKS POR HUNTER</div>
        <button onclick="generateHunterLinks()"
          style="font-size:9px;padding:2px 8px;background:#1e3a52;border:1px solid #334155;
          color:#94a3b8;border-radius:4px;cursor:pointer">Generar</button>
      </div>
      <div id="az-links-container" style="font-size:10px;color:#64748b">
        Asigna zonas y presiona "Generar".
      </div>
    </div>
  </div>
</div>
<!-- Marquee selector -->
<div id="marquee-rect"></div>
<button id="marquee-tool-btn" title="Selector de área — arrastra para seleccionar negocios y exportar CSV" onclick="toggleMarqueeTool()">▭</button>
<div id="marquee-panel">
  <span class="mp-count" id="mp-count-lbl">0 negocios</span>
  <button class="mp-btn" id="mp-dl-btn" onclick="downloadSelectionCSV()">⬇ Descargar CSV</button>
  <button class="mp-clear" onclick="clearMarqueeSelection()">✕ Limpiar</button>
</div>
<div id="map"></div>
<script>
var _bizneMap = L.map('map', {{center:[19.42,-99.13],zoom:11,zoomControl:true}});
window.THE_MAP = _bizneMap;
// Tile layer cargado inmediatamente — no esperar al DOMContentLoaded
window.TILE_LAYER = L.tileLayer(
  "https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png",
  {{attribution:'&copy; CartoDB &copy; OpenStreetMap', maxZoom:20}}
).addTo(_bizneMap);
</script>
{JS}
{CHAT_JS}
{MARQUEE_JS}
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
    ('refresh-btn', 'Actualizar'),
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
    ('ACTIV_DATA', 'Puntos activacion data'),
    ('activ-pulse', 'Activacion marker CSS'),
    ('LYR_ACTIV', 'Activacion layer JS'),
    ('hex_code', 'HEX-XXXX code en datos'),
    ('HEX-', 'HEX-XXXX en tooltip'),
    ('marquee-rect', 'Marquee CSS'),
    ('downloadSelectionCSV', 'Marquee CSV export JS'),
    ('toggleMarqueeTool', 'Marquee tool toggle'),
    ('chat-panel', 'Chat panel HTML'),
    ('chatProcess', 'Chat engine JS'),
    ('buildCountResponse', 'Chat count fn'),
    ('buildListResponse', 'Chat list fn'),
    ('applyFilters', 'Chat filters fn'),
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
