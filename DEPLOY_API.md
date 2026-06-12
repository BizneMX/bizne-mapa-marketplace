# Deployment del API server (Route Builder)

`api_server.py` da servicio al Route Builder de `staging.html`:

| Endpoint | Función |
|---|---|
| `GET /api/assignments` | Lee asignaciones de `hunter_zone_assignments` |
| `POST /api/assignments` | Guarda el snapshot de asignaciones (reemplaza el vigente) |
| `POST /api/chat` | Chat con Claude (`claude-haiku-4-5`) con contexto del mapa |
| `GET /api/health` | Health check |

## Dónde correrlo

**Dentro de la VPC** — la BD (`10.200.20.198:5432`) no es pública. El candidato natural
es el mismo host donde corre el MCP (`mcp.bizne.com.mx`), exponiendo el API por el
mismo nginx con un subpath o subdominio (p.ej. `https://rutas.bizne.com.mx`).

Si el API no está desplegado, el mapa funciona igual: guarda en `localStorage`
y exporta CSV. El chat sí requiere el API.

## Instalación

```bash
# En el host (dentro de la VPC)
python3 -m venv /opt/bizne-rb && source /opt/bizne-rb/bin/activate
pip install fastapi "uvicorn[standard]" anthropic sqlalchemy psycopg2-binary

# Variables de entorno (en /etc/bizne-rb.env, chmod 600)
DATABASE_URL=postgresql://USUARIO_ESCRITURA:PASSWORD@10.200.20.198:5432/bizne_api20_staging
ANTHROPIC_API_KEY=sk-ant-...
RB_CORS_ORIGINS=https://raw.githack.com,https://alonso-bizne.github.io
PORT=8090
```

⚠ **Usuario de BD:** `redash_reader` (el del MCP) es read-only. Crea/usa un usuario con
permisos de escritura **solo** sobre `hunter_zone_assignments`:

```sql
CREATE USER rb_writer WITH PASSWORD '...';
GRANT CONNECT ON DATABASE bizne_api20_staging TO rb_writer;
-- La tabla la crea el API en el primer arranque (CREATE TABLE IF NOT EXISTS);
-- si prefieres crearla a mano, corre el DDL de api_server.py y luego:
GRANT SELECT, INSERT, UPDATE, DELETE ON hunter_zone_assignments TO rb_writer;
GRANT USAGE, SELECT ON SEQUENCE hunter_zone_assignments_id_seq TO rb_writer;
```

## systemd

```ini
# /etc/systemd/system/bizne-rb.service
[Unit]
Description=Bizne Route Builder API
After=network.target

[Service]
EnvironmentFile=/etc/bizne-rb.env
ExecStart=/opt/bizne-rb/bin/uvicorn api_server:app --host 127.0.0.1 --port 8090
WorkingDirectory=/opt/bizne-mapa   # carpeta con api_server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now bizne-rb
curl -s localhost:8090/api/health   # → {"ok":true}
```

## nginx (mismo host del MCP)

```nginx
location /rb/ {
    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host $host;
}
```

→ La URL pública queda `https://mcp.bizne.com.mx/rb` (los endpoints en `/rb/api/...`).

## Conectar el mapa al API

Dos opciones (pueden coexistir):

1. **Por build:** define la variable de repositorio `RB_API_URL` en GitHub
   (Settings → Secrets and variables → Actions → Variables) con la URL pública,
   y la siguiente corrida de "Build Mapa Staging" la deja embebida en `staging.html`.
2. **Por navegador:** abre el Route Builder (botón 🗺), clic en ⚙ y pega la URL.
   Se guarda en `localStorage` de ese navegador.

## Verificación end-to-end

```bash
API=https://mcp.bizne.com.mx/rb
curl -s $API/api/health
curl -s $API/api/assignments
curl -s -X POST $API/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"¿Qué hunter tiene más zonas asignadas?","context":{"hunters":["Anel"],"asignaciones":{},"zonas":[]}}'
```
