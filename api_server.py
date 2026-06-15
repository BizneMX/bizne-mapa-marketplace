"""
api_server.py — API mínimo para el Route Builder del mapa Bizne (staging).

Endpoints:
  GET  /api/assignments        → asignaciones actuales (tabla hunter_zone_assignments)
  POST /api/assignments        → guarda el snapshot completo de asignaciones
  POST /api/chat               → chat con Claude (claude-haiku-4-5) con contexto de zonas

Debe correr DENTRO de la VPC (la BD no es pública) — p.ej. el mismo host del MCP.
Ver DEPLOY_API.md para systemd/nginx.

Variables de entorno:
  DATABASE_URL  (o DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD)
      ⚠ Usuario con permisos de ESCRITURA (redash_reader es read-only).
  ANTHROPIC_API_KEY      — para /api/chat
  RB_CORS_ORIGINS        — orígenes permitidos, coma-separados (default: *)
"""
import json
import os

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# ── DB ─────────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS hunter_zone_assignments (
    id SERIAL PRIMARY KEY,
    hex_id VARCHAR(20) NOT NULL,
    hex_code VARCHAR(10),
    hunter_name VARCHAR(100),
    route_order INTEGER,
    assigned_at TIMESTAMP DEFAULT NOW(),
    assigned_by VARCHAR(100),
    notes TEXT,
    UNIQUE(hex_id, hunter_name)
);
"""

_engine = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    if os.environ.get('DATABASE_URL'):
        raw = os.environ['DATABASE_URL']
        for prefix in ('postgres://', 'postgresql://'):
            if raw.startswith(prefix):
                raw = raw.replace(prefix, 'postgresql+psycopg2://', 1)
                break
        _engine = create_engine(raw, pool_pre_ping=True)
    else:
        required = ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing = [v for v in required if not os.environ.get(v)]
        if missing:
            raise HTTPException(503, f"BD no configurada — faltan: {', '.join(missing)}")
        _engine = create_engine(URL.create(
            'postgresql+psycopg2',
            username=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
            host=os.environ['DB_HOST'], port=int(os.environ.get('DB_PORT', '5432')),
            database=os.environ['DB_NAME'],
        ), pool_pre_ping=True)
    with _engine.begin() as conn:
        conn.execute(text(DDL))
    return _engine


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title='Bizne Route Builder API', version='1.0')

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


class SavePayload(BaseModel):
    assigned_by: str = 'mapa'
    week: str = ''
    assignments: list[Assignment]


class ChatPayload(BaseModel):
    message: str
    history: list[dict] = []
    context: dict = {}


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/assignments')
def get_assignments():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            'SELECT hex_id, hex_code, hunter_name, route_order, assigned_at, assigned_by, notes '
            'FROM hunter_zone_assignments ORDER BY hunter_name, route_order'
        )).mappings().all()
    return {'assignments': [dict(r) for r in rows]}


@app.post('/api/assignments')
def save_assignments(payload: SavePayload):
    """Guarda el snapshot completo: reemplaza la asignación vigente."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM hunter_zone_assignments'))
        for a in payload.assignments:
            conn.execute(text(
                'INSERT INTO hunter_zone_assignments '
                '(hex_id, hex_code, hunter_name, route_order, assigned_by, notes) '
                'VALUES (:hex_id, :hex_code, :hunter_name, :route_order, :assigned_by, :notes) '
                'ON CONFLICT (hex_id, hunter_name) DO UPDATE SET '
                'route_order = EXCLUDED.route_order, assigned_at = NOW(), '
                'assigned_by = EXCLUDED.assigned_by, notes = EXCLUDED.notes'
            ), {
                'hex_id': a.hex_id, 'hex_code': a.hex_code, 'hunter_name': a.hunter_name,
                'route_order': a.route_order, 'assigned_by': payload.assigned_by, 'notes': a.notes,
            })
    return {'saved': len(payload.assignments)}


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
                    'action': {'type': 'string', 'enum': ['assign']},
                    'hex_id': {'type': 'string'},
                    'hex_code': {'type': 'string'},
                    'hunter': {'type': 'string'},
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
            model='claude-haiku-4-5',   # respuestas rápidas, pedido explícito
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
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8090')))
