#!/usr/bin/env bash
# deploy_rb_api.sh — Instala el Route Builder API en el host de la VPC.
# Correr como root (o con sudo) en el servidor donde vive el MCP.
# Uso:  sudo bash deploy_rb_api.sh
set -euo pipefail

APP_DIR=/opt/bizne-rb
ENV_FILE=/etc/bizne-rb.env
REPO_RAW=https://raw.githubusercontent.com/alonso-bizne/bizne-mapa-marketplace/main

echo "== 1/5 Python venv + dependencias =="
mkdir -p "$APP_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q fastapi "uvicorn[standard]" anthropic sqlalchemy psycopg2-binary

echo "== 2/5 Código =="
curl -fsSL "$REPO_RAW/api_server.py" -o "$APP_DIR/api_server.py"

echo "== 3/5 Variables de entorno =="
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# ⚠ COMPLETAR antes de arrancar:
# Usuario de BD con ESCRITURA sobre hunter_zone_assignments (redash_reader es read-only)
DATABASE_URL=postgresql://rb_writer:CAMBIAR_PASSWORD@10.200.20.198:5432/bizne_api20_staging
ANTHROPIC_API_KEY=sk-ant-CAMBIAR
RB_CORS_ORIGINS=https://raw.githack.com,https://alonso-bizne.github.io
PORT=8090
EOF
  chmod 600 "$ENV_FILE"
  echo "   → Edita $ENV_FILE con las credenciales reales y vuelve a correr este script."
  exit 0
fi

echo "== 4/5 systemd =="
cat > /etc/systemd/system/bizne-rb.service <<EOF
[Unit]
Description=Bizne Route Builder API
After=network.target

[Service]
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn api_server:app --host 127.0.0.1 --port 8090
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now bizne-rb
sleep 2
systemctl --no-pager -l status bizne-rb | head -5

echo "== 5/5 Smoke test =="
curl -s localhost:8090/api/health && echo " ← OK"
cat <<'EOF'

Siguientes pasos manuales:
1. En Postgres (una sola vez):
     CREATE USER rb_writer WITH PASSWORD '...';
     GRANT CONNECT ON DATABASE bizne_api20_staging TO rb_writer;
     -- tras el primer arranque del API (crea la tabla):
     GRANT SELECT, INSERT, UPDATE, DELETE ON hunter_zone_assignments TO rb_writer;
     GRANT USAGE, SELECT ON SEQUENCE hunter_zone_assignments_id_seq TO rb_writer;
2. En nginx (host del MCP):
     location /rb/ { proxy_pass http://127.0.0.1:8090/; proxy_set_header Host $host; }
     nginx -t && systemctl reload nginx
3. Probar desde fuera:  curl https://mcp.bizne.com.mx/rb/api/health
4. En GitHub: Settings → Secrets and variables → Actions → Variables →
     RB_API_URL = https://mcp.bizne.com.mx/rb
EOF
