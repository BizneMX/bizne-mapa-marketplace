"""
api_server.py — API del Route Builder de Bizne (staging).

Endpoints:
  GET  /api/assignments          → asignaciones de la semana (PostgreSQL)
  POST /api/assignments          → guarda snapshot completo de la semana
  GET  /api/hunter/ruta          → hexes del hunter autenticado (app de Hunting)
  POST /api/chat                 → chat con Claude con contexto de zonas

Variables de entorno:
  DATABASE_URL          — postgresql://user:pass@host:port/db
  ANTHROPIC_API_KEY     — para /api/chat
  RB_CORS_ORIGINS       — orígenes permitidos, coma-separados (default: *)
  BIZNE_JWT_SECRET      — secreto para validar el JWT de la plataforma Bizne (MCP)
  BIZNE_JWT_ALGORITHM   — algoritmo JWT (default: HS256)
  HUNTERS_MAP           — JSON {"email": "nombre_display"} email → hunter_name
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import anthropic
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# ── Base de datos ───────────────────────────────────────────────────────
_engine = None

DDL = """
CREATE TABLE IF NOT EXISTS hunter_zone_assignments (
    id            SERIAL PRIMARY KEY,
    week          VARCHAR(10)  NOT NULL,
    hex_id        VARCHAR(30)  NOT NULL,
    hex_code      VARCHAR(20)  NOT NULL DEFAULT '',
    hunter_name   VARCHAR(100) NOT NULL,
    route_order   INTEGER      NOT NULL DEFAULT 1,
    assigned_by   VARCHAR(50)  DEFAULT 'mapa',
    notes         TEXT         DEFAULT '',
    days          VARCHAR(20)  DEFAULT '',
    assigned_at   TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT uq_week_hex_hunter UNIQUE(week, hex_id, hunter_name)
);
CREATE INDEX IF NOT EXISTS idx_assignments_week   ON hunter_zone_assignments(week);
CREATE INDEX IF NOT EXISTS idx_assignments_hunter ON hunter_zone_assignments(hunter_name);
"""


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise HTTPException(503, 'DATABASE_URL no configurado')
    _engine = create_engine(url, pool_pre_ping=True)
    with _engine.begin() as conn:
        conn.execute(text(DDL))
    return _engine


def current_iso_week() -> str:
    d = date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _parse_days(raw) -> list:
    if not raw:
        return []
    try:
        return [int(x) for x in str(raw).split(',') if x.strip()]
    except Exception:
        return []


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title='Bizne Route Builder API', version='3.0')

_origins = [o.strip() for o in os.environ.get('RB_CORS_ORIGINS', '*').split(',')]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=['*'],
    allow_headers=['*'],
)


class Assignment(BaseModel):
    hex_id: str
    hex_code: str = ''
    hunter_name: str
    route_order: int = 1
    notes: str = ''
    days: List[int] = []


class SavePayload(BaseModel):
    assigned_by: str = 'mapa'
    week: str = ''
    assignments: List[Assignment]


class ChatPayload(BaseModel):
    message: str
    history: List[Dict] = []
    context: Dict = {}


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/assignments')
def get_assignments(week: str = Query(default='')):
    wk = week or current_iso_week()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT hex_id, hex_code, hunter_name, week, route_order,
                       assigned_at, assigned_by, notes, days
                FROM hunter_zone_assignments
                WHERE week = :week
                ORDER BY hunter_name, route_order
            """),
            {'week': wk},
        ).fetchall()
    result = [
        {
            'hex_id':      r.hex_id,
            'hex_code':    r.hex_code,
            'hunter_name': r.hunter_name,
            'week':        r.week,
            'route_order': r.route_order,
            'assigned_at': r.assigned_at.isoformat() if r.assigned_at else '',
            'assigned_by': r.assigned_by or '',
            'notes':       r.notes or '',
            'days':        _parse_days(r.days),
        }
        for r in rows
    ]
    return {'assignments': result, 'week': wk}


@app.post('/api/assignments')
def save_assignments(payload: SavePayload):
    wk = payload.week or current_iso_week()
    engine = get_engine()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text('DELETE FROM hunter_zone_assignments WHERE week = :week'),
            {'week': wk},
        )
        if payload.assignments:
            conn.execute(
                text("""
                    INSERT INTO hunter_zone_assignments
                        (week, hex_id, hex_code, hunter_name, route_order,
                         assigned_by, notes, days, assigned_at)
                    VALUES
                        (:week, :hex_id, :hex_code, :hunter_name, :route_order,
                         :assigned_by, :notes, :days, :assigned_at)
                """),
                [
                    {
                        'week':        wk,
                        'hex_id':      a.hex_id,
                        'hex_code':    a.hex_code,
                        'hunter_name': a.hunter_name,
                        'route_order': a.route_order,
                        'assigned_by': payload.assigned_by,
                        'notes':       a.notes,
                        'days':        ','.join(str(d) for d in a.days) if a.days else '',
                        'assigned_at': now,
                    }
                    for a in payload.assignments
                ],
            )
    return {'saved': len(payload.assignments), 'week': wk}


# ── Auth JWT (MCP Bizne) ───────────────────────────────────────────────
def _extract_jwt(request: Request) -> Optional[str]:
    token = request.cookies.get('bizne_token')
    if token:
        return token
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if auth and auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    return None


def _get_email_from_jwt(request: Request) -> str:
    secret = os.environ.get('BIZNE_JWT_SECRET')
    if not secret:
        raise HTTPException(503, 'BIZNE_JWT_SECRET no configurado')
    token = _extract_jwt(request)
    if not token:
        raise HTTPException(401, 'No autenticado: falta el token de la plataforma (cookie bizne_token).')
    try:
        claims = jwt.decode(
            token, secret,
            algorithms=[os.environ.get('BIZNE_JWT_ALGORITHM', 'HS256')],
            options={'verify_aud': False},
        )
    except JWTError as exc:
        raise HTTPException(401, f'Token inválido o expirado: {exc}') from exc
    email = claims.get('sub')
    if not email:
        raise HTTPException(401, "Token sin claim 'sub'.")
    return email


def _hunters_map() -> dict:
    raw = os.environ.get('HUNTERS_MAP', '{}')
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _iso_week_to_monday(iso_week: str) -> str:
    try:
        m = iso_week.split('-W')
        year, week = int(m[0]), int(m[1])
        jan4 = date(year, 1, 4)
        monday = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=week - 1)
        return monday.isoformat()
    except Exception:
        return date.today().isoformat()


@app.get('/api/hunter/ruta')
def hunter_ruta(request: Request, week: str = Query(default='')):
    """HEXes asignados al hunter autenticado. Identidad extraída del JWT del MCP."""
    email = _get_email_from_jwt(request)
    wk = week or current_iso_week()
    hunter_name = _hunters_map().get(email)
    if not hunter_name:
        return {'hexes': [], 'week': wk}

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT hex_id, hex_code, notes, route_order, days
                FROM hunter_zone_assignments
                WHERE week = :week AND hunter_name = :hunter_name
                ORDER BY route_order
            """),
            {'week': wk, 'hunter_name': hunter_name},
        ).fetchall()

    fecha_visita = _iso_week_to_monday(wk)
    hexes = [
        {
            'hex_id':       r.hex_id,
            'hex_code':     r.hex_code,
            'colonia':      r.notes or '',
            'referencia':   None,
            'fecha_visita': fecha_visita,
            'estado':       'pendiente',
            'route_order':  r.route_order,
            'days':         _parse_days(r.days),
        }
        for r in rows
    ]
    return {'hexes': hexes, 'week': wk}


# ── Chat con Claude ────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres el asistente de planeación de rutas de hunting de Bizne para \
Policía Auxiliar en CDMX y Estado de México. Conoces el modelo de zonas H3 (resolución 8, \
malla con numeración HEX-XXXXX fija): la prioridad de cada hexágono se calcula 100% con \
sesiones de usuarios (proxy de demanda) cruzadas con la oferta de negocios en el hex y su \
anillo vecino. Tiers: A=rojo alta prioridad (demanda sin cobertura), B=naranja media-alta, \
C=amarillo equilibrio, D=verde cubierta. gap = cocinas faltantes (1 cocina ≈ 10 usuarios).

Tu trabajo: ayudar a asignar zonas a hunters y optimizar sus rutas de campo.
- Para agrupar zonas contiguas usa la distancia entre lat/lng (hexes H3-r8 miden ~0.7 km
  de radio; vecinos están a <1.5 km del centro).
- Prioriza tier A (🔴) y zonas con mayor score/gap.
- Cuando sugieras asignaciones concretas, inclúyelas en `actions` para que el usuario
  las confirme con un clic. Usa hex_code (HEX-XXXX) y el nombre exacto del hunter.
- Responde en español, conciso y accionable. El contexto JSON del estado actual del
  mapa (zonas, métricas, hunters, asignaciones) llega en cada mensaje."""

CHAT_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string'},
        'actions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'action':      {'type': 'string', 'enum': ['assign']},
                    'hex_id':      {'type': 'string'},
                    'hex_code':    {'type': 'string'},
                    'hunter':      {'type': 'string'},
                    'route_order': {'type': 'integer'},
                },
                'required': ['action', 'hex_code', 'hunter'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['reply', 'actions'],
    'additionalProperties': False,
}

_claude = None


def get_claude():
    global _claude
    if _claude is None:
        if not os.environ.get('ANTHROPIC_API_KEY'):
            raise HTTPException(503, 'ANTHROPIC_API_KEY no configurada')
        _claude = anthropic.Anthropic()
    return _claude


@app.post('/api/chat')
def chat(payload: ChatPayload):
    client = get_claude()
    history = [
        {'role': m['role'], 'content': m['content']}
        for m in payload.history[-12:]
        if m.get('role') in ('user', 'assistant') and m.get('content')
    ]
    user_msg = (
        f"<contexto_mapa>\n{json.dumps(payload.context, ensure_ascii=False)}\n</contexto_mapa>\n\n"
        f"{payload.message}"
    )
    try:
        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=2000,
            system=[{
                'type': 'text',
                'text': SYSTEM_PROMPT,
                'cache_control': {'type': 'ephemeral'},
            }],
            messages=history + [{'role': 'user', 'content': user_msg}],
            output_config={'format': {'type': 'json_schema', 'schema': CHAT_SCHEMA}},
        )
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f'Claude API error: {e.message}')
    text_block = next((b.text for b in response.content if b.type == 'text'), '{}')
    try:
        data = json.loads(text_block)
    except json.JSONDecodeError:
        data = {'reply': text_block, 'actions': []}
    return {'reply': data.get('reply', ''), 'actions': data.get('actions', [])}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))
