#!/usr/bin/env bash
# deploy_rb.sh — Instala y arranca el Route Builder API en el host MCP.
# Ejecutar como root (o con sudo) desde el directorio de este repo:
#   bash deploy_rb.sh
set -euo pipefail

VENV=/opt/bizne-rb
WORKDIR=/opt/bizne-mapa
SERVICE=bizne-rb
ENV_FILE=/etc/bizne-rb.env
PORT=8090

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Bizne Route Builder — Deploy Script"
echo "═══════════════════════════════════════════════════"
echo ""

# ── 1. Copiar api_server.py al workdir ─────────────────────────────────
mkdir -p "$WORKDIR"
cp "$(dirname "$0")/api_server.py" "$WORKDIR/api_server.py"
echo "✓ api_server.py copiado a $WORKDIR"

# ── 2. Virtualenv + dependencias ───────────────────────────────────────
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  echo "✓ Virtualenv creado en $VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q fastapi "uvicorn[standard]" anthropic sqlalchemy psycopg2-binary
echo "✓ Dependencias instaladas"

# ── 3. Variables de entorno ─────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo "Configuración de entorno (se guarda en $ENV_FILE, chmod 600):"
  echo ""
  read -rsp "  DB password para rb_writer: " DB_PASS; echo ""
  read -rsp "  ANTHROPIC_API_KEY (sk-ant-...): " ANT_KEY; echo ""
  read -rp  "  CORS origins (default: *): " CORS_ORIGINS
  CORS_ORIGINS="${CORS_ORIGINS:-*}"

  cat > "$ENV_FILE" <<EOF
DATABASE_URL=postgresql://rb_writer:${DB_PASS}@10.200.20.198:5432/bizne_api20_staging
ANTHROPIC_API_KEY=${ANT_KEY}
RB_CORS_ORIGINS=${CORS_ORIGINS}
PORT=${PORT}
EOF
  chmod 600 "$ENV_FILE"
  echo "✓ $ENV_FILE creado"
else
  echo "✓ $ENV_FILE ya existe — no se sobreescribe"
fi

# ── 4. Usuario de BD (requiere acceso psql con privilegios) ─────────────
echo ""
echo "¿Quieres crear el usuario rb_writer en PostgreSQL ahora? [s/N]"
read -r CREATE_USER
if [[ "$CREATE_USER" =~ ^[sS]$ ]]; then
  read -rsp "  Password de postgres (superuser): " PG_PASS; echo ""
  source "$ENV_FILE"
  PGPASSWORD="$PG_PASS" psql -h 10.200.20.198 -U postgres -d bizne_api20_staging <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rb_writer') THEN
    CREATE USER rb_writer WITH PASSWORD '$(grep DB_PASS "$ENV_FILE" | cut -d: -f3 | cut -d@ -f1)';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE bizne_api20_staging TO rb_writer;
SQL
  echo "✓ Usuario rb_writer listo (la tabla la crea el API en el primer arranque)"
fi

# ── 5. Servicio systemd ─────────────────────────────────────────────────
cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=Bizne Route Builder API
After=network.target

[Service]
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/uvicorn api_server:app --host 127.0.0.1 --port ${PORT}
WorkingDirectory=${WORKDIR}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
echo "✓ Servicio $SERVICE activo"

# ── 6. Verificar health ─────────────────────────────────────────────────
sleep 2
if curl -sf "http://127.0.0.1:${PORT}/api/health" > /dev/null; then
  echo "✓ Health check OK — el API responde en el puerto $PORT"
else
  echo "✗ El API no responde todavía. Revisa: journalctl -u $SERVICE -n 30"
  exit 1
fi

# ── 7. Snippet nginx ────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "  Agrega esto a tu nginx.conf:"
echo "══════════════════════════════════════════"
cat <<'NGINX'

location /rb/ {
    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    add_header Access-Control-Allow-Origin * always;
}
NGINX

echo ""
echo "Luego: nginx -t && nginx -s reload"
echo ""
echo "══════════════════════════════════════════"
echo "  URL pública: https://mcp.bizne.com.mx/rb"
echo ""
echo "  Configura en GitHub:"
echo "  Settings → Variables → RB_API_URL = https://mcp.bizne.com.mx/rb"
echo "══════════════════════════════════════════"
echo ""
