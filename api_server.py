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
  MCP_URL               — URL del servidor MCP de Bizne (default: https://mcp.bizne.com.mx/mcp)
  MCP_API_KEY           — API key del MCP (Bearer token); si está vacío el chat opera sin herramientas
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import anthropic
import requests as _requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# ── MCP ────────────────────────────────────────────────────────────────
MCP_URL = os.environ.get('MCP_URL', 'https://mcp.bizne.com.mx/mcp')
MCP_API_KEY = os.environ.get('MCP_API_KEY', '')

_MCP_TOOLS_CACHE: Optional[list] = None


def _mcp_call_tool(name: str, arguments: dict) -> str:
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': f'Bearer {MCP_API_KEY}',
    }
    r = _requests.post(MCP_URL, json={
        'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
        'params': {'protocolVersion': '2024-11-05',
                   'clientInfo': {'name': 'rb-chat', 'version': '1.0'},
                   'capabilities': {}},
    }, headers=headers, timeout=30)
    r.raise_for_status()
    sid = r.headers.get('mcp-session-id')
    if sid:
        headers['mcp-session-id'] = sid

    _requests.post(MCP_URL, json={
        'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {},
    }, headers=headers, timeout=15)

    r = _requests.post(MCP_URL, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': name, 'arguments': arguments},
    }, headers=headers, timeout=60)
    r.raise_for_status()

    ct = r.headers.get('content-type', '')
    if 'text/event-stream' in ct:
        body = None
        for line in r.text.splitlines():
            if line.startswith('data:'):
                body = json.loads(line[5:].strip())
    else:
        body = r.json()

    if body is None:
        return ''
    content = body.get('result', {}).get('content', [])
    return content[0].get('text', '') if content else json.dumps(body)


def _mcp_get_tools() -> list:
    global _MCP_TOOLS_CACHE
    if not MCP_API_KEY:
        return []
    if _MCP_TOOLS_CACHE is not None:
        return _MCP_TOOLS_CACHE
    try:
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'Authorization': f'Bearer {MCP_API_KEY}',
        }
        r = _requests.post(MCP_URL, json={
            'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
            'params': {'protocolVersion': '2024-11-05',
                       'clientInfo': {'name': 'rb-chat', 'version': '1.0'},
                       'capabilities': {}},
        }, headers=headers, timeout=30)
        r.raise_for_status()
        sid = r.headers.get('mcp-session-id')
        if sid:
            headers['mcp-session-id'] = sid

        _requests.post(MCP_URL, json={
            'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {},
        }, headers=headers, timeout=15)

        r = _requests.post(MCP_URL, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {},
        }, headers=headers, timeout=30)
        r.raise_for_status()

        ct = r.headers.get('content-type', '')
        if 'text/event-stream' in ct:
            body = None
            for line in r.text.splitlines():
                if line.startswith('data:'):
                    body = json.loads(line[5:].strip())
        else:
            body = r.json()

        raw_tools = (body or {}).get('result', {}).get('tools', [])
        _MCP_TOOLS_CACHE = [
            {
                'name': t['name'],
                'description': t.get('description', ''),
                'input_schema': t.get('inputSchema', {'type': 'object', 'properties': {}}),
            }
            for t in raw_tools
        ]
    except Exception:
        _MCP_TOOLS_CACHE = []
    return _MCP_TOOLS_CACHE


# ── Base de datos ───────────────────────────────────────────────────────
_engine = None

DDL = """
CREATE TABLE IF NOT EXISTS hunter_zone_assignments (
    id            SERIAL PRIMARY KEY,
    week          VARCHAR(10)  NOT NULL,
    hex_id        VARCHAR(30)  NOT NULL,
    hex_code      VARCHAR(20)  NOT NULL DEFAULT '',
    hunter_name   VARCHAR(100) NOT NULL DEFAULT '',
    user_id       INTEGER      REFERENCES usuarios(id),
    route_order   INTEGER      NOT NULL DEFAULT 1,
    assigned_by   VARCHAR(50)  DEFAULT 'mapa',
    notes         TEXT         DEFAULT '',
    days          VARCHAR(20)  DEFAULT '',
    assigned_at   TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT uq_week_hex_user UNIQUE(week, hex_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_assignments_week    ON hunter_zone_assignments(week);
CREATE INDEX IF NOT EXISTS idx_assignments_user_id ON hunter_zone_assignments(user_id);
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
    user_id: int
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


@app.get('/api/hunters')
def get_hunters():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, nombre, apellido, email
            FROM usuarios
            WHERE activo = true AND auth_role = 'hunters'
            ORDER BY nombre
        """)).fetchall()
    return {'hunters': [
        {'id': r.id, 'nombre': r.nombre or '', 'apellido': r.apellido or '', 'email': r.email}
        for r in rows
    ]}


@app.get('/api/assignments')
def get_assignments(week: str = Query(default='')):
    wk = week or current_iso_week()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT hza.hex_id, hza.hex_code, hza.user_id,
                       COALESCE(u.nombre, hza.hunter_name) AS hunter_name,
                       hza.week, hza.route_order,
                       hza.assigned_at, hza.assigned_by, hza.notes, hza.days
                FROM hunter_zone_assignments hza
                LEFT JOIN usuarios u ON u.id = hza.user_id
                WHERE hza.week = :week
                ORDER BY hunter_name, hza.route_order
            """),
            {'week': wk},
        ).fetchall()
    result = [
        {
            'hex_id':      r.hex_id,
            'hex_code':    r.hex_code,
            'user_id':     r.user_id,
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

    # Resolver hunter_name desde user_id
    user_ids = list({a.user_id for a in payload.assignments})
    name_map: Dict[int, str] = {}
    if user_ids:
        with engine.connect() as conn:
            rows = conn.execute(
                text('SELECT id, nombre FROM usuarios WHERE id = ANY(:ids)'),
                {'ids': user_ids},
            ).fetchall()
            name_map = {r.id: r.nombre or '' for r in rows}

    with engine.begin() as conn:
        conn.execute(
            text('DELETE FROM hunter_zone_assignments WHERE week = :week'),
            {'week': wk},
        )
        if payload.assignments:
            conn.execute(
                text("""
                    INSERT INTO hunter_zone_assignments
                        (week, hex_id, hex_code, user_id, hunter_name, route_order,
                         assigned_by, notes, days, assigned_at)
                    VALUES
                        (:week, :hex_id, :hex_code, :user_id, :hunter_name, :route_order,
                         :assigned_by, :notes, :days, :assigned_at)
                """),
                [
                    {
                        'week':        wk,
                        'hex_id':      a.hex_id,
                        'hex_code':    a.hex_code,
                        'user_id':     a.user_id,
                        'hunter_name': name_map.get(a.user_id, ''),
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

    engine = get_engine()
    with engine.connect() as conn:
        # Resolver user_id desde email
        row_u = conn.execute(
            text('SELECT id FROM usuarios WHERE email = :email'),
            {'email': email},
        ).fetchone()
        if not row_u:
            return {'hexes': [], 'week': wk}
        uid = row_u.id
        rows = conn.execute(
            text("""
                SELECT hex_id, hex_code, notes, route_order, days
                FROM hunter_zone_assignments
                WHERE week = :week AND user_id = :user_id
                ORDER BY route_order
            """),
            {'week': wk, 'user_id': uid},
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
SYSTEM_PROMPT = """Eres el Consultor Bizne del mapa de hunting en CDMX y Estado de México.

Tienes acceso a la base de datos completa de Bizne mediante herramientas MCP. Úsalas \
para responder preguntas sobre negocios, consumos, KPIs, menús, niveles de calidad y \
zonas H3 del mapa.

Nomenclatura obligatoria: "Biznes" (no "restaurantes"), "Consumos" (no "transacciones"), \
"BzCoins" (no "puntos").

Modelo de zonas H3 (resolución 8): prioridad calculada con sesiones de usuarios (demanda) \
vs oferta de Biznes en el hex y su anillo vecino. Tiers: A=rojo (demanda sin cobertura), \
B=naranja media-alta, C=amarillo equilibrio, D=verde cubierta. \
gap = cocinas faltantes (1 cocina ≈ 10 usuarios).

Cuando presentes listas de Biznes, usa tablas markdown con los campos más relevantes. \
Responde en español, conciso y accionable. \
Si el usuario pide asignaciones de zonas a hunters, inclúyelas en `actions`. \
El contexto JSON del mapa (hexes, hunters, asignaciones) llega en cada mensaje."""

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
    mcp_tools = _mcp_get_tools()

    user_msg = (
        f"<contexto_mapa>\n{json.dumps(payload.context, ensure_ascii=False)}\n</contexto_mapa>\n\n"
        f"{payload.message}"
    )
    messages = [
        *[
            {'role': m['role'], 'content': m['content']}
            for m in payload.history[-12:]
            if m.get('role') in ('user', 'assistant') and m.get('content')
        ],
        {'role': 'user', 'content': user_msg},
    ]

    response = None
    try:
        for _ in range(6):
            kwargs: dict = dict(
                model='claude-haiku-4-5',
                max_tokens=2000,
                system=[{
                    'type': 'text',
                    'text': SYSTEM_PROMPT,
                    'cache_control': {'type': 'ephemeral'},
                }],
                messages=messages,
                output_config={'format': {'type': 'json_schema', 'schema': CHAT_SCHEMA}},
            )
            if mcp_tools:
                kwargs['tools'] = mcp_tools
            response = client.messages.create(**kwargs)

            if response.stop_reason == 'tool_use':
                tool_results = []
                for block in response.content:
                    if block.type == 'tool_use':
                        try:
                            result = _mcp_call_tool(block.name, block.input)
                        except Exception as exc:
                            result = f'Error al llamar {block.name}: {exc}'
                        tool_results.append({
                            'type': 'tool_result',
                            'tool_use_id': block.id,
                            'content': result,
                        })
                messages.append({'role': 'assistant', 'content': response.content})
                messages.append({'role': 'user', 'content': tool_results})
            else:
                break
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f'Claude API error: {e.message}')

    if response is None:
        return {'reply': 'Sin respuesta', 'actions': []}
    text_block = next((b.text for b in response.content if b.type == 'text'), '{}')
    try:
        data = json.loads(text_block)
    except json.JSONDecodeError:
        data = {'reply': text_block, 'actions': []}
    return {'reply': data.get('reply', ''), 'actions': data.get('actions', [])}


# Handler para AWS Lambda (Mangum adapta ASGI → Lambda event)
try:
    from mangum import Mangum
    handler = Mangum(app, lifespan='off')
except ImportError:
    handler = None  # local: solo se usa uvicorn

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))
