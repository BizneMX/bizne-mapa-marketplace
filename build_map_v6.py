"""
build_map_v6.py — Pipeline del mapa Bizne leyendo PostgreSQL directo (SQLAlchemy).

Reemplaza la capa MCP HTTP de bizne_model_ci.py por una conexión directa a la BD,
sin modificar build_map_v5.py ni bizne_model_ci.py:

  1. Extrae los 4 queries SQL (SQL_NEGOCIOS, SQL_USUARIOS, SQL_TRANSACCIONES, SQL_UPCS)
     directamente de bizne_model_ci.py vía AST — una sola fuente de verdad, sin duplicar SQL.
  2. Los ejecuta contra PostgreSQL con SQLAlchemy y escribe los caches pg_*_cache.csv
     que bizne_model_ci.py consume como fallback.
  3. Corre bizne_model_ci.py SIN MCP_API_KEY → toma los caches → genera kepler_real_*.csv
     y actualiza data/upcs.csv (modelo de demanda idéntico al de producción).
  4. Corre build_map_v5.py intacto, y renombra el index.html resultante a staging.html,
     restaurando el index.html original (producción no se toca).

Fuentes de datos (en orden de preferencia):
  1. PostgreSQL directo (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD o DATABASE_URL)
     — requiere ruta de red a la BD. La BD vive en VPC privada (10.200.x.x), así que
     esto solo aplica desde un entorno con acceso (VPN, runner self-hosted en la VPC).
  2. MCP (MCP_API_KEY) — gateway público https://mcp.bizne.com.mx/mcp. Es la fuente
     operativa desde GitHub Actions; mismo SQL, mismos datos, vía bizne_model_ci.py.

Opcionales: DB_SSLMODE (default: prefer)

  5. Inyecta el Route Builder (route_builder.js + SortableJS) en staging.html —
     panel drag & drop de asignación de zonas a hunters + AI chat. La URL del
     API server (api_server.py) se toma de RB_API_URL si está definida.

Uso:
    python build_map_v6.py                # pipeline completo → staging.html
    python build_map_v6.py --dry-run      # valida extracción de queries sin tocar la BD
    python build_map_v6.py --inject-only  # solo re-inyecta route_builder.js en staging.html
"""
import ast
import os
import shutil
import subprocess
import sys

DIR          = os.path.dirname(os.path.abspath(__file__))
MODEL_SCRIPT = os.path.join(DIR, 'bizne_model_ci.py')
MAP_SCRIPT   = os.path.join(DIR, 'build_map_v5.py')
INDEX_HTML   = os.path.join(DIR, 'index.html')
STAGING_HTML = os.path.join(DIR, 'staging.html')

# Constante SQL en bizne_model_ci.py → cache CSV que _query_mcp() usa como fallback
QUERIES = {
    'SQL_NEGOCIOS':      'pg_negocios_cache.csv',
    'SQL_USUARIOS':      'pg_usuarios_cache.csv',
    'SQL_TRANSACCIONES': 'pg_transacciones_cache.csv',
    'SQL_UPCS':          'pg_upcs_cache.csv',
}


def extract_sql_constants():
    """Lee los queries SQL de bizne_model_ci.py sin ejecutarlo (AST)."""
    with open(MODEL_SCRIPT, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in QUERIES
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            found[node.targets[0].id] = node.value.value
    missing = set(QUERIES) - set(found)
    if missing:
        raise SystemExit(f"❌ No se encontraron en bizne_model_ci.py: {sorted(missing)}")
    return found


def build_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    connect_args = {
        'sslmode': os.environ.get('DB_SSLMODE', 'prefer'),
        'connect_timeout': 30,
    }

    # Fallback: DATABASE_URL completo (secret ya existente en el repo)
    if not os.environ.get('DB_HOST') and os.environ.get('DATABASE_URL'):
        raw = os.environ['DATABASE_URL']
        if raw.startswith('postgres://'):
            raw = raw.replace('postgres://', 'postgresql+psycopg2://', 1)
        elif raw.startswith('postgresql://'):
            raw = raw.replace('postgresql://', 'postgresql+psycopg2://', 1)
        return create_engine(raw, connect_args=connect_args)

    required = ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
    faltantes = [v for v in required if not os.environ.get(v)]
    if faltantes:
        raise SystemExit(f"❌ Variables de entorno faltantes: {', '.join(faltantes)} "
                         f"(o define DATABASE_URL completo)")

    url = URL.create(
        'postgresql+psycopg2',
        username=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        host=os.environ['DB_HOST'],
        port=int(os.environ.get('DB_PORT', '5432')),
        database=os.environ['DB_NAME'],
    )
    return create_engine(url, connect_args=connect_args)


def fetch_caches(sqls):
    """Ejecuta cada query y escribe el cache CSV que bizne_model_ci.py consume."""
    import pandas as pd

    engine = build_engine()
    print("== Paso 1: Consultando PostgreSQL directo (SQLAlchemy) ==")
    with engine.connect() as conn:
        for name, cache_file in QUERIES.items():
            print(f"  Ejecutando {name}…")
            df = pd.read_sql(sqls[name], conn)
            if len(df) == 0:
                raise SystemExit(f"❌ {name} devolvió 0 filas — abortando para no corromper caches")
            cache_path = os.path.join(DIR, cache_file)
            df.to_csv(cache_path, index=False, encoding='utf-8')
            print(f"  ✅ {name}: {len(df):,} filas → {cache_file}")
    engine.dispose()


def run_model(fuente):
    """Corre bizne_model_ci.py. Si la fuente fue Postgres directo, se quita
    MCP_API_KEY para forzar el uso de los caches recién escritos; si la fuente
    es 'mcp', se deja la key para que el modelo consulte el MCP él mismo."""
    print(f"== Paso 2: Modelo de demanda (bizne_model_ci.py · fuente: {fuente}) ==")
    env = os.environ.copy()
    if fuente == 'postgres':
        env.pop('MCP_API_KEY', None)   # fuerza el fallback a cache local
    env['GITHUB_ACTIONS_RUN'] = 'true'
    subprocess.run([sys.executable, MODEL_SCRIPT], check=True, env=env, cwd=DIR)


def run_map():
    """Corre build_map_v5.py intacto y desvía su output a staging.html."""
    print("== Paso 3: Ensamblando mapa (build_map_v5.py → staging.html) ==")
    backup = INDEX_HTML + '.v6bak'
    had_index = os.path.exists(INDEX_HTML)
    if had_index:
        shutil.copy2(INDEX_HTML, backup)
    try:
        env = os.environ.copy()
        env['GITHUB_ACTIONS_RUN'] = 'true'
        subprocess.run([sys.executable, MAP_SCRIPT], check=True, env=env, cwd=DIR)
        os.replace(INDEX_HTML, STAGING_HTML)
        print(f"  ✅ staging.html generado ({os.path.getsize(STAGING_HTML):,} bytes)")
    finally:
        if had_index and os.path.exists(backup):
            os.replace(backup, INDEX_HTML)
            print("  ✅ index.html de producción restaurado")


SORTABLE_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js'
RB_MARKER    = '<!-- route-builder-v6 -->'
RB_JS        = os.path.join(DIR, 'route_builder.js')


def inject_route_builder():
    """Inyecta SortableJS + route_builder.js antes de </body> en staging.html.
    Idempotente: si ya hay una inyección previa, la reemplaza."""
    print("== Paso 4: Inyectando Route Builder en staging.html ==")
    if not os.path.exists(STAGING_HTML):
        raise SystemExit("❌ No existe staging.html — corre el pipeline completo primero")
    with open(RB_JS, encoding='utf-8') as f:
        js = f.read()
    with open(STAGING_HTML, encoding='utf-8') as f:
        html = f.read()
    # Quitar inyección previa (entre marcadores)
    if RB_MARKER in html:
        start = html.index(RB_MARKER)
        end   = html.rindex(RB_MARKER) + len(RB_MARKER)
        html  = html[:start] + html[end:]
    api_url = os.environ.get('RB_API_URL', '')

    # Hunters desde RDS para fallback sin API pública
    hunters_data = []
    try:
        import psycopg2
        db_url = os.environ.get('DATABASE_URL', '')
        if db_url:
            conn = psycopg2.connect(db_url)
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, nombre, apellido, email FROM usuarios
                WHERE activo = true AND auth_role = 'hunters' ORDER BY nombre
            """)
            hunters_data = [
                {'id': r[0], 'nombre': r[1] or '', 'apellido': r[2] or '', 'email': r[3]}
                for r in cur.fetchall()
            ]
            conn.close()
            print(f"  👥 {len(hunters_data)} hunters bakeados desde RDS")
    except Exception as e:
        print(f"  ⚠ No se pudo cargar hunters desde DB: {e}")

    import json as _json
    block = (
        f'{RB_MARKER}\n'
        f'<script src="{SORTABLE_CDN}"></script>\n'
        f'<script>window.RB_CONFIG = {{"apiUrl": {json_dumps(api_url)}, '
        f'"hunters": {_json.dumps(hunters_data, ensure_ascii=False)}}};</script>\n'
        f'<script>\n{js}\n</script>\n'
        f'{RB_MARKER}'
    )
    if '</body>' in html:
        html = html.replace('</body>', block + '\n</body>', 1)
    else:
        html += block
    with open(STAGING_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  ✅ Route Builder inyectado ({len(js):,} chars de JS · API: {api_url or '(no configurado)'})")


def json_dumps(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def main():
    if '--inject-only' in sys.argv:
        inject_route_builder()
        return

    sqls = extract_sql_constants()
    print(f"✅ Queries extraídos de bizne_model_ci.py: {sorted(sqls)}")

    if '--dry-run' in sys.argv:
        for name in QUERIES:
            print(f"  {name}: {len(sqls[name]):,} chars")
        print("Dry-run OK — no se tocó la BD.")
        return

    # Fuente primaria: Postgres directo (solo entornos con ruta a la VPC).
    # Desde GitHub Actions la fuente operativa es el MCP.
    fuente = 'postgres'
    try:
        fetch_caches(sqls)
    except (SystemExit, Exception) as e:
        if not os.environ.get('MCP_API_KEY'):
            raise
        print(f"⚠ Postgres directo falló ({e}) — fallback a MCP_API_KEY")
        fuente = 'mcp'

    run_model(fuente)
    run_map()
    inject_route_builder()
    print(f"✅ Pipeline v6 completo → staging.html (fuente de datos: {fuente})")


if __name__ == '__main__':
    main()
