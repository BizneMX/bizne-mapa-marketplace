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
"$VENV/bin/pip" install -q fastapi "uvicorn[standard]" anthropic boto3
echo "✓ Dependencias instaladas"

# ── 3. Variables de entorno ─────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo "Configuración de entorno (se guarda en $ENV_FILE, chmod 600):"
  echo ""
  read -rp  "  AWS_REGION (ej. us-east-1): " AWS_REGION
  AWS_REGION="${AWS_REGION:-us-east-1}"
  echo ""
  echo "  Si el servidor tiene IAM Role con permisos DynamoDB, deja vacíos los siguientes:"
  read -rsp "  AWS_ACCESS_KEY_ID (opcional si hay IAM Role): " AWS_KEY; echo ""
  read -rsp "  AWS_SECRET_ACCESS_KEY (opcional si hay IAM Role): " AWS_SECRET; echo ""
  read -rsp "  ANTHROPIC_API_KEY (sk-ant-...): " ANT_KEY; echo ""
  read -rp  "  DYNAMO_TABLE (default: hunter_zone_assignments): " DYNAMO_TABLE
  DYNAMO_TABLE="${DYNAMO_TABLE:-hunter_zone_assignments}"
  read -rp  "  CORS origins (default: *): " CORS_ORIGINS
  CORS_ORIGINS="${CORS_ORIGINS:-*}"

  cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
$([ -n "${AWS_KEY:-}" ] && echo "AWS_ACCESS_KEY_ID=${AWS_KEY}" || echo "# AWS_ACCESS_KEY_ID= (usando IAM Role)")
$([ -n "${AWS_SECRET:-}" ] && echo "AWS_SECRET_ACCESS_KEY=${AWS_SECRET}" || echo "# AWS_SECRET_ACCESS_KEY= (usando IAM Role)")
DYNAMO_TABLE=${DYNAMO_TABLE}
ANTHROPIC_API_KEY=${ANT_KEY}
RB_CORS_ORIGINS=${CORS_ORIGINS}
PORT=${PORT}
EOF
  chmod 600 "$ENV_FILE"
  echo "✓ $ENV_FILE creado"
else
  echo "✓ $ENV_FILE ya existe — no se sobreescribe"
fi

# ── 4. Servicio systemd ─────────────────────────────────────────────────
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

# ── 5. Verificar health ─────────────────────────────────────────────────
sleep 2
if curl -sf "http://127.0.0.1:${PORT}/api/health" > /dev/null; then
  echo "✓ Health check OK — el API responde en el puerto $PORT"
else
  echo "✗ El API no responde todavía. Revisa: journalctl -u $SERVICE -n 30"
  exit 1
fi

# ── 6. Snippet nginx ────────────────────────────────────────────────────
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
