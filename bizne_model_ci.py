"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        BIZNE — MODELO DE DEMANDA CON DATA REAL                              ║
║        Policía Auxiliar · CDMX · Abril 2026                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

Ajustes vs. modelo sintético:
  1. Negocios filtrados a bbox CDMX (datos originales tienen outliers nacionales)
  2. Sectores PA = ancla principal de demanda (26,985 elementos en 20 ubicaciones)
  3. Transacciones = 8 días → sin STL/Prophet; análisis cross-seccional
  4. C_CAPACITY calibrado con datos reales: mediana 1.93 tx/negocio/día
  5. U_UTILIZATION = tasa_aceptacion real promedio = 0.76
  6. Supply ponderado por kitchen_quality_score (calidad efectiva)
  7. Señal de demanda no atendida: transacciones incompletas (limitada pero real)
"""

# %% [markdown]
# ## 0 · Imports & Configuración

# %%
import warnings
import os
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from scipy import stats
from scipy.stats import poisson, nbinom
from sklearn.preprocessing import MinMaxScaler
from sklearn.neighbors import KernelDensity
import hdbscan
import h3
import folium
from folium.plugins import HeatMap, MarkerCluster
from pykrige.ok import OrdinaryKriging

# ── Constantes calibradas con data real ──────────────────────────────────────
# C_CAPACITY: capacidad máxima asumida por negocio = 55 comidas/día
C_CAPACITY          = 55   # comidas por negocio por día (supuesto operativo Bizne)
CAPACITY_INACTIVE   = 30   # capacidad conservadora para negocios sin trx recientes
#   → activos pero sin historial: no los castigamos con 0 ni los sobreestimamos con 55
# U_UTILIZATION: promedio tasa_aceptacion EXCLUYENDO negocios con 0%
# (los 0% no tienen ventas activas, no representan rechazo real — distorsionan el promedio)
U_UTILIZATION  = 0.9588  # tasa_aceptacion promedio real (573/700 negocios con ventas activas)
SAFETY_BUFFER  = 1.15    # 15% headroom sobre P90
TARGET_COVERAGE= 0.90    # SLA 90% fulfillment
H3_RES         = 8       # ~531 m edge, ~0.74 km²  (~500 m solicitado por operaciones)
SECTOR_RADIUS_KM = 0.50  # radio de demanda: 500 m (acorde al hex edge de res 8)
SIGNUP_LAG_DAYS  = 1.63  # confirmado: días signup → primera transacción

# ── Supuestos de negocio (definidos por Bizne) ────────────────────────────────
TARGET_CONVERSION = 0.30          # tasa de conversión objetivo: 30% de elementos → usuarios activos
TX_PER_USER_MONTH = 6             # transacciones mensuales estimadas por usuario activo
TX_PER_USER_DAY   = TX_PER_USER_MONTH / 30  # = 0.20 tx/usuario/día

# Bounding box CDMX (filtra negocios con coords incorrectas)
LAT_MIN, LAT_MAX = 19.05, 19.75
LNG_MIN, LNG_MAX = -99.55, -98.80

SEED = 42
np.random.seed(SEED)

print("✅ Configuración cargada")
print(f"   C_CAPACITY (real)  : {C_CAPACITY:.2f} tx/negocio/día")
print(f"   U_UTILIZATION (real): {U_UTILIZATION:.0%}")
print(f"   H3 resolución       : {H3_RES}  (~{h3.average_hexagon_area(H3_RES,'km^2'):.2f} km²/hex)")


# %% [markdown]
# ## 1 · Carga y Limpieza de Datos Reales

# %%
# ── BIZNE MCP — Fuente de datos via API ───────────────────────────────────────
MCP_URL     = os.environ.get("MCP_URL", "https://mcp.bizne.com.mx/mcp")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
_DIR        = os.path.dirname(os.path.abspath(__file__))

def _query_mcp(sql, nombre, cache_file):
    """
    Ejecuta un query SQL via el MCP bizne (tool: run_sql, mode: export).
    Parsea la respuesta a DataFrame. Fallback a cache local si falla.
    Guarda cache nuevo tras cada consulta exitosa.
    """
    import requests, io as _io, json as _json
    cache_path = os.path.join(_DIR, cache_file)

    if MCP_API_KEY:
        try:
            print(f"  Consultando MCP: {nombre}...")
            # Headers requeridos por MCP Streamable HTTP transport
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {MCP_API_KEY}",
            }

            # 1 — Inicializar sesión y obtener session-id
            init_payload = {
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "bizne-ci", "version": "1.0"},
                    "capabilities": {}
                }
            }
            r = requests.post(MCP_URL, json=init_payload, headers=headers, timeout=60)
            r.raise_for_status()

            # Propagar session-id si el servidor lo devuelve
            session_id = r.headers.get("mcp-session-id", "")
            if session_id:
                headers["mcp-session-id"] = session_id

            # Notificar initialized (requerido por spec)
            notif_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {}
            }
            requests.post(MCP_URL, json=notif_payload, headers=headers, timeout=30)

            # 2 — Llamar run_sql en modo export (hasta 50k filas)
            tool_payload = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "run_sql",
                    "arguments": {"query": sql, "mode": "export"}
                }
            }
            r = requests.post(MCP_URL, json=tool_payload, headers=headers, timeout=180)
            r.raise_for_status()
            # El MCP responde UTF-8 pero sin charset en Content-Type; requests
            # asume latin-1 para text/* y rompe acentos ("Campaña" → "CampaÃ±a")
            r.encoding = "utf-8"

            # 3 — Parsear respuesta (JSON directo o SSE stream)
            content_type = r.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                # SSE: extraer líneas data:
                result = None
                for line in r.text.splitlines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload and payload != "[DONE]":
                            try:
                                result = _json.loads(payload)
                            except Exception:
                                pass
            else:
                result = r.json()

            if not result:
                raise ValueError("Respuesta vacía del MCP")

            content = result.get("result", {}).get("content", [])
            text = next((c["text"] for c in content if c.get("type") == "text"), None)
            # El MCP puede devolver {"rows":[...],"count":N} directamente
            # o el formato estándar MCP con content[].text
            if text:
                try:
                    parsed = _json.loads(text)
                    # Formato bizne MCP: {"rows": [...], "count": N, "error": "..."}
                    if isinstance(parsed, dict):
                        if parsed.get("error"):
                            raise ValueError(f"MCP error: {parsed['error']}")
                        if "rows" in parsed:
                            df = pd.DataFrame(parsed["rows"])
                        else:
                            df = pd.DataFrame([parsed])
                    elif isinstance(parsed, list):
                        df = pd.DataFrame(parsed)
                    else:
                        df = pd.read_csv(_io.StringIO(text))
                except ValueError:
                    raise
                except Exception:
                    df = pd.read_csv(_io.StringIO(text))
            else:
                raise ValueError(f"MCP no devolvió datos. Respuesta: {str(result)[:300]}")

            if len(df) == 0:
                print(f"  ⚠ MCP devolvió 0 filas para {nombre}.")
                print(f"  → Respuesta recibida (primeros 500 chars): {text[:500]!r}")
                raise ValueError("0 filas — posible error en query o response mal parseado")

            print(f"  ✅ {nombre}: {len(df):,} filas")
            df.to_csv(cache_path, index=False, encoding="utf-8")
            return df

        except Exception as e:
            print(f"  ⚠ Error MCP ({nombre}): {e}")
            print(f"  → Usando cache local...")
    else:
        print(f"  ⚠ MCP_API_KEY no configurado — usando cache local para {nombre}")

    if not os.path.exists(cache_path):
        print(f"  ❌ No hay cache local para {nombre} ({cache_file}).")
        print(f"     Configura el secret MCP_API_KEY en GitHub Actions.")
        raise SystemExit(1)
    df = pd.read_csv(cache_path)
    print(f"  ✅ {nombre} (cache): {len(df):,} filas")
    return df


def _coerce_numeric(df):
    """
    Convierte automáticamente a numérico todas las columnas que parecen números.
    El MCP devuelve todo como strings — este helper lo corrige de una vez.
    Columnas de texto conocidas se dejan como están.
    """
    _text_cols = {
        "name", "phone_number", "owner_name", "hunter", "address", "cp", "colonia",
        "delegacion", "kyc_status", "status_trx", "organization_id", "organization_name", "email", "phone",
        "food_types", "service_cohort", "etapa_negocio", "kitchen_quality_nivel",
        "last_transaction_register", "bizne_creation_date", "created_date",
        "allow_delivery", "menu_a_la_carta", "menu_bizne", "menu_premium", "menu_de_dia",
        "service_id", "sleep", "dormida", "is_active", "schedule",
    }
    for col in df.columns:
        if col in _text_cols:
            continue
        if df[col].dtype == object:
            converted = pd.to_numeric(df[col], errors="coerce")
            # Solo reemplazar si al menos 50% de valores no-nulos son numéricos
            non_null = df[col].notna().sum()
            if non_null > 0 and converted.notna().sum() / non_null >= 0.5:
                df[col] = converted
    return df


# ── SQL Queries ────────────────────────────────────────────────────────────────
SQL_NEGOCIOS = """
SELECT
    service_id, name, phone_number, owner_name, hunter, address, cp, colonia, delegacion,
    latitude, longitude, is_active, sleep,
    transacciones_historicas, transacciones_hist_real,
    transacciones_ultimos_90_dias, transacciones_ultimos_30_dias,
    ticket_promedio_ultimos_90_dias, ticket_promedio_ultimos_30_dias,
    comidas_ultimos_30_dias, ventas_ultimos_30_dias, bizne_fee_ultimos_30_dias,
    transacciones_acceptadas_ultimos_30_dias, delivery_ultimos_30_dias,
    tasa_aceptacion_ultimos_30_dias, tasa_no_aceptados_ultimos_30_dias,
    rating, tiempo_p50_aceptacion_min_ultimos_30_dias,
    dias_desde_creacion, dias_desde_ultima_transaccion,
    menu_a_la_carta, menu_bizne, menu_premium, menu_de_dia,
    score_rating, score_tiempo_aceptacion, score_no_aceptados,
    score_menu_dia, score_menu_carta, score_menu_bizne,
    ROUND((score_rating+score_tiempo_aceptacion+score_no_aceptados+score_menu_dia+score_menu_carta+score_menu_bizne)::numeric,2) AS kitchen_quality_score,
    CASE WHEN (score_rating+score_tiempo_aceptacion+score_no_aceptados+score_menu_dia+score_menu_carta+score_menu_bizne)>=85 THEN 'Excelente'
         WHEN (score_rating+score_tiempo_aceptacion+score_no_aceptados+score_menu_dia+score_menu_carta+score_menu_bizne)>=70 THEN 'Alta'
         WHEN (score_rating+score_tiempo_aceptacion+score_no_aceptados+score_menu_dia+score_menu_carta+score_menu_bizne)>=50 THEN 'Media'
         ELSE 'Baja' END AS kitchen_quality_nivel,
    CASE WHEN dias_desde_creacion<=30 AND transacciones_historicas=0 THEN 'Nuevo sin tracción'
         WHEN dias_desde_creacion<=30 AND transacciones_historicas>0 THEN 'Nuevo con tracción'
         WHEN dias_desde_creacion<=90 THEN 'En crecimiento'
         ELSE 'Maduro' END AS etapa_negocio,
    service_cohort, food_types, allow_delivery, max_delivery_distance,
    last_transaction_register, bizne_creation_date,
    schedule
FROM (
    SELECT *,
        ROUND((COALESCE(rating,0)/5.0*25)::numeric,2) AS score_rating,
        ROUND(GREATEST(0,15*(1-LEAST(COALESCE(tiempo_p50_aceptacion_min_ultimos_30_dias,15),15)/15.0))::numeric,2) AS score_tiempo_aceptacion,
        ROUND(GREATEST(0,10*(1-COALESCE(tasa_no_aceptados_ultimos_30_dias,1)))::numeric,2) AS score_no_aceptados,
        CASE WHEN menu_de_dia IS TRUE THEN 20 ELSE 0 END AS score_menu_dia,
        CASE WHEN menu_a_la_carta IS TRUE THEN 20 ELSE 0 END AS score_menu_carta,
        CASE WHEN menu_bizne IS TRUE THEN 10 ELSE 0 END AS score_menu_bizne
    FROM (
        SELECT
            s.id AS service_id, s.name, s.phone_number, s.owner_name,
            a.address,
            SUBSTRING(a.address FROM '[0-9]{5}') AS cp,
            NULLIF(UPPER(TRIM(REGEXP_REPLACE(SPLIT_PART(a.address,',',2),'\m[0-9]{5}\M','','g'))),'') AS colonia,
            NULLIF(UPPER(TRIM(REGEXP_REPLACE(SPLIT_PART(a.address,',',3),'\m[0-9]{5}\M','','g'))),'') AS delegacion,
            ST_Y(a.coordinates) AS latitude,
            ST_X(a.coordinates) AS longitude,
            s.is_active, s.allow_delivery, s.max_delivery_distance, s.sleep,
            COALESCE(s.schedule, '') AS schedule,
            ft.food_types,
            mf.menu_a_la_carta, mf.menu_bizne, mf.menu_premium, mf.menu_de_dia,
            COALESCE(th.transacciones_historicas,0) AS transacciones_historicas,
            COALESCE(th_real.transacciones_hist_real,0) AS transacciones_hist_real,
            COALESCE(t90.transacciones_ultimos_90_dias,0) AS transacciones_ultimos_90_dias,
            t90.ticket_promedio_ultimos_90_dias,
            COALESCE(sm30.transacciones_ultimos_30_dias,0) AS transacciones_ultimos_30_dias,
            COALESCE(sm30.comidas_ultimos_30_dias,0) AS comidas_ultimos_30_dias,
            sm30.ticket_promedio_ultimos_30_dias,
            COALESCE(sm30.bizne_fee_ultimos_30_dias,0) AS bizne_fee_ultimos_30_dias,
            COALESCE(sm30.ventas_ultimos_30_dias,0) AS ventas_ultimos_30_dias,
            COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias,0) AS transacciones_acceptadas_ultimos_30_dias,
            COALESCE(sm30.delivery_ultimos_30_dias,0) AS delivery_ultimos_30_dias,
            s.last_transaction_register,
            s.created_date AS bizne_creation_date,
            ROUND(sm30.tiempo_p50_aceptacion_min_ultimos_30_dias) AS tiempo_p50_aceptacion_min_ultimos_30_dias,
            GREATEST(CURRENT_DATE-s.created_date::date,0) AS dias_desde_creacion,
            CASE WHEN s.last_transaction_register IS NULL THEN NULL
                 ELSE GREATEST(CURRENT_DATE-s.last_transaction_register::date,0) END AS dias_desde_ultima_transaccion,
            ROUND(COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias/NULLIF(sm30.transacciones_ultimos_30_dias,0)::float,0)::numeric,2) AS tasa_aceptacion_ultimos_30_dias,
            ROUND((1-COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias/NULLIF(sm30.transacciones_ultimos_30_dias,0)::float,0))::numeric,2) AS tasa_no_aceptados_ultimos_30_dias,
            ROUND((s.calification_sum/NULLIF(s.calification_count::float,0))::numeric,2) AS rating,
            u.name AS hunter,
            CASE WHEN COALESCE(sm30.ventas_ultimos_30_dias,0)<1500 THEN '5 - Low Critico'
                 WHEN COALESCE(sm30.ventas_ultimos_30_dias,0)<5000 THEN '4 - Low'
                 WHEN COALESCE(sm30.ventas_ultimos_30_dias,0)<10000 THEN '3 - Growth'
                 WHEN COALESCE(sm30.ventas_ultimos_30_dias,0)<25000 THEN '2 - Core'
                 ELSE '1 - Elite' END AS service_cohort
        FROM service_service s
        JOIN administrative_division_address a ON s.address_id = a.id
        LEFT JOIN (
            SELECT ss.id, COUNT(t.id) AS transacciones_historicas
            FROM transaction_transaction t
            JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
            JOIN service_service ss ON tt.service_id = ss.id
            GROUP BY ss.id
        ) th ON th.id = s.id
        LEFT JOIN (
            -- Tx históricas excluyendo usuarios internos/test (para métrica de activación real)
            SELECT ss.id, COUNT(t.id) AS transacciones_hist_real
            FROM transaction_transaction t
            JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
            JOIN service_service ss ON tt.service_id = ss.id
            WHERE tt.user_id NOT IN (108608, 109497, 108604, 108609, 108585)
            GROUP BY ss.id
        ) th_real ON th_real.id = s.id
        LEFT JOIN (
            SELECT ss.id,
                COUNT(t.id) AS transacciones_ultimos_90_dias,
                AVG(t.amount)::float AS ticket_promedio_ultimos_90_dias
            FROM transaction_transaction t
            JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
            JOIN service_service ss ON tt.service_id = ss.id
            WHERE t.created_date >= NOW() - INTERVAL '90 days'
            GROUP BY ss.id
        ) t90 ON t90.id = s.id
        LEFT JOIN (
            SELECT ss.id,
                COUNT(t.id) AS transacciones_ultimos_30_dias,
                COALESCE(SUM(tt.count),0) AS comidas_ultimos_30_dias,
                COALESCE(SUM(t.amount),0) AS ventas_ultimos_30_dias,
                AVG(t.amount)::float AS ticket_promedio_ultimos_30_dias,
                COALESCE(SUM(t.service_fee),0) AS bizne_fee_ultimos_30_dias,
                COUNT(*) FILTER (WHERE t.hidden IS FALSE AND tt.is_active IS TRUE) AS transacciones_acceptadas_ultimos_30_dias,
                COUNT(*) FILTER (WHERE t.delivery IS TRUE) AS delivery_ultimos_30_dias,
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (tt.last_modified_date-tt.created_date))/60.0
                ) FILTER (
                    WHERE tt.last_modified_date IS NOT NULL AND tt.created_date IS NOT NULL
                      AND tt.last_modified_date >= tt.created_date
                      AND tt.is_active IS TRUE AND t.hidden IS FALSE
                ) AS tiempo_p50_aceptacion_min_ultimos_30_dias
            FROM transaction_transaction t
            JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
            JOIN service_service ss ON tt.service_id = ss.id
            WHERE t.created_date >= NOW() - INTERVAL '30 days'
            GROUP BY ss.id
        ) sm30 ON sm30.id = s.id
        LEFT JOIN (
            SELECT ss2.id AS service_id,
                COALESCE(BOOL_OR(lm.name ILIKE '%carta%' AND ss2.menu_image_status='approved'),FALSE) AS menu_a_la_carta,
                COALESCE(BOOL_OR(lm.name ILIKE '%bizne%'),FALSE) AS menu_bizne,
                COALESCE(BOOL_OR(lm.name ILIKE '%premium%'),FALSE) AS menu_premium,
                COALESCE(BOOL_OR(lm.name ILIKE '%dia%' OR lm.name ILIKE '%día%'),FALSE) AS menu_de_dia
            FROM service_service ss2
            LEFT JOIN (
                SELECT DISTINCT ON (sm.service_id, sm.name) sm.service_id, sm.name
                FROM service_internmenuservice sm
                WHERE sm.is_active IS TRUE
                ORDER BY sm.service_id, sm.name, sm.created_date DESC
            ) lm ON ss2.id = lm.service_id
            WHERE ss2.is_active IS TRUE
            GROUP BY ss2.id
        ) mf ON mf.service_id = s.id
        LEFT JOIN (
            SELECT csft.service_id,
                STRING_AGG(DISTINCT sf.name, ', ' ORDER BY sf.name) AS food_types
            FROM service_service_food_types csft
            JOIN service_foodtype sf ON sf.id = csft.foodtype_id
            GROUP BY csft.service_id
        ) ft ON ft.service_id = s.id
        LEFT JOIN user_user u ON u.id = s.hunter_id
        WHERE s.is_active IS TRUE AND a.coordinates IS NOT NULL
    ) _base
) _scored
ORDER BY kitchen_quality_score DESC, ventas_ultimos_30_dias DESC, transacciones_historicas DESC
"""

# (bloque CTE legacy eliminado — reemplazado por subqueries en SQL_NEGOCIOS)

_UNUSED = """
WITH trx_historicas AS (
    SELECT ss.id, COUNT(t.id) AS transacciones_historicas
    FROM transaction_transaction t
    JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
    JOIN service_service ss ON tt.service_id = ss.id
    GROUP BY ss.id
),
trx_90_dias AS (
    SELECT ss.id,
        COUNT(t.id) AS transacciones_ultimos_90_dias,
        AVG(t.amount)::float AS ticket_promedio_ultimos_90_dias
    FROM transaction_transaction t
    JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
    JOIN service_service ss ON tt.service_id = ss.id
    WHERE t.created_date >= NOW() - INTERVAL '90 days'
    GROUP BY ss.id
),
service_metrics_30d AS (
    SELECT ss.id,
        COUNT(t.id) AS transacciones_ultimos_30_dias,
        COALESCE(SUM(tt.count), 0) AS comidas_ultimos_30_dias,
        COALESCE(SUM(t.amount), 0) AS ventas_ultimos_30_dias,
        AVG(t.amount)::float AS ticket_promedio_ultimos_30_dias,
        COALESCE(SUM(t.service_fee), 0) AS bizne_fee_ultimos_30_dias,
        COUNT(*) FILTER (WHERE t.hidden IS FALSE AND tt.is_active IS TRUE) AS transacciones_acceptadas_ultimos_30_dias,
        COUNT(*) FILTER (WHERE t.delivery IS TRUE) AS delivery_ultimos_30_dias,
        PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (tt.last_modified_date - tt.created_date)) / 60.0
        ) FILTER (
            WHERE tt.last_modified_date IS NOT NULL AND tt.created_date IS NOT NULL
              AND tt.last_modified_date >= tt.created_date
              AND tt.is_active IS TRUE AND t.hidden IS FALSE
        ) AS tiempo_p50_aceptacion_min_ultimos_30_dias
    FROM transaction_transaction t
    JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
    JOIN service_service ss ON tt.service_id = ss.id
    WHERE t.created_date >= NOW() - INTERVAL '30 days'
    GROUP BY ss.id
),
latest_menu AS (
    SELECT DISTINCT ON (sm.service_id, sm.name)
        sm.service_id, sm.name, sm.created_date
    FROM service_internmenuservice sm
    WHERE sm.is_active IS TRUE
    ORDER BY sm.service_id, sm.name, sm.created_date DESC
),
menu_flags AS (
    SELECT ss.id AS service_id,
        COALESCE(BOOL_OR(lm.name ILIKE '%carta%' AND ss.menu_image_status = 'approved'), FALSE) AS menu_a_la_carta,
        COALESCE(BOOL_OR(lm.name ILIKE '%bizne%'), FALSE) AS menu_bizne,
        COALESCE(BOOL_OR(lm.name ILIKE '%premium%'), FALSE) AS menu_premium,
        COALESCE(BOOL_OR(lm.name ILIKE '%dia%' OR lm.name ILIKE '%día%'), FALSE) AS menu_de_dia
    FROM service_service ss
    LEFT JOIN latest_menu lm ON ss.id = lm.service_id
    WHERE ss.is_active IS TRUE
    GROUP BY ss.id
),
food_types AS (
    SELECT csft.service_id,
        STRING_AGG(DISTINCT sf.name, ', ' ORDER BY sf.name) AS food_types
    FROM service_service_food_types csft
    JOIN service_foodtype sf ON sf.id = csft.foodtype_id
    GROUP BY csft.service_id
),
base AS (
    SELECT
        s.id AS service_id, s.name, s.phone_number, s.owner_name,
        a.address,
        SUBSTRING(a.address FROM '[0-9]{5}') AS cp,
        NULLIF(UPPER(TRIM(REGEXP_REPLACE(SPLIT_PART(a.address, ',', 2), '\m[0-9]{5}\M', '', 'g'))), '') AS colonia,
        NULLIF(UPPER(TRIM(REGEXP_REPLACE(SPLIT_PART(a.address, ',', 3), '\m[0-9]{5}\M', '', 'g'))), '') AS delegacion,
        ST_Y(a.coordinates) AS latitude,
        ST_X(a.coordinates) AS longitude,
        s.is_active, s.allow_delivery, s.max_delivery_distance,
        ft.food_types,
        mf.menu_a_la_carta, mf.menu_bizne, mf.menu_premium, mf.menu_de_dia,
        COALESCE(th.transacciones_historicas, 0) AS transacciones_historicas,
        COALESCE(t90.transacciones_ultimos_90_dias, 0) AS transacciones_ultimos_90_dias,
        t90.ticket_promedio_ultimos_90_dias,
        COALESCE(sm30.transacciones_ultimos_30_dias, 0) AS transacciones_ultimos_30_dias,
        COALESCE(sm30.comidas_ultimos_30_dias, 0) AS comidas_ultimos_30_dias,
        sm30.ticket_promedio_ultimos_30_dias,
        COALESCE(sm30.bizne_fee_ultimos_30_dias, 0) AS bizne_fee_ultimos_30_dias,
        COALESCE(sm30.ventas_ultimos_30_dias, 0) AS ventas_ultimos_30_dias,
        COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias, 0) AS transacciones_acceptadas_ultimos_30_dias,
        COALESCE(sm30.delivery_ultimos_30_dias, 0) AS delivery_ultimos_30_dias,
        s.last_transaction_register,
        s.created_date AS bizne_creation_date,
        ROUND(sm30.tiempo_p50_aceptacion_min_ultimos_30_dias) AS tiempo_p50_aceptacion_min_ultimos_30_dias,
        GREATEST(CURRENT_DATE - s.created_date::date, 0) AS dias_desde_creacion,
        CASE WHEN s.last_transaction_register IS NULL THEN NULL
             ELSE GREATEST(CURRENT_DATE - s.last_transaction_register::date, 0) END AS dias_desde_ultima_transaccion,
        ROUND(COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias / NULLIF(sm30.transacciones_ultimos_30_dias, 0)::float, 0)::numeric, 2) AS tasa_aceptacion_ultimos_30_dias,
        ROUND((1 - COALESCE(sm30.transacciones_acceptadas_ultimos_30_dias / NULLIF(sm30.transacciones_ultimos_30_dias, 0)::float, 0))::numeric, 2) AS tasa_no_aceptados_ultimos_30_dias,
        ROUND((s.calification_sum / NULLIF(s.calification_count::float, 0))::numeric, 2) AS rating,
        u.name AS hunter,
        CASE WHEN COALESCE(sm30.ventas_ultimos_30_dias, 0) < 1500 THEN '5 - Low Critico'
             WHEN COALESCE(sm30.ventas_ultimos_30_dias, 0) < 5000 THEN '4 - Low'
             WHEN COALESCE(sm30.ventas_ultimos_30_dias, 0) < 10000 THEN '3 - Growth'
             WHEN COALESCE(sm30.ventas_ultimos_30_dias, 0) < 25000 THEN '2 - Core'
             ELSE '1 - Elite' END AS service_cohort,
        s.sleep
    FROM service_service s
    JOIN administrative_division_address a ON s.address_id = a.id
    LEFT JOIN trx_historicas th ON th.id = s.id
    LEFT JOIN trx_90_dias t90 ON t90.id = s.id
    LEFT JOIN service_metrics_30d sm30 ON sm30.id = s.id
    LEFT JOIN menu_flags mf ON mf.service_id = s.id
    LEFT JOIN food_types ft ON ft.service_id = s.id
    LEFT JOIN user_user u ON u.id = s.hunter_id
    WHERE s.is_active IS TRUE AND a.coordinates IS NOT NULL
),
scored AS (
    SELECT *,
        ROUND((COALESCE(rating, 0) / 5.0 * 25)::numeric, 2) AS score_rating,
        ROUND(GREATEST(0, 15 * (1 - LEAST(COALESCE(tiempo_p50_aceptacion_min_ultimos_30_dias, 15), 15) / 15.0))::numeric, 2) AS score_tiempo_aceptacion,
        ROUND(GREATEST(0, 10 * (1 - COALESCE(tasa_no_aceptados_ultimos_30_dias, 1)))::numeric, 2) AS score_no_aceptados,
        CASE WHEN menu_de_dia IS TRUE THEN 20 ELSE 0 END AS score_menu_dia,
        CASE WHEN menu_a_la_carta IS TRUE THEN 20 ELSE 0 END AS score_menu_carta,
        CASE WHEN menu_bizne IS TRUE THEN 10 ELSE 0 END AS score_menu_bizne
    FROM base
),
final AS (
    SELECT *,
        ROUND((score_rating + score_tiempo_aceptacion + score_no_aceptados + score_menu_dia + score_menu_carta + score_menu_bizne)::numeric, 2) AS kitchen_quality_score,
        CASE WHEN (score_rating + score_tiempo_aceptacion + score_no_aceptados + score_menu_dia + score_menu_carta + score_menu_bizne) >= 85 THEN 'Excelente'
             WHEN (score_rating + score_tiempo_aceptacion + score_no_aceptados + score_menu_dia + score_menu_carta + score_menu_bizne) >= 70 THEN 'Alta'
             WHEN (score_rating + score_tiempo_aceptacion + score_no_aceptados + score_menu_dia + score_menu_carta + score_menu_bizne) >= 50 THEN 'Media'
             ELSE 'Baja' END AS kitchen_quality_nivel,
        CASE WHEN dias_desde_creacion <= 30 AND transacciones_historicas = 0 THEN 'Nuevo sin tracción'
             WHEN dias_desde_creacion <= 30 AND transacciones_historicas > 0 THEN 'Nuevo con tracción'
             WHEN dias_desde_creacion <= 90 THEN 'En crecimiento'
             ELSE 'Maduro' END AS etapa_negocio
    FROM scored
)
SELECT service_id, name, phone_number, owner_name, hunter, address, cp, colonia, delegacion,
    latitude, longitude, is_active, sleep,
    transacciones_historicas, transacciones_ultimos_90_dias, transacciones_ultimos_30_dias,
    ticket_promedio_ultimos_90_dias, ticket_promedio_ultimos_30_dias,
    comidas_ultimos_30_dias, ventas_ultimos_30_dias, bizne_fee_ultimos_30_dias,
    transacciones_acceptadas_ultimos_30_dias, delivery_ultimos_30_dias,
    tasa_aceptacion_ultimos_30_dias, tasa_no_aceptados_ultimos_30_dias,
    rating, tiempo_p50_aceptacion_min_ultimos_30_dias,
    dias_desde_creacion, dias_desde_ultima_transaccion,
    menu_a_la_carta, menu_bizne, menu_premium, menu_de_dia,
    score_rating, score_tiempo_aceptacion, score_no_aceptados,
    score_menu_dia, score_menu_carta, score_menu_bizne,
    kitchen_quality_score, kitchen_quality_nivel, etapa_negocio, service_cohort,
    food_types, allow_delivery, max_delivery_distance,
    last_transaction_register, bizne_creation_date
FROM final
ORDER BY kitchen_quality_score DESC, ventas_ultimos_30_dias DESC, transacciones_historicas DESC
"""

SQL_USUARIOS = """
SELECT
    u.user_id, u.user_name, u.organization_id, u.organization_name,
    u.phone_number, u.is_active, u.is_verified, u.created_date,
    u.last_logging_datetime, u.last_request_datetime, u.type_id,
    u.longitude_last_session, u.latitude_last_session,
    u.longitude_signup, u.latitude_signup,
    ur.name AS tipo_usuario,
    ks.kyc_status, ks.kyc_last_update,
    COALESCE(t.transacciones, 0) AS transacciones,
    COALESCE(t.comidas, 0) AS comidas,
    COALESCE(t.consumo_total, 0) AS consumo_total,
    t.ticket_promedio,
    COALESCE(t.biznes_consumo, 0) AS biznes_consumo,
    COALESCE(t.membresia_transacciones, 0) AS membresia_transacciones,
    COALESCE(t.membresia_monto, 0) AS membresia_consumo,
    t.first_trx_periodo,
    hft.first_trx_ever,
    DATE_PART('day', hft.first_trx_ever - u.created_date) AS days_to_first_trx,
    ST_Y(uu.coordinates) AS latitude,
    ST_X(uu.coordinates) AS longitude
FROM (
    SELECT
        uu2.id AS user_id, uu2.name AS user_name, uu2.organization_id,
        oo.name AS organization_name, uu2.phone_number,
        uu2.is_active, uu2.is_verified, uu2.created_date,
        uu2.last_logging_datetime, uu2.last_request_datetime, uu2.type_id,
        ST_X(uu2.coordinates) AS longitude_last_session,
        ST_Y(uu2.coordinates) AS latitude_last_session,
        ST_X(uu2.signup_coordinates) AS longitude_signup,
        ST_Y(uu2.signup_coordinates) AS latitude_signup
    FROM user_user uu2
    LEFT JOIN organization_organization oo ON oo.id = uu2.organization_id
    WHERE oo.name IN ('Policia Auxiliar')
      AND (uu2.name IS NULL OR uu2.name NOT ILIKE '%test%')
      AND (uu2.email IS NULL OR uu2.email NOT ILIKE '%test%')
) u
LEFT JOIN (
    SELECT user_id, status AS kyc_status, created_date AS kyc_last_update
    FROM (
        SELECT k.user_id, k.status, k.created_date,
            ROW_NUMBER() OVER (
                PARTITION BY k.user_id
                ORDER BY CASE WHEN k.status='APPROVED' THEN 1 ELSE 0 END DESC,
                    k.created_date DESC, k.id DESC
            ) AS rn
        FROM kyc_kycsession k
    ) _kyc WHERE rn = 1
) ks ON ks.user_id = u.user_id
LEFT JOIN (
    SELECT tt.user_id, MIN(tt.date) AS first_trx_ever
    FROM transaction_transactionticket tt
    JOIN (
        SELECT uu3.id AS user_id FROM user_user uu3
        LEFT JOIN organization_organization oo2 ON oo2.id = uu3.organization_id
        WHERE oo2.name IN ('Policia Auxiliar')
          AND (uu3.name IS NULL OR uu3.name NOT ILIKE '%test%')
          AND (uu3.email IS NULL OR uu3.email NOT ILIKE '%test%')
    ) _valid ON tt.user_id = _valid.user_id
    WHERE tt.is_active IS TRUE AND tt.hidden IS FALSE AND tt.service_id <> 326
    GROUP BY tt.user_id
) hft ON hft.user_id = u.user_id
LEFT JOIN (
    SELECT _u.user_id,
        COUNT(tt.id) FILTER (WHERE tt.service_id <> 326) AS transacciones,
        COALESCE(SUM(tt.count) FILTER (WHERE tt.service_id <> 326), 0) AS comidas,
        COALESCE(SUM(tt.amount) FILTER (WHERE tt.service_id <> 326), 0) AS consumo_total,
        AVG(tt.amount) FILTER (WHERE tt.service_id <> 326) AS ticket_promedio,
        COUNT(tt.id) FILTER (WHERE tt.service_id = 326) AS membresia_transacciones,
        COALESCE(SUM(tt.amount) FILTER (WHERE tt.service_id = 326), 0) AS membresia_monto,
        COUNT(DISTINCT tt.service_id) FILTER (WHERE tt.service_id <> 326) AS biznes_consumo,
        MIN(tt.date) FILTER (WHERE tt.service_id <> 326) AS first_trx_periodo
    FROM transaction_transactionticket tt
    JOIN (
        SELECT uu4.id AS user_id FROM user_user uu4
        LEFT JOIN organization_organization oo3 ON oo3.id = uu4.organization_id
        WHERE oo3.name IN ('Policia Auxiliar')
          AND (uu4.name IS NULL OR uu4.name NOT ILIKE '%test%')
          AND (uu4.email IS NULL OR uu4.email NOT ILIKE '%test%')
    ) _u ON tt.user_id = _u.user_id
    WHERE tt.date >= NOW() - INTERVAL '30 days'
      AND tt.is_active IS TRUE AND tt.hidden IS FALSE
    GROUP BY _u.user_id
) t ON u.user_id = t.user_id
LEFT JOIN organization_userparticipationtype ur ON ur.id = u.type_id
LEFT JOIN user_user uu ON uu.id = u.user_id
ORDER BY transacciones DESC
"""

SQL_TRANSACCIONES = """
SELECT
    t.id, t.created_date,
    CASE WHEN o.name IS NULL THEN 'B2C' ELSE o.name END AS organizacion,
    t.amount,
    ST_X(tt.coordinates) AS longitude,
    ST_Y(tt.coordinates) AS latitude,
    tt.service_id, s.name,
    CASE WHEN t.hidden IS FALSE THEN 'Transacción completa'
         ELSE 'Transacción incompleta' END AS status_trx
FROM transaction_transaction t
JOIN transaction_transactionticket tt ON t.ticket_id = tt.id
JOIN user_user u ON tt.user_id = u.id
JOIN service_service s ON tt.service_id = s.id
LEFT JOIN organization_organization o ON u.organization_id = o.id
WHERE t.created_date >= NOW() - INTERVAL '30 days'
  AND o.name IN ('Policia Auxiliar')
  AND tt.service_id <> 326
"""

SQL_UPCS = """
SELECT
    ut.id,
    ut.name,
    add.address,
    ST_Y(add.coordinates)::text AS latitude,
    ST_X(add.coordinates)::text AS longitude
FROM organization_organization o
JOIN organization_organizationrole orr   ON o.id = orr.organization_id
JOIN organization_userparticipationtype ut ON ut.organization_role_id = orr.id
JOIN administrative_division_address add  ON add.id = ut.address_id
WHERE o.id = 1
  AND add.coordinates IS NOT NULL
LIMIT 500
"""

# ── 1.1 Negocios — directo desde Postgres ─────────────────────────────────────
QUALITY_PATH = None   # mantenido por compatibilidad

df_biz_raw = _query_mcp(SQL_NEGOCIOS, "Negocios", "pg_negocios_cache.csv")
df_biz_raw = _coerce_numeric(df_biz_raw)

# Forzar conversión explícita de columnas numéricas críticas (MCP devuelve todo como string)
for _col in [
    "tasa_aceptacion_ultimos_30_dias", "tasa_no_aceptados_ultimos_30_dias",
    "transacciones_historicas", "transacciones_hist_real",
    "transacciones_ultimos_90_dias", "transacciones_ultimos_30_dias",
    "ventas_ultimos_30_dias", "rating", "kitchen_quality_score",
    "tiempo_p50_aceptacion_min_ultimos_30_dias", "dias_desde_creacion",
    "dias_desde_ultima_transaccion", "latitude", "longitude",
]:
    if _col in df_biz_raw.columns:
        df_biz_raw[_col] = pd.to_numeric(df_biz_raw[_col], errors="coerce").fillna(0)

# Normalizar columna sleep/dormida (bool)
# El query devuelve "sleep" directamente desde la BD
if "sleep" in df_biz_raw.columns:
    df_biz_raw["dormida"] = df_biz_raw["sleep"].fillna(False).astype(bool)
elif "dormida" in df_biz_raw.columns:
    df_biz_raw["dormida"] = df_biz_raw["dormida"].fillna(False).astype(bool)
else:
    df_biz_raw["dormida"] = False
# Alias para compatibilidad con el resto del script
df_biz_raw["Dormidas"] = df_biz_raw["dormida"]

# Separar: activas vs dormidas
# Dormidas = True → no aparecen en la app → capacidad efectiva = 0
#            pero son oferta activable fácilmente (no eliminar del catálogo)
df_biz_raw["effective_capacity"] = df_biz_raw.apply(
    lambda r: 0.0 if r["Dormidas"]
    else (C_CAPACITY * r["tasa_aceptacion_ultimos_30_dias"]
          if r["tasa_aceptacion_ultimos_30_dias"] > 0
          else CAPACITY_INACTIVE),
    axis=1
)

# Solo negocios en CDMX
in_cdmx = (
    df_biz_raw["latitude"].between(LAT_MIN, LAT_MAX) &
    df_biz_raw["longitude"].between(LNG_MIN, LNG_MAX)
)
df_biz_all  = df_biz_raw[in_cdmx].copy()   # todos (activas + dormidas)
df_biz      = df_biz_all[~df_biz_all["Dormidas"]].copy()   # solo activas
df_biz_dorm = df_biz_all[df_biz_all["Dormidas"]].copy()    # solo dormidas

print(f"Negocios raw          : {len(df_biz_raw):,}")
print(f"Negocios en CDMX      : {len(df_biz_all):,}  ({len(df_biz_raw)-len(df_biz_all)} fuera de bbox)")
print(f"  🟢 Activas           : {len(df_biz):,}  (visibles en app, capacidad real)")
print(f"  💤 Dormidas          : {len(df_biz_dorm):,}  (ocultas en app, capacidad = 0)")
print(f"  Rating promedio (activas): {df_biz.rating.mean():.2f} ⭐")
n_activos   = (df_biz.tasa_aceptacion_ultimos_30_dias > 0).sum()
n_inactivos = (df_biz.tasa_aceptacion_ultimos_30_dias == 0).sum()
print(f"  Con trx recientes    : {n_activos} → capacidad = 55 × tasa")
print(f"  Sin trx recientes    : {n_inactivos} → capacidad = {CAPACITY_INACTIVE} comidas/día")
print(f"  Capacidad ef. total  : {df_biz.effective_capacity.sum():.0f} comidas/día")
print(f"  Oferta dormida       : {len(df_biz_dorm)} cocinas × ~{CAPACITY_INACTIVE} = "
      f"{len(df_biz_dorm)*CAPACITY_INACTIVE:.0f} comidas/día potenciales si se reactivan")

# ── 1.2 Sectores PA (ancla de demanda) ────────────────────────────────────────
df_sec = pd.read_csv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sectores_pa.csv")
)
df_sec = df_sec.rename(columns={
    "latitud": "lat", "longitud": "lng",
    "# de elementos": "elementos",
    "Indicativo": "indicativo",
    "SECTOR": "sector",
})

print(f"\nSectores PA           : {len(df_sec):,}")
print(f"  Total elementos      : {df_sec.elementos.sum():,}")
print(f"  Promedio por sector  : {df_sec.elementos.mean():.0f}")

# ── 1.2b Estaciones de Metro CDMX (señal de demanda adicional) ───────────────
# Metro = 22% de los elementos PA (~5,937 elementos)
# Tipo: 100% Custodio Fijo — permanecen en su estación asignada
# Fuente coords: conocimiento de entrenamiento (OSM / STC oficial)
METRO_SHARE    = 0.22   # fracción de elementos PA asignados a Metro
METRO_ELEMENTS = int(df_sec.elementos.sum() * METRO_SHARE * 0.85)  # ~5,040 en estaciones

df_metro = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "metro_cdmx_estaciones.csv"))

# Estimar elementos por estación:
#   Estaciones de transbordo (2+ líneas) → más elementos
#   Base: 25 por línea que pasa por la estación
station_lines = df_metro.groupby("nombre")["linea"].count().rename("n_lineas")
df_metro = df_metro.merge(station_lines, on="nombre")
df_metro["elementos_base"] = df_metro["n_lineas"] * 25

# Escalar para que el total coincida con METRO_ELEMENTS
scale = METRO_ELEMENTS / df_metro["elementos_base"].sum()
df_metro["elementos"] = (df_metro["elementos_base"] * scale).round(1)

# Filtrar SOLO líneas donde opera la Policía Auxiliar
PA_METRO_LINEAS = {"L4","L5","L6","L7","L9","LA","LB","L12"}
df_metro = df_metro[
    df_metro["linea"].isin(PA_METRO_LINEAS) &
    df_metro["lat"].between(LAT_MIN, LAT_MAX) &
    df_metro["lng"].between(LNG_MIN, LNG_MAX)
].copy()

# Re-escalar elementos al total PA Metro después del filtro de líneas
if df_metro["elementos_base"].sum() > 0:
    scale2 = METRO_ELEMENTS / df_metro["elementos_base"].sum()
    df_metro["elementos"] = (df_metro["elementos_base"] * scale2).round(1)

print(f"\nEstaciones Metro PA   : {len(df_metro):,} registros ({df_metro['nombre'].nunique()} estaciones únicas)")
print(f"  Líneas PA            : {', '.join(sorted(df_metro.linea.unique()))}")
print(f"  Líneas excluidas     : L1, L2, L3, L8 (no opera PA)")
print(f"  Elementos estimados  : {df_metro.elementos.sum():.0f} ({METRO_SHARE:.0%} de elementos PA)")
print(f"  Elementos por estación (prom): {df_metro.elementos.mean():.1f}")

# ── 1.2c Edificios Administrativos PA (demanda Custodio Fijo) ─────────────────
ADMIN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "admin_buildings.csv")
df_admin = pd.read_csv(ADMIN_PATH).rename(columns={"longuitude":"lng","latitude":"lat"})
df_admin = df_admin[
    df_admin["lat"].between(LAT_MIN, LAT_MAX) &
    df_admin["lng"].between(LNG_MIN, LNG_MAX)
].copy()

print(f"\nEdificios Admin PA    : {len(df_admin)}")
for _, r in df_admin.iterrows():
    print(f"  {r['Nombre']}: {int(r['Elementos'])} elementos  ({r['lat']:.5f}, {r['lng']:.5f})")

# ── 1.3 Usuarios — directo desde Postgres ────────────────────────────────────
ANALYTICS_PATH = None   # mantenido por compatibilidad

df_su_raw = _query_mcp(SQL_USUARIOS, "Usuarios", "pg_usuarios_cache.csv")
df_su_raw = _coerce_numeric(df_su_raw)
df_su_raw["created_date"] = pd.to_datetime(df_su_raw["created_date"], utc=True, errors="coerce").dt.tz_localize(None)

# Forzar conversión numérica explícita en columnas de usuarios
for _col in [
    "transacciones", "consumo_total", "ticket_promedio", "days_to_first_trx",
    "latitude", "longitude", "latitude_last_session", "longitude_last_session",
    "latitude_signup", "longitude_signup",
]:
    if _col in df_su_raw.columns:
        df_su_raw[_col] = pd.to_numeric(df_su_raw[_col], errors="coerce").fillna(0)

# Centroide admin para usuarios sin coordenadas (fallback)
_valid = df_su_raw.dropna(subset=["latitude","longitude"])
_valid = _valid[_valid.latitude.between(LAT_MIN, LAT_MAX) & _valid.longitude.between(LNG_MIN, LNG_MAX)]
ADMIN_LAT = _valid["latitude"].median()
ADMIN_LNG = _valid["longitude"].median()

df_su_raw["tiene_coords_reales"] = (
    df_su_raw["latitude"].notna() &
    df_su_raw["latitude"].between(LAT_MIN, LAT_MAX)
)
df_su_raw["latitude"]  = df_su_raw["latitude"].fillna(ADMIN_LAT)
df_su_raw["longitude"] = df_su_raw["longitude"].fillna(ADMIN_LNG)

# Subconjuntos por KYC
df_su = df_su_raw[df_su_raw["kyc_status"] == "APPROVED"].copy()
df_su_potential = df_su_raw[
    df_su_raw["kyc_status"].isin(["IN_PROGRESS","SUBMITTED"])
].copy()

print(f"\nUsuarios analytics    : {len(df_su_raw):,}  ← archivo actualizado con lat/lng")
print(f"  APPROVED             : {len(df_su):,}")
print(f"    Con coords reales  : {df_su['tiene_coords_reales'].sum()}")
print(f"    Sin primera compra : {(df_su.transacciones==0).sum()}  ← oportunidad activación")
print(f"    Con compras        : {(df_su.transacciones>0).sum()}")
print(f"  IN_PROGRESS/SUBMITTED: {len(df_su_potential):,}")
print(f"  Ticket promedio      : ${df_su[df_su.transacciones>0].ticket_promedio.median():.0f}")
print(f"  Penetración actual   : {len(df_su)/df_sec.elementos.sum():.2%}")

# ── 1.4 Transacciones — directo desde Postgres (últimos 30 días) ──────────────
df_tx = _query_mcp(SQL_TRANSACCIONES, "Transacciones", "pg_transacciones_cache.csv")
df_tx = _coerce_numeric(df_tx)
df_tx["created_date"] = pd.to_datetime(df_tx["created_date"], utc=True, errors="coerce").dt.tz_localize(None)

# Forzar conversión numérica explícita en columnas de transacciones
for _col in ["latitude", "longitude", "amount", "service_fee"]:
    if _col in df_tx.columns:
        df_tx[_col] = pd.to_numeric(df_tx[_col], errors="coerce").fillna(0)
df_tx = df_tx[
    df_tx["latitude"].between(LAT_MIN, LAT_MAX) &
    df_tx["longitude"].between(LNG_MIN, LNG_MAX)
].copy()

df_tx_complete   = df_tx[df_tx["status_trx"] == "Transacción completa"]
df_tx_incomplete = df_tx[df_tx["status_trx"] == "Transacción incompleta"]

days_data = (df_tx["created_date"].max() - df_tx["created_date"].min()).days

# ── 1.5 UPCs — desde Postgres ──────────────────────────────────────────────
df_upcs_raw = _query_mcp(SQL_UPCS, "UPCs", "pg_upcs_cache.csv")
df_upcs_raw = _coerce_numeric(df_upcs_raw)
# Validar coordenadas en bbox CDMX
for _c in ["latitude", "longitude"]:
    if _c in df_upcs_raw.columns:
        df_upcs_raw[_c] = pd.to_numeric(df_upcs_raw[_c], errors="coerce")
df_upcs_raw = df_upcs_raw[
    df_upcs_raw["latitude"].between(LAT_MIN, LAT_MAX) &
    df_upcs_raw["longitude"].between(LNG_MIN, LNG_MAX)
].copy()
# Guardar CSV limpio para build_map_v5.py (columnas: id, name, address, latitude, longitude)
_upc_out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(_upc_out_dir, exist_ok=True)
df_upcs_raw[["id","name","address","latitude","longitude"]].to_csv(
    os.path.join(_upc_out_dir, "upcs.csv"), index=False, encoding="utf-8"
)
print(f"✅ upcs (data/upcs.csv) actualizado desde BD: {len(df_upcs_raw):,} UPCs")

print(f"\nTransacciones         : {len(df_tx):,} en {days_data} días")
print(f"  Completas            : {len(df_tx_complete):,}")
print(f"  Incompletas          : {len(df_tx_incomplete):,}  ← demanda no atendida")
print(f"  Fecha inicio         : {df_tx.created_date.min().date()}")
print(f"  Fecha fin            : {df_tx.created_date.max().date()}")
print(f"\n⚠ Nota: {days_data} días de transacciones es insuficiente para serie temporal")
print("  → Se omite STL/Prophet; análisis cross-seccional con sectores como ancla")


# %% [markdown]
# ## 2 · H3 Grid y Asignación de Hexágonos

# %%
def to_hex(lat, lng, res=H3_RES):
    try:
        return h3.latlng_to_cell(float(lat), float(lng), res)
    except Exception:
        return None

# Asignar hex a cada dataset
df_biz["hex_id"]          = df_biz.apply(lambda r: to_hex(r.latitude, r.longitude), axis=1)
df_sec["hex_id"]          = df_sec.apply(lambda r: to_hex(r.lat, r.lng), axis=1)
df_metro["hex_id"]        = df_metro.apply(lambda r: to_hex(r.lat, r.lng), axis=1)
df_admin["hex_id"]        = df_admin.apply(lambda r: to_hex(r.lat, r.lng), axis=1)
df_su["hex_id"]           = df_su.apply(lambda r: to_hex(r.latitude, r.longitude), axis=1)
df_su_potential["hex_id"] = df_su_potential.apply(lambda r: to_hex(r.latitude, r.longitude), axis=1)
df_tx["hex_id"]           = df_tx.apply(lambda r: to_hex(r.latitude, r.longitude), axis=1)
df_tx_complete            = df_tx[df_tx["status_trx"] == "Transacción completa"].copy()
df_tx_incomplete          = df_tx[df_tx["status_trx"] == "Transacción incompleta"].copy()

# Master hex index: unión de todos los hexes con datos
all_hexes = (
    set(df_biz.hex_id.dropna()) |
    set(df_sec.hex_id.dropna()) |
    set(df_su.hex_id.dropna()) |
    set(df_tx.hex_id.dropna())
)

# ── 3 Tipos de Policía (fuente: encuesta n=41, Bizne Abril 2026) ──────────────
#
#  Tipo              % elementos  Radio      k_rings  Conversión  Comportamiento
#  ─────────────────────────────────────────────────────────────────────────────
#  Custodio Fijo       44%        250 m      k=1      38%         Compra recurrente diaria
#  Operativo en Ruta   24%        750 m      k=4      30%         Zona amplia, hora fija
#  Patrullero sin Hora 32%        1,500 m    k=7      24%         Impulsivo, ruta variable
#
# Pesos calibrados para que el promedio ponderado = 30% (TARGET_CONVERSION):
#   0.44×0.38 + 0.24×0.30 + 0.32×0.24 = 0.167+0.072+0.077 = 0.316 ≈ 30% ✓
#
# Fuente cuadrantes: CDMX = 5 zonas → 15 regiones → 75 sectores → 865 cuadrantes
# Los patrulleros cubren múltiples cuadrantes dentro de su sector (~11.5 cuadrantes/sector)
# → radio estimado de patrullaje ≈ √(sector_area) ≈ 1.5 km

TIPOS_POLICIA = {
    # nombre:   (fraccion, k_rings, conversion, dist_decay)
    # k_rings calibrados para resolución 8 (edge ~531m):
    #   fijo     → target 500m   → k=1  (~531m)
    #   ruta     → target 1.5km  → k=2  (~1,062m)
    #   patrulla → target 2.5km  → k=3  (~1,593m)
    "fijo":     (0.44, 1, 0.38, 0.50),
    "ruta":     (0.24, 2, 0.30, 0.25),
    "patrulla": (0.32, 3, 0.24, 0.12),
}

HEX_EDGE_KM = h3.average_hexagon_edge_length(H3_RES, "km")
print(f"H3 res {H3_RES}: edge={HEX_EDGE_KM*1000:.0f}m")
print(f"\nModelo de 3 tipos de policía:")
for tipo, (frac, k, conv, decay) in TIPOS_POLICIA.items():
    radio_m = k * HEX_EDGE_KM * 1000
    print(f"  {tipo:<10}: {frac:.0%} elementos | radio ≈{radio_m:.0f}m | conversión {conv:.0%}")

# Expandir demanda por tipo desde cada sector
sector_hex_demand      = {}   # total elementos ponderados por hex
sector_hex_demand_fijo = {}   # solo custodios fijos
sector_hex_demand_ruta = {}   # solo operativos en ruta
sector_hex_demand_pat  = {}   # solo patrulleros

for _, sec in df_sec.iterrows():
    if pd.isna(sec.hex_id):
        continue

    for tipo, (fraccion, k_rings, conversion, decay) in TIPOS_POLICIA.items():
        elementos_tipo = sec.elementos * fraccion

        try:
            neighbors = h3.grid_disk(sec.hex_id, k_rings)
        except Exception:
            neighbors = {sec.hex_id}

        for nhex in neighbors:
            ring = h3.grid_distance(sec.hex_id, nhex) if nhex != sec.hex_id else 0
            # Peso por distancia: decae según el tipo
            dist_weight = max(0.05, 1.0 - ring * decay)
            contrib = elementos_tipo * dist_weight

            sector_hex_demand[nhex]  = sector_hex_demand.get(nhex, 0) + contrib
            all_hexes.add(nhex)

            if tipo == "fijo":
                sector_hex_demand_fijo[nhex] = sector_hex_demand_fijo.get(nhex, 0) + contrib
            elif tipo == "ruta":
                sector_hex_demand_ruta[nhex] = sector_hex_demand_ruta.get(nhex, 0) + contrib
            else:
                sector_hex_demand_pat[nhex]  = sector_hex_demand_pat.get(nhex, 0) + contrib

# ── Expansión demanda Metro ───────────────────────────────────────────────────
# Metro policías = 100% Custodio Fijo (asignados a su estación, no patrullan)
# Mismo comportamiento: k=1 ring, conversión 38%, decay 0.50
CONV_METRO  = TIPOS_POLICIA["fijo"][2]   # 0.38 — misma conversión que fijo
DECAY_METRO = TIPOS_POLICIA["fijo"][3]   # 0.50 — decay fuerte (no se mueven)
K_METRO     = TIPOS_POLICIA["fijo"][1]   # k=1

metro_hex_demand = {}

for _, sta in df_metro.iterrows():
    if pd.isna(sta.hex_id):
        continue
    try:
        neighbors = h3.grid_disk(sta.hex_id, K_METRO)
    except Exception:
        neighbors = {sta.hex_id}

    for nhex in neighbors:
        ring = h3.grid_distance(sta.hex_id, nhex) if nhex != sta.hex_id else 0
        dist_weight = max(0.05, 1.0 - ring * DECAY_METRO)
        contrib = sta.elementos * dist_weight

        metro_hex_demand[nhex] = metro_hex_demand.get(nhex, 0) + contrib
        # También suma al sec_demand total (Metro es parte del universo PA)
        sector_hex_demand[nhex] = sector_hex_demand.get(nhex, 0) + contrib
        all_hexes.add(nhex)

print(f"Metro expandido: {len(metro_hex_demand):,} hexes con señal de Metro")

# ── Expansión demanda Edificios Administrativos ──────────────────────────────
# Custodio Fijo 100%: personal admin fijo en su sede, radio 250m (k=1)
admin_hex_demand = {}
for _, adm in df_admin.iterrows():
    if pd.isna(adm.hex_id):
        continue
    try:
        neighbors = h3.grid_disk(adm.hex_id, K_METRO)   # k=1, mismo que fijo
    except Exception:
        neighbors = {adm.hex_id}
    for nhex in neighbors:
        ring = h3.grid_distance(adm.hex_id, nhex) if nhex != adm.hex_id else 0
        dist_weight = max(0.05, 1.0 - ring * DECAY_METRO)
        contrib = adm["Elementos"] * dist_weight
        admin_hex_demand[nhex]      = admin_hex_demand.get(nhex, 0) + contrib
        sector_hex_demand[nhex]     = sector_hex_demand.get(nhex, 0) + contrib
        sector_hex_demand_fijo[nhex]= sector_hex_demand_fijo.get(nhex, 0) + contrib
        all_hexes.add(nhex)

print(f"Admin expandido : {len(admin_hex_demand):,} hexes con señal de edificios admin")

# Construir DataFrame base de hexes
hex_coords = {hx: h3.cell_to_latlng(hx) for hx in all_hexes}
df_hex = pd.DataFrame(
    [(hx, lat, lng) for hx, (lat, lng) in hex_coords.items()],
    columns=["hex_id", "lat", "lng"]
).set_index("hex_id")

# Filtrar hexes fuera de CDMX
df_hex = df_hex[
    df_hex.lat.between(LAT_MIN, LAT_MAX) &
    df_hex.lng.between(LNG_MIN, LNG_MAX)
]

print(f"\nHexes en grid CDMX    : {len(df_hex):,}")
print(f"  Con demanda sectorial: {sum(1 for h in df_hex.index if h in sector_hex_demand)}")


# %% [markdown]
# ## 3 · Construcción de Señales de Demanda

# %%
# ── Señal A: Demanda sectorial desagregada por tipo de policía ───────────────
# Peso total: 45% — ancla principal
df_hex["sec_demand"]      = pd.Series(sector_hex_demand).reindex(df_hex.index).fillna(0)
df_hex["sec_dem_fijo"]    = pd.Series(sector_hex_demand_fijo).reindex(df_hex.index).fillna(0)
df_hex["sec_dem_ruta"]    = pd.Series(sector_hex_demand_ruta).reindex(df_hex.index).fillna(0)
df_hex["sec_dem_patrulla"]= pd.Series(sector_hex_demand_pat).reindex(df_hex.index).fillna(0)
df_hex["sec_dem_metro"]   = pd.Series(metro_hex_demand).reindex(df_hex.index).fillna(0)

# ── Señal B: Signups APPROVED (demanda activa confirmada) ────────────────────
# Peso: 25% — usuarios con cuenta activa = demanda real
# Peso adicional por número de transacciones del usuario
sig_su = (df_su.groupby("hex_id")
          .agg(
              su_count       = ("user_id", "count"),
              su_tx_total    = ("transacciones", "sum"),
              su_consumo     = ("consumo_total", "sum"),
          ).reset_index())

df_hex = df_hex.join(sig_su.set_index("hex_id"), how="left").fillna(0)

# Signups potenciales (IN_PROGRESS) con peso 0.4
sig_su_pot = (df_su_potential.groupby("hex_id")
              .size().rename("su_potential_count").reset_index())
df_hex = df_hex.join(sig_su_pot.set_index("hex_id"), how="left").fillna(0)

df_hex["su_demand"] = df_hex["su_tx_total"] * 1.0 + df_hex["su_potential_count"] * 0.4

# ── Señal C: Transacciones completadas (preferencia revelada) ────────────────
# Peso: 20% — dónde realmente está comprando la gente
sig_tx = (df_tx_complete.groupby("hex_id")
          .agg(
              tx_count  = ("id", "count"),
              tx_amount = ("amount", "sum"),
          ).reset_index())
df_hex = df_hex.join(sig_tx.set_index("hex_id"), how="left").fillna(0)

# ── Señal D: Transacciones incompletas (demanda no atendida) ─────────────────
# Peso: 10% — evidencia directa de brecha (limitada: solo 9 eventos)
sig_unf = (df_tx_incomplete.groupby("hex_id")
           .size().rename("unf_count").reset_index())
df_hex = df_hex.join(sig_unf.set_index("hex_id"), how="left").fillna(0)

print("Señales construidas:")
print(f"  A. Sectorial    | hexes con datos: {(df_hex.sec_demand>0).sum()}")
print(f"  B. Signups      | hexes con datos: {(df_hex.su_demand>0).sum()}")
print(f"  C. Transacciones| hexes con datos: {(df_hex.tx_count>0).sum()}")
print(f"  D. Incompletas  | hexes con datos: {(df_hex.unf_count>0).sum()}")


# %% [markdown]
# ## 4 · KDE sobre Transacciones y Signups

# %%
# KDE sobre transacciones completadas (dónde consume la gente realmente)
if len(df_tx_complete) >= 10:
    tx_coords = df_tx_complete[["latitude","longitude"]].values
    kde_tx = KernelDensity(bandwidth="scott", kernel="gaussian")
    kde_tx.fit(tx_coords)
    log_d = kde_tx.score_samples(df_hex[["lat","lng"]].values)
    kde_tx_vals = np.exp(log_d)
    df_hex["kde_tx"] = (kde_tx_vals - kde_tx_vals.min()) / (kde_tx_vals.max() - kde_tx_vals.min() + 1e-9)
else:
    df_hex["kde_tx"] = 0.0

# KDE sobre signups APPROVED (dónde están los usuarios activos)
if len(df_su) >= 10:
    su_coords = df_su[["latitude","longitude"]].values
    kde_su = KernelDensity(bandwidth="scott", kernel="gaussian")
    kde_su.fit(su_coords)
    log_d = kde_su.score_samples(df_hex[["lat","lng"]].values)
    kde_su_vals = np.exp(log_d)
    df_hex["kde_su"] = (kde_su_vals - kde_su_vals.min()) / (kde_su_vals.max() - kde_su_vals.min() + 1e-9)
else:
    df_hex["kde_su"] = 0.0

# KDE combinado (blend 60% tx preference, 40% signup location)
df_hex["kde_combined"] = 0.60 * df_hex["kde_tx"] + 0.40 * df_hex["kde_su"]

print("KDE calculado")
print(f"  Base transacciones : {len(df_tx_complete)} puntos")
print(f"  Base signups       : {len(df_su)} puntos")


# %% [markdown]
# ## 5 · Demand Index (DI) — Ponderación Ajustada a Data Real

# %%
# Normalizar señales [0,1]
scaler = MinMaxScaler()

for raw, norm in [
    ("sec_demand",  "sec_norm"),
    ("tx_count",    "tx_norm"),
    ("unf_count",   "unf_norm"),
]:
    vals = df_hex[[raw]].values
    df_hex[norm] = scaler.fit_transform(vals).flatten()

# ── Demand Index ─────────────────────────────────────────────────────────────
# Señal de signups ELIMINADA del DI espacial:
#   Los 194 signups están geocodificados al edificio administrativo de activación
#   de la PA, no a la ubicación real de trabajo de cada policía.
#   Usarla como señal espacial crea un sesgo artificial hacia ese edificio.
#   El signup_count se mantiene para calcular penetration_rate, pero no entra al DI.
#
# Pesos redistribuidos:
#   Sectores      60% → ancla principal con 3 tipos de policía calibrados
#   Transacciones 25% → preferencia revelada (dónde realmente se compra)
#   Incompletas   15% → evidencia directa de brecha de supply
WEIGHTS = {
    "sec_norm":  0.60,
    "tx_norm":   0.25,
    "unf_norm":  0.15,
}

df_hex["DI_raw"] = sum(df_hex[col] * w for col, w in WEIGHTS.items()).clip(0, 1)

# Blend con KDE reducido a 15%:
# KDE se basa en transacciones existentes → penaliza zonas sin actividad aún
# (que son exactamente las que queremos encontrar: alta demanda latente, sin supply)
df_hex["DI"] = (0.85 * df_hex["DI_raw"] + 0.15 * df_hex["kde_combined"]).clip(0, 1)

# ── Clasificación base por DI ─────────────────────────────────────────────────
def classify_zone(di):
    if di >= 0.55: return "A_PRIORIDAD_ALTA"
    if di >= 0.35: return "B_PRIORIDAD_MEDIA"
    if di >= 0.15: return "C_VIGILANCIA"
    return "D_BAJA"

df_hex["zone_tier"] = df_hex["DI"].apply(classify_zone)

zone_counts = df_hex["zone_tier"].value_counts()
print("Distribución de zonas (pre-override, solo por DI):")
emojis = {"A_PRIORIDAD_ALTA":"🔴","B_PRIORIDAD_MEDIA":"🟠","C_VIGILANCIA":"🟡","D_BAJA":"🟢"}
for tier, cnt in sorted(zone_counts.items()):
    pct = cnt / len(df_hex) * 100
    print(f"  {emojis.get(tier,'·')} {tier:<20}: {cnt:4d} hexes ({pct:.1f}%)")


# %% [markdown]
# ## 6 · Supply Efectivo por Hexágono

# %%
# Agregar negocios activos por hex, ponderando por calidad efectiva
sig_supply = (df_biz.groupby("hex_id")
              .agg(
                  biz_count      = ("service_id", "count"),
                  effective_cap  = ("effective_capacity", "sum"),
                  avg_rating     = ("rating", "mean"),
                  tx_30d         = ("transacciones_ultimos_30_dias", "sum"),
                  avg_acceptance = ("tasa_aceptacion_ultimos_30_dias", "mean"),
              ).reset_index())

df_hex = df_hex.join(sig_supply.set_index("hex_id"), how="left").fillna(0)

# Supply en hexes vecinos también cubre demanda (radio de servicio de negocios)
# Expandimos supply con k=1 ring (negocios a ~460m también cubren el hex)
supply_expanded = {}
for hx, row in df_hex[df_hex.biz_count > 0].iterrows():
    try:
        neighbors = h3.grid_disk(hx, 1)
    except Exception:
        neighbors = {hx}
    for nhex in neighbors:
        if nhex in df_hex.index:
            supply_expanded[nhex] = supply_expanded.get(nhex, 0) + row["effective_cap"] * 0.5

df_hex["supply_reach"] = pd.Series(supply_expanded).reindex(df_hex.index).fillna(0)
df_hex["total_supply"] = df_hex["effective_cap"] + df_hex["supply_reach"]

print(f"Supply mapeado:")
print(f"  Hexes con negocios directos: {(df_hex.biz_count>0).sum()}")
print(f"  Hexes con alcance de supply : {(df_hex.total_supply>0).sum()}")
print(f"  Total tx/día cubiertos      : {df_hex.effective_cap.sum():.0f}")


# %% [markdown]
# ## 7 · Kriging — Interpolar Zonas con Data Escasa

# %%
# Usar hexes con al menos alguna señal para ajustar variograma
krige_base = df_hex[df_hex["DI_raw"] > 0].sample(
    min(500, (df_hex["DI_raw"] > 0).sum()), random_state=SEED
)

OK = OrdinaryKriging(
    krige_base["lng"].values,
    krige_base["lat"].values,
    krige_base["DI_raw"].values,
    variogram_model="spherical",
    verbose=False,
    enable_plotting=False,
)

z_pred, z_var = OK.execute("points", df_hex["lng"].values, df_hex["lat"].values)
df_hex["DI_kriged"]   = np.clip(z_pred.data, 0, 1)
df_hex["kriging_var"] = z_var.data

# Rellenar hexes sin datos directos con estimado krigeado
no_data_mask = (df_hex["sec_demand"] == 0) & (df_hex["tx_count"] == 0)
df_hex.loc[no_data_mask, "DI"] = df_hex.loc[no_data_mask, "DI_kriged"] * 0.7
# Re-clasificar por DI tras kriging (sin overrides aquí — se aplican al final)
df_hex["zone_tier"] = df_hex["DI"].apply(classify_zone)

print(f"Kriging completado")
print(f"  Hexes sin datos directos → krigeados: {no_data_mask.sum()}")
print(f"  Std dev kriging promedio             : {np.sqrt(df_hex.kriging_var).mean():.4f}")


# %% [markdown]
# ## 8 · Modelo de Capacidad P90 — Negocios Necesarios por Zona

# %%
# ── Estimación de demanda diaria por hex ─────────────────────────────────────
# Con solo 8 días de transacciones no podemos ajustar distribución por hex
# Usamos la señal de sectores para estimar demanda latente total y
# distribuirla proporcionalmente al DI por hex

# Demanda total observada: transacciones completas en 8 días → daily rate
TOTAL_TX_DAILY = len(df_tx_complete) / max(days_data, 1)

# Penetración actual: usuarios APPROVED / total elementos
PENETRATION_RATE = len(df_su) / df_sec["elementos"].sum()
MARKET_SIZE      = df_sec["elementos"].sum()

print(f"Métricas de mercado:")
print(f"  Total elementos PA    : {MARKET_SIZE:,}")
print(f"  Usuarios APPROVED     : {len(df_su):,}")
print(f"  Penetración actual    : {PENETRATION_RATE:.2%}")
print(f"  Tx/día observadas     : {TOTAL_TX_DAILY:.1f}")
print(f"  Tx potenciales/día    : {MARKET_SIZE * PENETRATION_RATE * df_su.transacciones.mean() / 30:.0f} (si todos los usuarios activos repiten patrón)")

# Demanda potencial por hex: proporcional al DI
di_sum = df_hex["DI"].sum()
df_hex["demand_share"]  = df_hex["DI"] / (di_sum + 1e-9)

# Demanda estimada por tipo de policía con conversiones diferenciadas:
#   Fijo    → conv 38%, más predecible → peso 1.0
#   Ruta    → conv 30%, zona intermedia → peso 1.0
#   Patrulla→ conv 24%, impulsivo → peso 1.0 (menor conv compensa mayor dispersión)
CONV_FIJO    = TIPOS_POLICIA["fijo"][2]     # 0.38
CONV_RUTA    = TIPOS_POLICIA["ruta"][2]     # 0.30
CONV_PATROL  = TIPOS_POLICIA["patrulla"][2] # 0.24

df_hex["est_daily_demand"] = (
    # Demanda observada (transacciones reales distribuidas por DI)
    TOTAL_TX_DAILY * df_hex["demand_share"] +
    # Custodio Fijo PA: alta conversión, recurrente
    df_hex["sec_dem_fijo"]     * CONV_FIJO   * TX_PER_USER_DAY +
    # Operativo en Ruta PA: conversión media
    df_hex["sec_dem_ruta"]     * CONV_RUTA   * TX_PER_USER_DAY +
    # Patrullero sin Hora PA: conversión menor, demanda dispersa
    df_hex["sec_dem_patrulla"] * CONV_PATROL * TX_PER_USER_DAY +
    # Metro (100% Custodio Fijo): misma conversión que fijo, anclado a estación
    df_hex["sec_dem_metro"]    * CONV_METRO  * TX_PER_USER_DAY
)

# ── SEÑAL DE DEMANDA: PUNTOS DE ACTIVACIÓN ──────────────────────────────────
# 150 elementos por punto · 30% conversión · 0.2 tx/día = 9 tx/día base
# Decay por anillo H3 (res 8 ≈ 800m/anillo): k=0→100%, k=1→65%, k=2→35%
_ACTIV_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "puntos_activacion.csv")
ACTIV_ELEMENTOS  = 150
ACTIV_CONV       = 0.30
ACTIV_TX_DAY     = 6 / 30
ACTIV_DEM_BASE   = ACTIV_ELEMENTOS * ACTIV_CONV * ACTIV_TX_DAY   # 9 tx/día
ACTIV_DECAY      = {0: 1.0, 1: 0.65, 2: 0.35}                    # ~2 km total

if os.path.exists(_ACTIV_CSV):
    df_activ = pd.read_csv(_ACTIV_CSV)
    # Acumular señal por hex
    _activ_dem = {}
    for _, _row in df_activ.iterrows():
        try:
            _ch = h3.latlng_to_cell(float(_row["Lat"]), float(_row["Long"]), H3_RES)
        except Exception:
            continue
        for _k, _decay in ACTIV_DECAY.items():
            _ring = [_ch] if _k == 0 else list(h3.grid_ring(_ch, _k))
            for _hx in _ring:
                _activ_dem[_hx] = _activ_dem.get(_hx, 0) + ACTIV_DEM_BASE * _decay
    # Aplicar al DataFrame
    df_hex["dem_activacion"] = df_hex.index.map(lambda hx: round(_activ_dem.get(hx, 0), 1))
    df_hex["est_daily_demand"] += df_hex["dem_activacion"]
    n_boost = (df_hex["dem_activacion"] > 0).sum()
    print(f"  Puntos de activacion: {len(df_activ)} puntos → {n_boost} hexes boosteados")
else:
    df_hex["dem_activacion"] = 0.0
    print("  ⚠ puntos_activacion.csv no encontrado")

# P90 usando distribución de Poisson (apropiada para datos escasos)
# Para datos con más historial usar NegBinom
df_hex["D90_daily"] = df_hex["est_daily_demand"].apply(
    lambda mu: float(poisson.ppf(0.90, max(mu, 0.01)))
)

# Negocios necesarios para atender P90
df_hex["N_needed"] = np.ceil(
    (df_hex["D90_daily"] * SAFETY_BUFFER) / (U_UTILIZATION * C_CAPACITY)
).clip(lower=0)

# Gap y cobertura
df_hex["gap"]      = (df_hex["N_needed"] - df_hex["biz_count"]).clip(lower=0)
df_hex["surplus"]  = (df_hex["biz_count"] - df_hex["N_needed"]).clip(lower=0)
df_hex["coverage"] = np.where(
    df_hex["D90_daily"] > 0,
    (df_hex["biz_count"] * U_UTILIZATION * C_CAPACITY) / df_hex["D90_daily"],
    1.0
).clip(0, 2)
df_hex["meets_sla"] = df_hex["coverage"] >= TARGET_COVERAGE

# ── OVERRIDE SEMÁNTICO FINAL ─────────────────────────────────────────────────
# Se aplica aquí porque ya tenemos total_supply, coverage y gap disponibles.
# Lógica: si hay demanda sectorial PA y no hay supply que la cubra, es urgente.
#
# Regla A — Demanda sectorial + SIN supply (ni directo ni de vecinos):
#   → A_PRIORIDAD_ALTA (brecha crítica: policías tienen demanda pero nada donde comer)
mask_sec_any       = df_hex["sec_demand"] > 0
mask_zero_supply   = df_hex["total_supply"] == 0
df_hex.loc[mask_sec_any & mask_zero_supply, "zone_tier"] = "A_PRIORIDAD_ALTA"

# Regla B — Demanda sectorial + supply insuficiente (cobertura < SLA 90%):
#   → B_PRIORIDAD_MEDIA mínimo (hay algo pero no es suficiente)
mask_low_coverage  = df_hex["coverage"] < TARGET_COVERAGE
mask_downgraded    = df_hex["zone_tier"].isin(["C_VIGILANCIA", "D_BAJA"])
df_hex.loc[mask_sec_any & ~mask_zero_supply & mask_low_coverage & mask_downgraded,
           "zone_tier"] = "B_PRIORIDAD_MEDIA"

# Informe de override
n_A = (mask_sec_any & mask_zero_supply).sum()
n_B = (mask_sec_any & ~mask_zero_supply & mask_low_coverage & mask_downgraded).sum()
print(f"\n── Override semántico final ──")
print(f"  Regla A (sec_demand>0, total_supply=0) → A: {n_A} hexes")
print(f"  Regla B (sec_demand>0, coverage<90%)   → B: {n_B} hexes")
zone_final = df_hex["zone_tier"].value_counts().sort_index()
print(f"\nDistribución FINAL de zonas:")
for tier, cnt in sorted(zone_final.items()):
    print(f"  {emojis.get(tier,'·')} {tier:<22}: {cnt:4d} hexes ({cnt/len(df_hex)*100:.1f}%)")

# Priority score para ranking
gap_norm = MinMaxScaler().fit_transform(df_hex[["gap"]]).flatten()
df_hex["priority_score"] = (
    df_hex["DI"].values * 0.40 +
    gap_norm * 0.35 +
    (1 - df_hex["coverage"].values.clip(0,1)) * 0.25
).round(4)

print(f"\nResumen de capacidad:")
print(f"  Hexes que cumplen SLA 90%  : {df_hex.meets_sla.sum():,} ({df_hex.meets_sla.mean():.1%})")
print(f"  Hexes bajo SLA (gap > 0)   : {(df_hex.gap>0).sum():,}")
print(f"  Total negocios adicionales  : {df_hex.gap.sum():.0f} (para cubrir P90)")
print(f"  Total surplus (exceso supply): {df_hex.surplus.sum():.0f}")

print(f"\nTop 10 hexes prioritarios:")
priority_hexes = df_hex[df_hex.zone_tier.isin(["A_PRIORIDAD_ALTA","B_PRIORIDAD_MEDIA"])]
top10 = priority_hexes.nlargest(10, "priority_score")[
    ["lat","lng","zone_tier","DI","est_daily_demand","D90_daily","biz_count","N_needed","gap","coverage","priority_score"]
]
print(top10.round(3).to_string())


# %% [markdown]
# ## 9 · Mapa Folium (preview rápido)

# %%
def safe(text):
    """Escapa caracteres que rompen tooltips de Folium en JavaScript."""
    return str(text).replace("'", "’").replace('"', "“").replace("`", "‘").replace("\\", "")

m = folium.Map(location=[19.42, -99.13], zoom_start=11, tiles="CartoDB positron")

tier_colors = {
    "A_PRIORIDAD_ALTA":   "#dc2626",
    "B_PRIORIDAD_MEDIA":  "#f97316",
    "C_VIGILANCIA":       "#eab308",
    "D_BAJA":             "#22c55e",
}
tier_min_opacity = {
    "A_PRIORIDAD_ALTA":  0.70,
    "B_PRIORIDAD_MEDIA": 0.55,
    "C_VIGILANCIA":      0.35,
    "D_BAJA":            0.18,
}

# Colores por línea de Metro
metro_line_colors = {
    "L1":"#e91e8c","L2":"#0047ba","L3":"#6b8f3e","L4":"#74cecd",
    "L5":"#f5c000","L6":"#e4202a","L7":"#f87400","L8":"#007a51",
    "L9":"#6a1f6e","L12":"#c0972a","LA":"#9b2b6d","LB":"#b5b5b5",
}

# ── Usuarios KYC Approved sin primera compra (cohort de activación) ───────────
# Todos los APPROVED con 0 tx — coords reales cuando existen, admin cuando no
su_sin_compra = df_su[df_su["transacciones"] == 0].copy()
su_con_compra = df_su[df_su["transacciones"] >  0].copy()

n_sin_coords = (~su_sin_compra["tiene_coords_reales"]).sum()
print(f"Usuarios APPROVED sin primera compra : {len(su_sin_compra)}")
print(f"  Con coords reales  : {su_sin_compra['tiene_coords_reales'].sum()}")
print(f"  En centroide admin : {n_sin_coords}  (aparecen agrupados en el mapa)")
print(f"Usuarios APPROVED con compras        : {len(su_con_compra)}")

# ════════════════════════════════════════
# CAPAS (FeatureGroups — toggle en mapa)
# ════════════════════════════════════════

# ── Capa 1: Zonas de Demanda — GeoJSON dinámico con tooltip configurable ──────
# Los datos se inyectan como variable JS; el tooltip se reconstruye en tiempo real
# según los campos que el usuario seleccione en el panel de control.

import json as _json

hex_features = []
for hx, row in df_hex.iterrows():
    try:
        boundary = h3.cell_to_boundary(hx)
        coords   = [[lng, lat] for lat, lng in boundary]
        coords.append(coords[0])
        color   = tier_colors.get(row["zone_tier"], "#94a3b8")
        base_op = tier_min_opacity.get(row["zone_tier"], 0.15)
        opacity = round(max(base_op, 0.15 + 0.70 * float(row["DI"])), 3)

        hex_features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {
                "hex_id":            hx,
                "zone_tier":         row["zone_tier"],
                "DI":                round(float(row["DI"]), 3),
                "demanda_dia":       round(float(row["est_daily_demand"]), 1),
                "D90":               int(row["D90_daily"]),
                "biz_actuales":      int(row["biz_count"]),
                "biz_necesarios":    int(row["N_needed"]),
                "gap":               int(row["gap"]),
                "cobertura_pct":     round(float(row["coverage"]) * 100, 1),
                "priority_score":    round(float(row["priority_score"]), 3),
                "dem_fijo":          round(float(row["sec_dem_fijo"]), 0),
                "dem_ruta":          round(float(row["sec_dem_ruta"]), 0),
                "dem_patrulla":      round(float(row["sec_dem_patrulla"]), 0),
                "dem_metro":         round(float(row["sec_dem_metro"]), 0),
                "fill_color":        color,
                "fill_opacity":      opacity,
            }
        })
    except Exception:
        pass

geojson_hexes = {"type": "FeatureCollection", "features": hex_features}
geojson_str   = _json.dumps(geojson_hexes, ensure_ascii=False)

# ── GeoJSON de negocios activos (para tooltip configurable en JS) ─────────────
biz_features = []
for _, biz in df_biz.iterrows():
    try:
        quality = float(biz.get("kitchen_quality_score", 50) or 50)
        color   = "#16a34a" if quality >= 75 else "#ca8a04" if quality >= 50 else "#dc2626"
        biz_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(biz.longitude), float(biz.latitude)]},
            "properties": {
                "nombre":       safe(str(biz["name"])),
                "rating":       round(float(biz.rating), 1),
                "calidad":      int(quality),
                "capacidad":    int(biz.effective_capacity),
                "tx_30d":       int(biz.transacciones_ultimos_30_dias),
                "tasa_acepta":  round(float(biz.tasa_aceptacion_ultimos_30_dias)*100, 1),
                "tasa_rechazo": round(float(biz.tasa_no_aceptados_ultimos_30_dias)*100, 1),
                "delegacion":   safe(str(biz.get("delegacion",""))),
                "etapa":        safe(str(biz.get("etapa_negocio",""))),
                "dias_sin_trx": int(biz.dias_desde_ultima_transaccion) if pd.notna(biz.dias_desde_ultima_transaccion) else 0,
                "menu_dia":     "✅" if biz.get("menu_de_dia") in [True,"true","TRUE",1] else "❌",
                "menu_bizne":   "✅" if biz.get("menu_bizne")  in [True,"true","TRUE",1] else "❌",
                "menu_carta":   "✅" if biz.get("menu_a_la_carta") in [True,"true","TRUE",1] else "❌",
                "cohort":       safe(str(biz.get("service_cohort",""))),
                "quality_score":int(float(biz.get("kitchen_quality_score") or 0)),
                "fill_color":   color,
            }
        })
    except Exception:
        pass

geojson_biz_str = _json.dumps({"type":"FeatureCollection","features":biz_features}, ensure_ascii=False)

# ── GeoJSON de cocinas dormidas (para buscador JS) ────────────────────────────
dorm_features = []
for _, biz in df_biz_dorm.iterrows():
    try:
        dorm_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(biz.longitude), float(biz.latitude)]},
            "properties": {
                "nombre":       safe(str(biz["name"])),
                "rating":       round(float(biz.rating), 1),
                "tx_historicas":int(biz.transacciones_historicas),
                "dias_sin_trx": int(biz.dias_desde_ultima_transaccion) if pd.notna(biz.dias_desde_ultima_transaccion) else 0,
                "delegacion":   safe(str(biz.get("delegacion",""))),
                "etapa":        safe(str(biz.get("etapa_negocio",""))),
            }
        })
    except Exception:
        pass
geojson_dorm_str = _json.dumps({"type":"FeatureCollection","features":dorm_features}, ensure_ascii=False)

# ── Bloque JS completo ────────────────────────────────────────────────────────
hex_js = f"""
<script>
// ════════════════════════════════════════════════════════════════
//  BIZNE MAP ENGINE — Hexes + Negocios + Panel configurable
// ════════════════════════════════════════════════════════════════

var HEX_DATA  = {geojson_str};
var BIZ_DATA  = {geojson_biz_str};
var DORM_DATA = {geojson_dorm_str};

// ── Campos tooltip HEXES ────────────────────────────────────────
var HEX_FIELDS = [
  {{ key:"DI",            label:"DI (Demand Index)",   on:true  }},
  {{ key:"demanda_dia",   label:"Demanda est./día",    on:true  }},
  {{ key:"D90",           label:"D90 (p90)",           on:false }},
  {{ key:"biz_actuales",  label:"Negocios actuales",   on:true  }},
  {{ key:"biz_necesarios",label:"Negocios necesarios", on:false }},
  {{ key:"gap",           label:"Gap",                 on:true  }},
  {{ key:"cobertura_pct", label:"Cobertura (%)",       on:true  }},
  {{ key:"priority_score",label:"Priority score",      on:false }},
  {{ key:"dem_fijo",      label:"Dem. Fijo (elem)",    on:false }},
  {{ key:"dem_ruta",      label:"Dem. Ruta (elem)",    on:false }},
  {{ key:"dem_patrulla",  label:"Dem. Patrulla",       on:false }},
  {{ key:"dem_metro",     label:"Dem. Metro",          on:false }},
];

// ── Campos tooltip NEGOCIOS ─────────────────────────────────────
var BIZ_FIELDS = [
  {{ key:"rating",       label:"Rating ★",         on:true  }},
  {{ key:"capacidad",    label:"Capacidad (coms/d)",on:true  }},
  {{ key:"tx_30d",       label:"Tx últimos 30d",   on:true  }},
  {{ key:"tasa_acepta",  label:"Aceptación (%)",   on:true  }},
  {{ key:"tasa_rechazo", label:"Rechazo (%)",      on:false }},
  {{ key:"menu_dia",     label:"Menú del día",     on:true  }},
  {{ key:"menu_bizne",   label:"Menú Bizne",       on:true  }},
  {{ key:"menu_carta",   label:"Menú a la carta",  on:true  }},
  {{ key:"delegacion",   label:"Delegación",       on:false }},
  {{ key:"etapa",        label:"Etapa",            on:false }},
  {{ key:"dias_sin_trx",    label:"Días sin trx",     on:false }},
  {{ key:"cohort",          label:"Cohort",            on:true  }},
  {{ key:"quality_score",   label:"Quality Score",     on:true  }},
];

function buildHexTooltip(p) {{
  var h = "<b>" + p.zone_tier + "</b>";
  HEX_FIELDS.forEach(function(f) {{
    var cb = document.getElementById("hf_"+f.key);
    if (cb && cb.checked) {{
      var v = p[f.key];
      if (f.key==="cobertura_pct") v = v+"%";
      h += "<br>"+f.label+": <b>"+v+"</b>";
    }}
  }});
  return h;
}}

function buildBizTooltip(p) {{
  var h = "<b>"+p.nombre+"</b>";
  BIZ_FIELDS.forEach(function(f) {{
    var cb = document.getElementById("bf_"+f.key);
    if (cb && cb.checked) {{
      var v = p[f.key];
      if (f.key==="tasa_acepta"||f.key==="tasa_rechazo") v = v+"%";
      h += "<br>"+f.label+": <b>"+v+"</b>";
    }}
  }});
  return h;
}}

function updateHexTooltips() {{
  if (!window.BIZNE_HEX_LAYER) return;
  window.BIZNE_HEX_LAYER.eachLayer(function(layer) {{
    if (layer._p) layer.setTooltipContent(buildHexTooltip(layer._p));
  }});
}}

function updateBizTooltips() {{
  if (!window.BIZNE_BIZ_LAYER) return;
  window.BIZNE_BIZ_LAYER.eachLayer(function(layer) {{
    if (layer._p) layer.setTooltipContent(buildBizTooltip(layer._p));
  }});
}}

// ── Buscador de negocios por nombre (activos + dormidas) ────────
function searchNegocios(query) {{
  var q = (query || '').toLowerCase().trim();
  var total = 0, visible = 0;

  function filterLayer(layer, defaultOpacity) {{
    if (!layer || !layer.eachLayer) return;
    layer.eachLayer(function(l) {{
      if (!l._p) return;
      total++;
      var nombre = (l._p.nombre || '').toLowerCase();
      var match  = q === '' || nombre.indexOf(q) !== -1;
      if (match) {{
        l.setStyle({{ opacity:1, fillOpacity:defaultOpacity }});
        l.options.interactive = true;
        visible++;
      }} else {{
        l.setStyle({{ opacity:0, fillOpacity:0 }});
        l.options.interactive = false;
      }}
    }});
  }}

  filterLayer(window.BIZNE_BIZ_LAYER,  0.75);  // activos
  filterLayer(window.BIZNE_DORM_LAYER, 0.55);  // dormidas

  var el = document.getElementById('biz-search-count');
  if (el) {{
    el.textContent = q === '' ? '' : visible + ' de ' + total + ' negocios';
    el.style.color = visible === 0 ? '#dc2626' : '#64748b';
  }}
}}

// ── Filtro de tiers ────────────────────────────────────────────
function filterTiers() {{
  if (!window.BIZNE_HEX_LAYER) return;
  window.BIZNE_HEX_LAYER.eachLayer(function(layer) {{
    if (!layer.feature) return;
    var tier = layer.feature.properties.zone_tier;
    var cb = document.getElementById("tier_"+tier);
    var show = cb ? cb.checked : true;
    layer.setStyle({{
      fillOpacity: show ? layer.feature.properties.fill_opacity : 0,
      opacity:     show ? 1 : 0,
      interactive: show,
    }});
  }});
}}

// ── Inicializar capas ───────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function() {{
  setTimeout(function() {{
    var maps = Object.values(window).filter(function(v){{
      return v && v._container && v.addLayer;
    }});
    var theMap = maps[maps.length-1];
    if (!theMap) return;

    // Pane de menor z-index para hexes (350 < overlayPane 400)
    // → Los CircleMarkers de Sectores/UPCs quedan encima y reciben el hover
    if (!theMap.getPane('hexPane')) {{
      theMap.createPane('hexPane');
      theMap.getPane('hexPane').style.zIndex = 350;
    }}

    // Capa de hexes
    window.BIZNE_HEX_LAYER = L.geoJSON(HEX_DATA, {{
      pane: 'hexPane',
      style: function(f) {{
        return {{
          color: f.properties.fill_color, weight: 0.5,
          fillColor: f.properties.fill_color,
          fillOpacity: f.properties.fill_opacity,
        }};
      }},
      onEachFeature: function(f, layer) {{
        layer._p = f.properties;
        layer.bindTooltip(buildHexTooltip(f.properties), {{sticky:true, opacity:0.96}});
      }}
    }}).addTo(theMap);

    // Capa de negocios activos (gestionada en JS para tooltip configurable)
    window.BIZNE_BIZ_LAYER = L.geoJSON(BIZ_DATA, {{
      pointToLayer: function(f, latlng) {{
        return L.circleMarker(latlng, {{
          radius: 5,
          color: f.properties.fill_color,
          weight: 1,
          fillColor: f.properties.fill_color,
          fillOpacity: 0.75,
        }});
      }},
      onEachFeature: function(f, layer) {{
        layer._p = f.properties;
        layer.bindTooltip(buildBizTooltip(f.properties), {{sticky:true, opacity:0.96}});
      }}
    }}).addTo(theMap);

    // ── Registrar capa de negocios en el LayerControl de Folium ──────────────
    // Buscamos el control de capas nativo de Leaflet y le añadimos nuestro layer
    var allControls = [];
    theMap.eachLayer(function() {{}});  // no-op para asegurar estado listo
    // Leaflet guarda controles en _controlContainer
    var ctrlEls = document.querySelectorAll('.leaflet-control-layers');
    if (ctrlEls.length > 0) {{
      // Encontrar el objeto L.Control.Layers correspondiente
      var found = null;
      Object.keys(theMap._layers || {{}}).forEach(function(k) {{
        var lyr = theMap._layers[k];
        if (lyr && lyr._map && lyr.addOverlay) found = lyr;
      }});
      // Alternativa: buscar en controles del mapa
      if (!found) {{
        (theMap._controlCorners ? Object.values(theMap._controlCorners) : [])
          .forEach(function(corner) {{
            Array.from(corner.children || []).forEach(function(el) {{
              if (el._leaflet_events && el.addOverlay) found = el;
            }});
          }});
      }}
    }}

    // Si no encontramos via DOM, usamos el truco del evento layeradd
    // Creamos el LayerControl manualmente y añadimos nuestra capa
    window.BIZNE_LAYER_CTRL = L.control.layers(null,
      {{"🍽 Negocios Activos (tooltip config)": window.BIZNE_BIZ_LAYER}},
      {{position: "topright", collapsed: false}}
    );
    // No añadimos este control extra — en su lugar inyectamos en el control existente
    var existingCtrl = null;
    theMap.eachLayer(function() {{}});
    // Buscar control de capas de Folium directamente en el mapa
    if (theMap._controls) {{
      theMap._controls.forEach(function(c) {{
        if (c && c.addOverlay) existingCtrl = c;
      }});
    }}
    // Capa JS de dormidas — creada antes de registrar en el LayerControl
    window.BIZNE_DORM_LAYER = L.geoJSON(DORM_DATA, {{
      pointToLayer: function(f, latlng) {{
        return L.circleMarker(latlng, {{
          radius: 5, color:"#6b7280", weight:1.5,
          fillColor:"#9ca3af", fillOpacity:0.55,
          dashArray:"4",
        }});
      }},
      onEachFeature: function(f, layer) {{
        layer._p = f.properties;
        var p = f.properties;
        // Tooltip explícito con dirección y offset para asegurar visibilidad
        layer.bindTooltip(
          "<b>Dormida: " + p.nombre + "</b><br>" +
          "Rating: " + p.rating +
          " &nbsp;|&nbsp; Tx hist: " + p.tx_historicas + "<br>" +
          "Dias sin trx: " + p.dias_sin_trx + "<br>" +
          "<i style='color:#9ca3af'>Capacidad al reactivar: ~30 com/dia</i>",
          {{sticky:false, opacity:0.97, direction:"top", offset:[0,-6]}}
        );
        // Abrir tooltip también al pasar el mouse
        layer.on("mouseover", function() {{ this.openTooltip(); }});
        layer.on("mouseout",  function() {{ this.closeTooltip(); }});
      }}
    }}).addTo(theMap);

    // Registrar ambas capas JS en el LayerControl de Folium
    if (existingCtrl) {{
      existingCtrl.addOverlay(window.BIZNE_BIZ_LAYER,  "🍽 Negocios Activos");
      existingCtrl.addOverlay(window.BIZNE_DORM_LAYER, "💤 Cocinas Dormidas");
    }} else {{
      // Fallback: control separado con ambas capas
      L.control.layers(null, {{
        "🍽 Negocios Activos":  window.BIZNE_BIZ_LAYER,
        "💤 Cocinas Dormidas": window.BIZNE_DORM_LAYER
      }}, {{position:"topright", collapsed:false}}).addTo(theMap);
    }}

    window.BIZNE_MAP = theMap;
  }}, 600);
}});
</script>
"""

# ── Panel de control — draggable, con filtro de tiers + tooltip selector ──────
panel_js = """
<style>
#bmap-panel {
  position:fixed;top:80px;right:330px;z-index:1001;
  background:#fff;border-radius:10px;
  box-shadow:0 3px 14px rgba(0,0,0,0.18);
  font-family:system-ui,sans-serif;font-size:11px;
  min-width:210px;max-height:82vh;
  display:flex;flex-direction:column;
  user-select:none;
}
#bmap-panel-header {
  background:#1e293b;color:#fff;padding:9px 12px;
  border-radius:10px 10px 0 0;cursor:move;
  display:flex;justify-content:space-between;align-items:center;
  font-size:12px;font-weight:600;
}
#bmap-panel-body {
  padding:10px 13px;overflow-y:auto;flex:1;
}
.bmap-section { margin-bottom:10px; }
.bmap-section-title {
  font-size:10px;font-weight:700;letter-spacing:.5px;
  color:#64748b;text-transform:uppercase;margin-bottom:5px;
}
.bmap-check {
  display:block;padding:2px 0;cursor:pointer;
  display:flex;align-items:center;gap:5px;
}
.bmap-check input { margin:0; }
.bmap-tier { display:flex;align-items:center;gap:5px;padding:2px 0;cursor:pointer; }
.bmap-dot { width:11px;height:11px;border-radius:2px;flex-shrink:0; }
.bmap-btn-row { display:flex;gap:5px;margin-top:6px; }
.bmap-btn {
  flex:1;font-size:10px;padding:3px 0;cursor:pointer;
  border:1px solid #e2e8f0;border-radius:4px;background:#f8fafc;
}
.bmap-btn:hover { background:#e2e8f0; }
hr.bmap-hr { border:none;border-top:1px solid #f1f5f9;margin:8px 0; }
</style>

<div id="bmap-panel">
  <div id="bmap-panel-header">
    <span>⚙️ Configuración del mapa</span>
    <button onclick="document.getElementById('bmap-panel').style.display='none';
                     document.getElementById('bmap-toggle').style.display='block'"
      style="border:none;background:none;cursor:pointer;color:#fff;font-size:15px;line-height:1">✕</button>
  </div>
  <div id="bmap-panel-body">

    <!-- BUSCADOR DE NEGOCIOS -->
    <div class="bmap-section">
      <div class="bmap-section-title">🔍 Buscar negocio</div>
      <input id="biz-search" type="text" placeholder="Nombre del negocio..."
        oninput="searchNegocios(this.value)"
        style="width:100%;padding:5px 8px;border:1px solid #e2e8f0;border-radius:5px;
               font-size:11px;font-family:system-ui,sans-serif;box-sizing:border-box;
               outline:none;color:#1e293b;background:#f8fafc">
      <div id="biz-search-count" style="font-size:9px;color:#94a3b8;margin-top:4px;text-align:right"></div>
      <button onclick="searchNegocios('');document.getElementById('biz-search').value=''"
        style="margin-top:5px;width:100%;font-size:10px;padding:3px;cursor:pointer;
               border:1px solid #e2e8f0;border-radius:4px;background:#f8fafc;color:#64748b">
        Mostrar todos</button>
    </div>

    <hr class="bmap-hr">

    <!-- FILTRO DE TIERS -->
    <div class="bmap-section">
      <div class="bmap-section-title">🗺 Filtro de prioridad (hexes)</div>
      <label class="bmap-tier">
        <input type="checkbox" id="tier_A_PRIORIDAD_ALTA" checked onchange="filterTiers()">
        <span class="bmap-dot" style="background:#dc2626"></span> A — Prioridad Alta
      </label>
      <label class="bmap-tier">
        <input type="checkbox" id="tier_B_PRIORIDAD_MEDIA" checked onchange="filterTiers()">
        <span class="bmap-dot" style="background:#f97316"></span> B — Prioridad Media
      </label>
      <label class="bmap-tier">
        <input type="checkbox" id="tier_C_VIGILANCIA" checked onchange="filterTiers()">
        <span class="bmap-dot" style="background:#eab308"></span> C — Vigilancia
      </label>
      <label class="bmap-tier">
        <input type="checkbox" id="tier_D_BAJA" checked onchange="filterTiers()">
        <span class="bmap-dot" style="background:#22c55e"></span> D — Baja
      </label>
    </div>

    <hr class="bmap-hr">

    <!-- TOOLTIP HEXES -->
    <div class="bmap-section">
      <div class="bmap-section-title">📋 Tooltip — Hexágonos</div>
"""

for key, label, on in [
    ("DI",            "DI (Demand Index)",    True),
    ("demanda_dia",   "Demanda est./día",     True),
    ("D90",           "D90 (p90)",            False),
    ("biz_actuales",  "Negocios actuales",    True),
    ("biz_necesarios","Negocios necesarios",  False),
    ("gap",           "Gap",                  True),
    ("cobertura_pct", "Cobertura (%)",        True),
    ("priority_score","Priority score",       False),
    ("dem_fijo",      "Dem. Fijo",            False),
    ("dem_ruta",      "Dem. Ruta",            False),
    ("dem_patrulla",  "Dem. Patrulla",        False),
    ("dem_metro",     "Dem. Metro",           False),
]:
    chk = "checked" if on else ""
    panel_js += (
        f'      <label class="bmap-check">'
        f'<input type="checkbox" id="hf_{key}" {chk} onchange="updateHexTooltips()"> '
        f'{label}</label>\n'
    )

panel_js += """
      <div class="bmap-btn-row">
        <button class="bmap-btn" onclick="document.querySelectorAll('[id^=hf_]').forEach(c=>c.checked=true);updateHexTooltips()">Todos</button>
        <button class="bmap-btn" onclick="document.querySelectorAll('[id^=hf_]').forEach(c=>c.checked=false);updateHexTooltips()">Ninguno</button>
      </div>
    </div>

    <hr class="bmap-hr">

    <!-- TOOLTIP NEGOCIOS -->
    <div class="bmap-section">
      <div class="bmap-section-title">🍽 Tooltip — Negocios</div>
"""

for key, label, on in [
    ("rating",       "Rating ★",          True),
    ("capacidad",    "Capacidad (com/d)",  True),
    ("tx_30d",       "Tx últimos 30d",     True),
    ("tasa_acepta",  "Aceptación (%)",     True),
    ("tasa_rechazo", "Rechazo (%)",        False),
    ("menu_dia",     "Menú del día",       True),
    ("menu_bizne",   "Menú Bizne",         True),
    ("menu_carta",   "Menú a la carta",    True),
    ("delegacion",   "Delegación",         False),
    ("etapa",        "Etapa negocio",      False),
    ("dias_sin_trx", "Días sin trx",       False),
    ("cohort",        "Cohort",             True),
    ("quality_score", "Quality Score",      True),
]:
    chk = "checked" if on else ""
    panel_js += (
        f'      <label class="bmap-check">'
        f'<input type="checkbox" id="bf_{key}" {chk} onchange="updateBizTooltips()"> '
        f'{label}</label>\n'
    )

panel_js += """
      <div class="bmap-btn-row">
        <button class="bmap-btn" onclick="document.querySelectorAll('[id^=bf_]').forEach(c=>c.checked=true);updateBizTooltips()">Todos</button>
        <button class="bmap-btn" onclick="document.querySelectorAll('[id^=bf_]').forEach(c=>c.checked=false);updateBizTooltips()">Ninguno</button>
      </div>
    </div>

  </div><!-- /body -->
</div><!-- /panel -->

<!-- Botón para reabrir -->
<button id="bmap-toggle" onclick="this.style.display='none';document.getElementById('bmap-panel').style.display='flex'"
  style="display:none;position:fixed;top:80px;right:330px;z-index:1002;
  background:#1e293b;color:#fff;border:none;border-radius:8px;
  padding:7px 12px;font-size:11px;cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,0.2)">⚙️ Config</button>

<script>
// ── Drag & Drop del panel ──────────────────────────────────────
(function() {
  var panel = document.getElementById('bmap-panel');
  var header = document.getElementById('bmap-panel-header');
  var dragging = false, ox = 0, oy = 0;

  header.addEventListener('mousedown', function(e) {
    dragging = true;
    var r = panel.getBoundingClientRect();
    ox = e.clientX - r.left;
    oy = e.clientY - r.top;
    panel.style.right = 'auto';
    e.preventDefault();
  });

  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    panel.style.left = (e.clientX - ox) + 'px';
    panel.style.top  = (e.clientY - oy) + 'px';
  });

  document.addEventListener('mouseup', function() { dragging = false; });
})();
</script>
"""

m.get_root().html.add_child(folium.Element(hex_js))
m.get_root().html.add_child(folium.Element(panel_js))

# ── Capa 2: Sectores PA ───────────────────────────────────────────────────────
fg_sectores = folium.FeatureGroup(name="🏢 Sectores PA", show=True)
for _, sec in df_sec.iterrows():
    folium.CircleMarker(
        location=[sec.lat, sec.lng], radius=10,
        color="#7c3aed", fill=True, fill_opacity=0.85, weight=2,
        tooltip=(
            f"<b>{safe(sec.indicativo)} — {safe(sec.sector)}</b><br>"
            f"{int(sec.elementos):,} elementos<br>"
            f"Demanda diaria est.: {sec.elementos * TARGET_CONVERSION * TX_PER_USER_DAY:.0f} tx/día"
        ),
    ).add_to(fg_sectores)
fg_sectores.add_to(m)

# ── Capa 2b: Edificios Administrativos PA ────────────────────────────────────
fg_admin = folium.FeatureGroup(name=f"🏛 Edificios Admin PA ({len(df_admin)})", show=True)
for _, adm in df_admin.iterrows():
    folium.Marker(
        location=[adm.lat, adm.lng],
        tooltip=(
            f"<b>{safe(str(adm['Nombre']))}</b><br>"
            f"{int(adm['Elementos'])} elementos<br>"
            f"Demanda diaria est.: {int(adm['Elementos'] * TARGET_CONVERSION * TX_PER_USER_DAY)} tx/día<br>"
            f"Tipo: Custodio Fijo (100%)"
        ),
        icon=folium.Icon(color="darkblue", icon="building", prefix="fa"),
    ).add_to(fg_admin)
fg_admin.add_to(m)

# ── Capa UPCs (Policía CDMX — solo visualización, no afecta modelo) ──────────
# ⚠ El CSV tiene lat/lng invertidos: columna 'latitude' tiene valores ~-99 (lng)
#   y columna 'longitude' tiene valores ~19 (lat). Se corrigen al cargar.
UPC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "upcs.csv")
df_upc = pd.read_csv(UPC_PATH).rename(columns={"latitude":"lng_raw","longitude":"lat_raw"})
df_upc = df_upc[
    df_upc["lat_raw"].between(LAT_MIN, LAT_MAX) &
    df_upc["lng_raw"].between(LNG_MIN, LNG_MAX)
].copy()

fg_upc = folium.FeatureGroup(name=f"🚔 UPCs Policía CDMX ({len(df_upc)})", show=False)
for _, u in df_upc.iterrows():
    folium.CircleMarker(
        location=[float(u.lat_raw), float(u.lng_raw)],
        radius=6,
        color="#1D4ED8", fill=True, fill_color="#93C5FD",
        fill_opacity=0.75, weight=1.5,
        tooltip=f"<b>{safe(str(u['name']))}</b><br>{safe(str(u.get('address',''))[:60])}",
    ).add_to(fg_upc)
fg_upc.add_to(m)
print(f"UPCs cargados: {len(df_upc)} puntos (otra organización — solo visualización)")

# ── Capa 3: Estaciones Metro ──────────────────────────────────────────────────
fg_metro = folium.FeatureGroup(name="🚇 Estaciones Metro", show=True)
for _, sta in df_metro.iterrows():
    lcolor = metro_line_colors.get(sta.linea, "#888888")
    folium.CircleMarker(
        location=[sta.lat, sta.lng], radius=5,
        color=lcolor, fill=True, fill_opacity=0.85, weight=1.5,
        tooltip=(
            f"<b>{safe(sta.nombre)}</b> [{sta.linea}]<br>"
            f"~{sta.elementos:.0f} elementos PA<br>"
            f"Líneas de transbordo: {int(sta.n_lineas)}"
        ),
    ).add_to(fg_metro)
fg_metro.add_to(m)

# ── Capa 4: Negocios Activos — gestionada por JS (tooltip configurable) ───────
# La capa se crea en JavaScript usando BIZ_DATA GeoJSON inyectado arriba.
# No se crea un FeatureGroup Folium para evitar duplicar marcadores.
# El toggle de visibilidad se maneja desde el panel de configuración.

# ── Capa 4b: Cocinas Dormidas — gestionada por JS (buscador + tooltip) ────────
# La capa se crea en JavaScript usando DORM_DATA GeoJSON inyectado arriba.
# Esto permite que el buscador filtre activos y dormidas de forma unificada.

# ── Capa 5: Usuarios APPROVED con compras ────────────────────────────────────
fg_usuarios = folium.FeatureGroup(name="👤 Usuarios APPROVED (con compras)", show=False)
for _, su in su_con_compra.iterrows():
    folium.CircleMarker(
        location=[su.latitude, su.longitude], radius=4,
        color="#2563eb", fill=True, fill_opacity=0.65, weight=0,
        tooltip=f"APPROVED · {int(su.transacciones)} compras · ticket ${su.ticket_promedio:.0f}",
    ).add_to(fg_usuarios)
fg_usuarios.add_to(m)

# ── Capa 6: KYC Approved sin primera compra (oportunidad de activación) ───────
fg_activar = folium.FeatureGroup(
    name=f"🔔 KYC Aprobado — sin primera compra ({len(su_sin_compra)} usuarios)",
    show=True
)
for _, su in su_sin_compra.iterrows():
    dias = (pd.Timestamp.now() - pd.to_datetime(su.created_date, dayfirst=True)).days
    tiene_coords = su.get("tiene_coords_reales", False)
    # Diferenciar visualmente: coords reales = círculo normal, centroide = más pequeño y transparente
    radio   = 7 if tiene_coords else 5
    opacity = 0.85 if tiene_coords else 0.50
    nota    = "" if tiene_coords else "<br><i style='color:#999'>📍 Ubicación: edificio admin</i>"
    folium.CircleMarker(
        location=[su.latitude, su.longitude],
        radius=radio,
        color="#f97316", fill=True, fill_opacity=opacity, weight=2,
        tooltip=(
            f"<b>Sin primera compra</b>{nota}<br>"
            f"KYC aprobado hace {dias} días<br>"
            f"Organización: {safe(str(su.organization_name))}"
        ),
    ).add_to(fg_activar)
fg_activar.add_to(m)

# ── Capa 7: Zonas con usuarios sin conversión (correlación supply vs conversión) ──
# Objetivo: ¿las zonas donde hay usuarios KYC sin compra son las mismas
#            que tienen brecha de supply? Si sí → el problema es oferta, no producto.
#
# Construcción:
#   1. Asignar hex a cada usuario sin compra (con coords reales)
#   2. Contar usuarios sin compra por hex
#   3. Cruzar con gap y coverage del modelo
#   4. Pintar hexes con borde azul eléctrico — opacidad proporcional al # usuarios

su_sin_compra_geo = su_sin_compra[su_sin_compra["tiene_coords_reales"] == True].copy()
su_sin_compra_geo["hex_id"] = su_sin_compra_geo.apply(
    lambda r: to_hex(r.latitude, r.longitude), axis=1
)
su_sin_compra_geo = su_sin_compra_geo.dropna(subset=["hex_id"])

# Agregar por hex
hex_sin_conv = (su_sin_compra_geo.groupby("hex_id")
                .agg(
                    usuarios_sin_compra = ("user_id", "count"),
                    dias_kyc_prom       = ("days_to_first_trx", lambda x:
                        (pd.Timestamp.now() - su_sin_compra_geo.loc[x.index, "created_date"]).dt.days.mean()
                    ),
                ).reset_index())

# Cruzar con datos del modelo (gap, coverage, supply)
hex_sin_conv = hex_sin_conv.merge(
    df_hex[["lat","lng","gap","coverage","total_supply","zone_tier","biz_count"]].reset_index(),
    on="hex_id", how="left"
)

# Categorizar correlación
def correlacion_supply(row):
    if row["total_supply"] == 0 and row["usuarios_sin_compra"] > 0:
        return "sin_supply"      # sin oferta → hipótesis: supply es el problema
    elif row["coverage"] < 0.90 and row["usuarios_sin_compra"] > 0:
        return "supply_insuf"    # oferta insuficiente
    else:
        return "supply_ok"       # hay supply → problema puede ser otro

hex_sin_conv["correlacion"] = hex_sin_conv.apply(correlacion_supply, axis=1)

n_sin_supply  = (hex_sin_conv.correlacion == "sin_supply").sum()
n_insuf       = (hex_sin_conv.correlacion == "supply_insuf").sum()
n_supply_ok   = (hex_sin_conv.correlacion == "supply_ok").sum()
total_usc     = hex_sin_conv.usuarios_sin_compra.sum()

print(f"\nCorrelación supply vs no-conversión ({len(hex_sin_conv)} hexes con usuarios sin compra):")
print(f"  🔴 Sin supply cerca      : {n_sin_supply} hexes → {hex_sin_conv[hex_sin_conv.correlacion=='sin_supply'].usuarios_sin_compra.sum()} usuarios")
print(f"  🟠 Supply insuficiente   : {n_insuf} hexes → {hex_sin_conv[hex_sin_conv.correlacion=='supply_insuf'].usuarios_sin_compra.sum()} usuarios")
print(f"  🟢 Supply OK (otro factor): {n_supply_ok} hexes → {hex_sin_conv[hex_sin_conv.correlacion=='supply_ok'].usuarios_sin_compra.sum()} usuarios")

corr_colors = {
    "sin_supply":   "#7c3aed",   # morado — sin oferta, claramente supply gap
    "supply_insuf": "#0ea5e9",   # azul — oferta insuficiente
    "supply_ok":    "#10b981",   # verde — supply existe, problema es otro
}

fg_noconv = folium.FeatureGroup(
    name=f"📊 Zonas sin conversión ({len(hex_sin_conv)} hexes, {int(total_usc)} usuarios)",
    show=True
)

max_usc = max(hex_sin_conv.usuarios_sin_compra.max(), 1)
for _, row in hex_sin_conv.iterrows():
    try:
        boundary = h3.cell_to_boundary(row.hex_id)
    except Exception:
        continue
    color   = corr_colors.get(row.correlacion, "#888")
    opacity = 0.25 + 0.55 * (row.usuarios_sin_compra / max_usc)

    sup_txt = (
        "Sin supply cercano" if row.correlacion == "sin_supply"
        else f"Cobertura {row.coverage:.0%}" if row.correlacion == "supply_insuf"
        else f"Supply OK ({int(row.biz_count) if pd.notna(row.biz_count) else 0} negocios)"
    )
    folium.Polygon(
        locations=boundary,
        color=color, weight=2.5,
        fill=True, fill_color=color, fill_opacity=opacity,
        tooltip=(
            f"<b>Zona sin conversión</b><br>"
            f"Usuarios KYC sin compra: <b>{int(row.usuarios_sin_compra)}</b><br>"
            f"Supply: {sup_txt}<br>"
            f"Gap de negocios: {int(row.gap) if pd.notna(row.gap) else 'N/A'}<br>"
            f"<i>{'⚠ Hipótesis: falta oferta' if row.correlacion != 'supply_ok' else '→ Investigar otro factor'}</i>"
        ),
    ).add_to(fg_noconv)

fg_noconv.add_to(m)

# ── Capa 7: Heatmap — Transacciones Completadas ───────────────────────────────
fg_tx_heat = folium.FeatureGroup(name=f"🟢 Heatmap transacciones completadas ({len(df_tx_complete)})", show=False)
if len(df_tx_complete) > 0:
    heat_tx = df_tx_complete[["latitude","longitude"]].values.tolist()
    HeatMap(heat_tx, radius=18, blur=14,
            gradient={0.3:"#065F46", 0.6:"#10B981", 0.85:"#6EE7B7", 1.0:"#FFFFFF"}
    ).add_to(fg_tx_heat)
fg_tx_heat.add_to(m)

# ── Capa: Heatmap — Signups (todos los usuarios con coords válidas) ────────────
# Incluye todos los KYC status con lat/lng válida — refleja dónde se están registrando
signups_heat = df_su_raw[
    df_su_raw["latitude"].notna() &
    df_su_raw["longitude"].notna() &
    df_su_raw["latitude"].between(LAT_MIN, LAT_MAX) &
    df_su_raw["longitude"].between(LNG_MIN, LNG_MAX)
][["latitude","longitude"]].values.tolist()

fg_signups_heat = folium.FeatureGroup(
    name=f"📝 Heatmap signups ({len(signups_heat)} usuarios)", show=False
)
if signups_heat:
    HeatMap(
        signups_heat, radius=20, blur=16, min_opacity=0.35,
        gradient={0.2:"#1E3A5F", 0.5:"#2563EB", 0.75:"#93C5FD", 1.0:"#FFFFFF"}
    ).add_to(fg_signups_heat)
fg_signups_heat.add_to(m)

# ── Capa 8: Heatmap — Demanda no atendida (incompletas) ───────────────────────
fg_unf = folium.FeatureGroup(name=f"🔥 Heatmap demanda no atendida ({len(df_tx_incomplete)})", show=False)
if len(df_tx_incomplete) > 0:
    heat = df_tx_incomplete[["latitude","longitude"]].values.tolist()
    HeatMap(heat, radius=20, blur=15,
            gradient={0.3:"#7F1D1D", 0.6:"#DC2626", 0.85:"#FCA5A5", 1.0:"#FFFFFF"}
    ).add_to(fg_unf)
fg_unf.add_to(m)

# ── Control de capas ─────────────────────────────────────────────────────────
folium.LayerControl(collapsed=False, position="topright").add_to(m)

# ── Leyenda ───────────────────────────────────────────────────────────────────
legend = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
     padding:14px 18px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.2);
     font-family:system-ui,sans-serif;font-size:12px;line-height:1.8;min-width:210px">
  <b style="font-size:13px">Bizne · PA CDMX · Abr 2026</b><br>
  <hr style="margin:6px 0;border:none;border-top:1px solid #eee">
  <b>Zonas de demanda</b><br>
  <span style="color:#dc2626">█</span> A — Prioridad Alta (sin supply)<br>
  <span style="color:#f97316">█</span> B — Prioridad Media<br>
  <span style="color:#eab308">█</span> C — Vigilancia<br>
  <span style="color:#22c55e">█</span> D — Baja<br>
  <hr style="margin:6px 0;border:none;border-top:1px solid #eee">
  <b>Puntos</b><br>
  <span style="color:#7c3aed">●</span> Sector PA<br>
  <span style="color:#888">●</span> Estación Metro (color por línea)<br>
  <span style="color:#16a34a">●</span> Negocio activo calidad alta<br>
  <span style="color:#ca8a04">●</span> Negocio activo calidad media<br>
  <span style="color:#6b7280">●</span> Cocina dormida (toggle para ver)<br>
  <span style="color:#f97316;font-weight:700">●</span> KYC sin primera compra<br>
  <span style="color:#2563eb">●</span> Usuario con compras<br>
  <hr style="margin:6px 0;border:none;border-top:1px solid #eee">
  <b>Zonas sin conversión</b><br>
  <span style="color:#7c3aed">█</span> Sin supply cerca (oferta = causa)<br>
  <span style="color:#0ea5e9">█</span> Supply insuficiente<br>
  <span style="color:#10b981">█</span> Supply OK (otro factor)
</div>"""
m.get_root().html.add_child(folium.Element(legend))

map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bizne_mapa_real.html")
m.save(map_path)
print(f"✅ Mapa guardado → bizne_mapa_real.html")
print(f"   Capas disponibles:")
print(f"   ✓ Zonas de Demanda (hexágonos)  — visible al abrir")
print(f"   ✓ Sectores PA                   — visible al abrir")
print(f"   ✓ Estaciones Metro              — visible al abrir")
print(f"   ✓ Negocios Activos              — visible al abrir")
print(f"   ✓ KYC sin primera compra ({len(su_sin_compra)})  — visible al abrir  ← nuevo")
print(f"   ○ Usuarios con compras ({len(su_con_compra)})    — oculto (toggle en mapa)")
print(f"   ○ Heatmap trx completadas ({len(df_tx_complete)})  — oculto (toggle en mapa)  ← nuevo")
print(f"   ○ Heatmap demanda no atendida ({len(df_tx_incomplete)}) — oculto (toggle en mapa)")


# %% [markdown]
# ## 10 · Export Kepler.gl — 3 CSVs Listos para Cargar

# %%
# ── CSV 1: Hexágonos de demanda (capa H3) ────────────────────────────────────
kepler_hex = df_hex[[
    "lat", "lng", "zone_tier", "DI",
    "sec_demand", "sec_dem_fijo", "sec_dem_ruta", "sec_dem_patrulla", "sec_dem_metro",
    "su_count", "tx_count", "unf_count",
    "est_daily_demand", "dem_activacion", "D90_daily",
    "biz_count", "N_needed", "gap", "coverage",
    "avg_rating", "priority_score",
]].rename(columns={
    "lat":              "hex_lat",
    "lng":              "hex_lng",
    "sec_demand":       "elementos_sector_total",
    "sec_dem_fijo":     "demanda_fijo",
    "sec_dem_ruta":     "demanda_ruta",
    "sec_dem_patrulla": "demanda_patrulla",
    "sec_dem_metro":    "demanda_metro",
    "su_count":         "usuarios_approved",
    "tx_count":         "transacciones_8d",
    "unf_count":        "tx_incompletas",
    "est_daily_demand": "demanda_estimada_dia",
    "dem_activacion":   "demanda_activacion",
    "D90_daily":        "D90_diario",
    "biz_count":        "negocios_actuales",
    "N_needed":         "negocios_necesarios",
    "coverage":         "cobertura",
    "avg_rating":       "rating_promedio",
})

# Valor numérico de tier para escala de color en Kepler
tier_val = {"A_PRIORIDAD_ALTA": 4, "B_PRIORIDAD_MEDIA": 3, "C_VIGILANCIA": 2, "D_BAJA": 1}
kepler_hex["tier_value"] = kepler_hex["zone_tier"].map(tier_val)
kepler_hex.index.name = "hex_id"
kepler_hex = kepler_hex.reset_index().round(4)

kepler_hex.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_hex_demanda.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_hex_demanda.csv   ({len(kepler_hex):,} hexes)")

# ── CSV 2: Negocios activos (capa Point — supply) ─────────────────────────────
# Campos base + campos de calidad que necesita build_map_v5.py
_biz_base_cols = [
    "latitude", "longitude", "name", "delegacion",
    "rating", "etapa_negocio",
    "transacciones_ultimos_30_dias", "ventas_ultimos_30_dias",
    "tasa_aceptacion_ultimos_30_dias", "tasa_no_aceptados_ultimos_30_dias",
    "effective_capacity", "food_types",
]
_biz_quality_cols = [
    "kitchen_quality_score", "kitchen_quality_nivel",
    "transacciones_historicas", "transacciones_hist_real", "transacciones_ultimos_90_dias",
    "service_cohort", "menu_bizne", "menu_de_dia", "menu_a_la_carta",
    "tiempo_p50_aceptacion_min_ultimos_30_dias",
    "service_id", "phone_number", "owner_name", "hunter",
    "address", "colonia", "bizne_creation_date", "dias_desde_creacion",
    "schedule",
]
_biz_cols = _biz_base_cols + [c for c in _biz_quality_cols if c in df_biz.columns]
kepler_biz = df_biz[_biz_cols].rename(columns={
    "latitude":                                  "lat",
    "longitude":                                 "lng",
    "transacciones_ultimos_30_dias":             "tx_30d",
    "transacciones_ultimos_90_dias":             "tx_90d",
    "transacciones_historicas":                  "tx_historicas",
    "transacciones_hist_real":                   "tx_hist_real",
    "ventas_ultimos_30_dias":                    "ventas_30d",
    "tasa_aceptacion_ultimos_30_dias":           "tasa_aceptacion",
    "tasa_no_aceptados_ultimos_30_dias":         "tasa_rechazo",
    "effective_capacity":                        "capacidad_comidas_dia",
    "tiempo_p50_aceptacion_min_ultimos_30_dias": "tiempo_acepta",
    "bizne_creation_date":                       "creation_date",
    "dias_desde_creacion":                       "dias_creacion",
    "schedule":                                  "horario",
}).round(4)

kepler_biz.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_negocios.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_negocios.csv       ({len(kepler_biz):,} negocios activos)")

# ── CSV: Cocinas Dormidas (capa separada) ─────────────────────────────────────
kepler_dorm = df_biz_dorm[[
    "latitude", "longitude", "name", "delegacion", "rating",
    "transacciones_historicas", "dias_desde_ultima_transaccion",
    "kitchen_quality_score", "etapa_negocio",
]].rename(columns={
    "latitude":                    "lat",
    "longitude":                   "lng",
    "transacciones_historicas":    "tx_historicas",
    "dias_desde_ultima_transaccion":"dias_sin_trx",
    "kitchen_quality_score":       "quality_score",
}).round(4)
kepler_dorm["capacidad_si_reactiva"] = CAPACITY_INACTIVE
kepler_dorm.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_dormidas.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_dormidas.csv        ({len(kepler_dorm):,} cocinas dormidas)")

# ── CSV 3: Sectores PA (capa Point — demanda potencial) ──────────────────────
# Excluir "Direcion" — contiene comas internas que rompen el parsing de Kepler
kepler_sec = df_sec[["lat", "lng", "indicativo", "sector", "elementos"]].copy().round(6)
# Supuestos: 30% conversión objetivo × 6 tx/mes / 30 días
kepler_sec["usuarios_potenciales"]  = (kepler_sec["elementos"] * TARGET_CONVERSION).round(0).astype(int)
kepler_sec["demanda_diaria_est"]    = (kepler_sec["elementos"] * TARGET_CONVERSION * TX_PER_USER_DAY).round(1)
kepler_sec["demanda_mensual_est"]   = (kepler_sec["elementos"] * TARGET_CONVERSION * TX_PER_USER_MONTH).round(0).astype(int)

kepler_sec.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_sectores.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_sectores.csv        ({len(kepler_sec):,} sectores)")

# ── CSV 4: Signups APPROVED (capa Point — usuarios activos) ──────────────────
kepler_su = df_su[[
    "user_id", "latitude", "longitude",
    "transacciones", "consumo_total", "ticket_promedio", "days_to_first_trx"
]].rename(columns={
    "latitude":       "lat",
    "longitude":      "lng",
    "transacciones":  "tx_total",
    "consumo_total":  "consumo_mxn",
    "ticket_promedio":"ticket_prom",
}).round(4)

kepler_su.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_usuarios.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_usuarios.csv        ({len(kepler_su):,} usuarios APPROVED)")

# ── CSV 5: Estaciones Metro (capa Point) ─────────────────────────────────────
kepler_metro = df_metro[[
    "lat", "lng", "linea", "nombre", "elementos", "n_lineas"
]].rename(columns={
    "elementos": "elementos_estimados",
    "n_lineas":  "num_lineas_transbordo",
}).round(4)
kepler_metro.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepler_real_metro.csv"), index=False, encoding="utf-8")
print(f"✅ kepler_real_metro.csv           ({len(kepler_metro):,} registros, {kepler_metro['nombre'].nunique()} estaciones)")


# %% [markdown]
# ## 11 · Resumen Ejecutivo

# %%
print("\n" + "═"*60)
print("  RESUMEN EJECUTIVO — BIZNE DEMANDA PA · ABRIL 2026")
print("═"*60)

print(f"\n📊 MERCADO")
print(f"  Elementos totales PA      : {MARKET_SIZE:,}")
print(f"  Usuarios APPROVED activos : {len(df_su):,} ({PENETRATION_RATE:.1%} penetración)")
print(f"  Usuarios en proceso KYC   : {len(df_su_potential):,} (demanda potencial próxima)")
print(f"  Ticket promedio           : ${df_su.ticket_promedio.median():.0f} MXN")

print(f"\n🏢 SUPPLY ACTUAL")
print(f"  Negocios en CDMX          : {len(df_biz):,}")
print(f"  Calidad promedio           : {df_biz.kitchen_quality_score.mean():.1f}/100")
print(f"  Rating promedio            : {df_biz.rating.mean():.2f} ⭐")
print(f"  Tasa aceptación promedio   : {df_biz.tasa_aceptacion_ultimos_30_dias.mean():.1%}")

print(f"\n🗺 ZONAS DE OPORTUNIDAD")
for tier in ["A_PRIORIDAD_ALTA","B_PRIORIDAD_MEDIA","C_VIGILANCIA"]:
    sub = df_hex[df_hex.zone_tier == tier]
    emoji = emojis.get(tier, "·")
    print(f"  {emoji} {tier:<22}: {len(sub):4d} hexes | "
          f"gap total: {sub.gap.sum():.0f} negocios | "
          f"demanda: {sub.est_daily_demand.sum():.0f} tx/día")

print(f"\n🎯 TOP 5 HEXES PARA ADQUISICIÓN INMEDIATA")
top5 = df_hex.nlargest(5, "priority_score")[
    ["lat","lng","zone_tier","elementos_sector" if "elementos_sector" in df_hex.columns else "sec_demand",
     "gap","coverage","priority_score"]
]
# Fix column reference
top5 = df_hex.nlargest(5, "priority_score")[
    ["lat","lng","zone_tier","sec_demand","gap","coverage","priority_score"]
].rename(columns={"sec_demand":"elementos_sector"})
print(top5.round(3).to_string())

print(f"\n✅ Archivos Kepler listos:")
print(f"   📍 kepler_real_hex_demanda.csv  — hexágonos H3 (capa principal)")
print(f"   🏪 kepler_real_negocios.csv     — negocios activos (supply)")
print(f"   🏢 kepler_real_sectores.csv     — sectores PA (demanda potencial)")
print(f"   👤 kepler_real_usuarios.csv     — usuarios APPROVED (demanda activa)")
print(f"\n   📍 bizne_mapa_real.html         — mapa Folium (preview)")


# Copiar como index.html para GitHub Pages
import shutil
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bizne_mapa_real.html"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))
print("✅ index.html listo para GitHub Pages")
