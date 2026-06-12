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

Variables de entorno requeridas: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
Opcionales: DB_SSLMODE (default: prefer)

Uso:
    python build_map_v6.py            # pipeline completo → staging.html
    python build_map_v6.py --dry-run  # valida extracción de queries sin tocar la BD
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


def run_model():
    """Corre bizne_model_ci.py sin MCP_API_KEY → usa los caches recién escritos."""
    print("== Paso 2: Modelo de demanda (bizne_model_ci.py via caches) ==")
    env = os.environ.copy()
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


def main():
    sqls = extract_sql_constants()
    print(f"✅ Queries extraídos de bizne_model_ci.py: {sorted(sqls)}")

    if '--dry-run' in sys.argv:
        for name in QUERIES:
            print(f"  {name}: {len(sqls[name]):,} chars")
        print("Dry-run OK — no se tocó la BD.")
        return

    fetch_caches(sqls)
    run_model()
    run_map()
    print("✅ Pipeline v6 completo → staging.html")


if __name__ == '__main__':
    main()
