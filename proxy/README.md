# CRM49 Proxy (fallback for when the CRM49 firewall blocks the app's IP)

The CRM49 API endpoint (`www.upsideimoveis.com.br`) blocks TCP from non-BR
datacenter IPs. When whitelisting isn't available, this folder ships a
minimal HTTP CONNECT proxy (tinyproxy) that you deploy on a Brazilian VPS
(Oracle Cloud Free Tier São Paulo is ideal — zero cost, permanent).

## Setup on the BR VPS

1. Install Docker + Docker Compose on the VPS.
2. Clone this repo (or just copy the `proxy/` folder).
3. Edit `tinyproxy.conf` — replace `CHANGE-ME-strong-password` on the
   `BasicAuth` line.
4. Open TCP 8888 in the VPS's firewall (Oracle Cloud → Security Lists).
5. `docker compose up -d` inside `proxy/`.
6. Note the public IP of the BR VPS.

## Wire up the main app

On the main app (Coolify), set this env var:

```
CRM49_HTTP_PROXY=http://crm49:<password>@<BR_VPS_PUBLIC_IP>:8888
```

Redeploy. The CRM49Client will route every upstream call through the
proxy automatically; no code change, no other service is affected.

## Verify

From the BR VPS:

```bash
curl -x http://crm49:<password>@127.0.0.1:8888 \
  -H "Authorization: Bearer <TOKEN>" \
  "https://www.upsideimoveis.com.br/crm/api/v1/properties?page=1&per_page=1"
```

Should return JSON with `pagination` and `data`.

Then on the app side (Coolify web terminal):

```bash
python -c "import asyncio; from app.services.property_sync import sync_all_tenants_once; print(asyncio.run(sync_all_tenants_once()))"
```

Should return `synced: 1274`.

## Turning the proxy back off

Once CRM49 whitelists the app server's IP, just unset `CRM49_HTTP_PROXY`
and redeploy. The proxy VPS can stay up (idle, zero cost on Oracle Free
Tier) or be destroyed.
