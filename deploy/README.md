# Hosted deployment — HTTPS migration

Runbook for putting Syndrix behind HTTPS on `195.35.14.71`. Written for whoever
administers the server; steps 1–5 are all on the VPS, step 6 is in Podio's web UI.

**Why:** Podio's OAuth refuses plain-HTTP callbacks on a raw IP, so both
"Connect Podio" and "Connect Files" fail on the current `http://195.35.14.71:*`
setup. HTTPS is the only fix — there is no override flag.

**Scope:** the MyCase Agent is unaffected and needs no changes. Its token refresh
sends no `redirect_uri`, and its API calls go to `external-integrations.mycase.com`,
so the origin change is invisible to it.

---

## 1. Install Caddy and drop in the config

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile   # syntax check before reloading
sudo systemctl reload caddy
```

Caddy requests certificates for both hostnames on first request. Watch it happen:

```bash
sudo journalctl -u caddy -f
```

## 2. Rebuild the frontend image — a restart is NOT enough

`NEXT_PUBLIC_API_URL` is a Next.js *public* env var: its value is **inlined into
the JavaScript bundle at build time**. Changing it in `.env` or the compose file
and restarting the container has no effect — the old URL is already baked into
the shipped JS.

Rebuild with:

```
NEXT_PUBLIC_API_URL=https://api.195.35.14.71.nip.io
```

This is the single most commonly missed step. If the app loads but every API call
still points at `http://195.35.14.71:8100`, this is why.

## 3. Backend `.env`

```
CORS_ORIGINS=https://app.195.35.14.71.nip.io
```

Then restart the backend.

Without this, every request dies on the CORS preflight. The browser reports only
a generic **"Failed to fetch"** — the actual `400` on the `OPTIONS` request is
visible only in DevTools → Network. `get_cors_origins()` in `app/config.py`
always allows `http://localhost:3000` and nothing else unless listed here.

## 4. Close the plain-HTTP ports

Bind 3100/8100 to `127.0.0.1` in the compose file, or firewall them:

```bash
sudo ufw deny 3100
sudo ufw deny 8100
```

If `http://195.35.14.71:3100` stays reachable, someone will use it and hit the
exact same OAuth failures with no obvious explanation. It also leaves the
`crypto.subtle` secure-context problems in play on that origin.

## 5. Verify

```bash
curl -sI https://api.195.35.14.71.nip.io/health     # 200, valid cert
curl -sI https://app.195.35.14.71.nip.io           # 200, valid cert
```

## 6. Update the Podio API key (podio.com/settings/api)

Edit the **existing** key — one key still covers both connections:

| Field  | Value                      |
|--------|----------------------------|
| Domain | `api.195.35.14.71.nip.io`  |

The `api` subdomain, because the REST OAuth callback is a backend route. The
hosted-MCP connection is unaffected by this field.

---

## Certificate risk worth knowing up front

`nip.io` is **not** on the Public Suffix List, so every `nip.io` certificate
worldwide shares one Let's Encrypt rate-limit bucket. Issuance intermittently
fails with:

```
too many certificates already issued for: nip.io
```

If that happens, either:

- swap both hostnames to **`sslip.io`** (same wildcard-DNS trick — nip.io is now
  folded into sslip.io), or
- register a cheap real domain, which removes the risk permanently and is the
  better long-term answer anyway.

Only the two hostnames in the Caddyfile and the values in steps 2, 3 and 6 need
to change.

---

## Post-migration check order

Run these in this order — it isolates cause if something breaks:

1. Log in at `https://app.195.35.14.71.nip.io` → confirms the rebuild + CORS
2. **Run a MyCase Agent query** → confirms nothing regressed, *before* touching Podio
3. Connect Podio → the HTTPS error should be gone
4. Connect Files → the domain-mismatch error should be gone

## Known gap (not triggered by this migration)

If MyCase is ever disconnected and reconnected, its connect flow still points at
the old origin via two settings: `mycase_redirect_uri` (editable in Settings →
MyCase, and must also match what's registered in the MyCase developer portal)
and `mycase_frontend_redirect` (**no Settings UI field**, falls back to
`http://localhost:3000/dashboard/mycase-agent`).

Existing MyCase tokens are unaffected, so there is nothing to do now. This only
matters at reconnect time.
