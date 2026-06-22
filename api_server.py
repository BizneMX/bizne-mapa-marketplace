"""
api_server.py — API mínimo para el Route Builder del mapa Bizne (staging).

Endpoints:
  GET  /api/assignments          → asignaciones actuales (tabla DynamoDB)
  POST /api/assignments          → guarda el snapshot completo de asignaciones
  GET  /api/hunter/ruta          → hexes asignados al hunter autenticado (para la app de Hunting)
  POST /api/chat                 → chat con Claude (claude-haiku-4-5) con contexto de zonas

Variables de entorno:
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION  — credenciales AWS
      (si corre en EC2/Lambda con IAM role, no se necesitan las dos primeras)
  DYNAMO_TABLE          — nombre de la tabla DynamoDB (default: hunter_zone_assignments)
  ANTHROPIC_API_KEY     — para /api/chat
  RB_CORS_ORIGINS       — orígenes permitidos, coma-separados (default: *)
  BIZNE_JWT_SECRET      — secreto para validar el JWT emitido por la plataforma Bizne (MCP)
  BIZNE_JWT_ALGORITHM   — algoritmo JWT (default: HS256)
  HUNTERS_MAP           — JSON {"email": "nombre_display"} para mapear email → hunter_name
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import anthropic
import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel

# ── DynamoDB ────────────────────────────────────────────────────────────
TABLE_NAME = os.environ.get('DYNAMO_TABLE', 'hunter_zone_assignments')

# Esquema de la tabla:
#   PK  week      (String) — "2026-W25"
#   SK  hex_hunter (String) — "{hex_id}#{hunter_name}"

_table = None


def get_table():
    global _table
    if _table is not None:
        return _table

    kwargs = {'region_name': os.environ.get('AWS_REGION', 'us-east-1')}
    dynamodb = boto3.resource('dynamodb', **kwargs)

    try:
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {'AttributeName': 'week',       'KeyType': 'HASH'},
                {'AttributeName': 'hex_hunter', 'KeyType': 'RANGE'},
            ],
            AttributeDefinitions=[
                {'AttributeName': 'week',       'AttributeType': 'S'},
                {'AttributeName': 'hex_hunter', 'AttributeType': 'S'},
            ],
            BillingMode='PAY_PER_REQUEST',
        )
        table.wait_until_exists()
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            table = dynamodb.Table(TABLE_NAME)
        else:
            raise HTTPException(503, f'DynamoDB error: {e}')

    _table = table
    return _table


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


def _query_all(table, wk: str) -> list:
    """Query con paginación automática."""
    items, kwargs = [], {'KeyConditionExpression': Key('week').eq(wk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get('Items', []))
        last = resp.get('LastEvaluatedKey')
        if not last:
            break
        kwargs['ExclusiveStartKey'] = last
    return items


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title='Bizne Route Builder API', version='2.0')

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
    days: list[int] = []


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
def get_assignments(week: str = Query(default='')):
    wk = week or current_iso_week()
    table = get_table()
    items = _query_all(table, wk)
    items.sort(key=lambda r: (r.get('hunter_name', ''), int(r.get('route_order', 1))))
    result = []
    for item in items:
        result.append({
            'hex_id':       item.get('hex_id', ''),
            'hex_code':     item.get('hex_code', ''),
            'hunter_name':  item.get('hunter_name', ''),
            'week':         item.get('week', wk),
            'route_order':  int(item.get('route_order', 1)),
            'assigned_at':  item.get('assigned_at', ''),
            'assigned_by':  item.get('assigned_by', ''),
            'notes':        item.get('notes', ''),
            'days':         _parse_days(item.get('days', '')),
        })
    return {'assignments': result, 'week': wk}


@app.post('/api/assignments')
def save_assignments(payload: SavePayload):
    """Reemplaza el snapshot completo de la semana."""
    wk = payload.week or current_iso_week()
    table = get_table()
    now = datetime.now(timezone.utc).isoformat()

    # Borrar asignaciones existentes de la semana
    existing = _query_all(table, wk)
    with table.batch_writer() as batch:
        for item in existing:
            batch.delete_item(Key={'week': item['week'], 'hex_hunter': item['hex_hunter']})

    # Insertar nuevas asignaciones
    with table.batch_writer() as batch:
        for a in payload.assignments:
            batch.put_item(Item={
                'week':         wk,
                'hex_hunter':   f"{a.hex_id}#{a.hunter_name}",
                'hex_id':       a.hex_id,
                'hex_code':     a.hex_code,
                'hunter_name':  a.hunter_name,
                'route_order':  a.route_order,
                'assigned_by':  payload.assigned_by,
                'notes':        a.notes,
                'days':         ','.join(str(d) for d in a.days) if a.days else '',
                'assigned_at':  now,
            })

    return {'saved': len(payload.assignments), 'week': wk}


# ── Auth JWT (MCP Bizne) ───────────────────────────────────────────────
def _extract_jwt(request: Request) -> str | None:
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
    """'2026-W26' → '2026-06-22'"""
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
    """HEXes asignados al hunter autenticado (para la app de Hunting).
    Identidad extraída del JWT emitido por la plataforma Bizne (MCP).
    """
    email = _get_email_from_jwt(request)
    wk = week or current_iso_week()
    hunter_name = _hunters_map().get(email)
    if not hunter_name:
        return {'hexes': [], 'week': wk}

    table = get_table()
    items = _query_all(table, wk)
    items = [i for i in items if i.get('hunter_name') == hunter_name]
    items.sort(key=lambda i: int(i.get('route_order', 1)))

    fecha_visita = _iso_week_to_monday(wk)
    hexes = [
        {
            'hex_id':      item['hex_id'],
            'hex_code':    item.get('hex_code', ''),
            'colonia':     item.get('notes', ''),
            'referencia':  None,
            'fecha_visita': fecha_visita,
            'estado':      'pendiente',
            'route_order': int(item.get('route_order', 1)),
            'days':        _parse_days(item.get('days', '')),
        }
        for item in items
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
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8090')))
