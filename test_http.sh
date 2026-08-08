#!/bin/bash
export DB_PATH="$(mktemp -d)/regole.db" ADMIN_ACCESS_CODE="codice-di-collaudo-lungo"
export BASE_URL="http://127.0.0.1:3299" GITHUB_CLIENT_ID="Iv1.finto" GITHUB_CLIENT_SECRET="finto" \
       ALLOWED_GITHUB_LOGIN="alcor6502" JWT_SIGNING_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
       PORT=3299 ANTHROPIC_CIDR=""
python3 server.py > /tmp/srv.log 2>&1 & SRV=$!
for i in $(seq 1 40); do curl -so /dev/null http://127.0.0.1:3299/.well-known/oauth-authorization-server && break; sleep 0.25; done

pass=0; fail=0
chk() { if eval "$2"; then echo "  OK   $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }

PRM=$(curl -s http://127.0.0.1:3299/.well-known/oauth-protected-resource/mcp)
chk "RFC9728 protected-resource per /mcp" '[ -n "$PRM" ] && echo "$PRM" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"resource\"].endswith(\"/mcp\"); assert d[\"authorization_servers\"]" 2>/dev/null'
AS=$(curl -s http://127.0.0.1:3299/.well-known/oauth-authorization-server)
chk "RFC8414 metadata AS" 'echo "$AS" | python3 -c "import json,sys; d=json.load(sys.stdin); assert \"S256\" in d[\"code_challenge_methods_supported\"]; assert d[\"registration_endpoint\"]; assert d[\"authorization_endpoint\"]; assert d[\"token_endpoint\"]" 2>/dev/null'
REG=$(curl -s -X POST http://127.0.0.1:3299/register -H 'Content-Type: application/json' -d '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"],"client_name":"claude","token_endpoint_auth_method":"none","grant_types":["authorization_code","refresh_token"],"response_types":["code"],"scope":"user"}')
CID=$(echo "$REG" | python3 -c "import json,sys;print(json.load(sys.stdin).get('client_id',''))" 2>/dev/null)
chk "DCR /register emette client_id" '[ -n "$CID" ]'
CH=$(python3 - <<'PY'
import base64,hashlib
v=b"a"*43
print(base64.urlsafe_b64encode(hashlib.sha256(v).digest()).rstrip(b"=").decode())
PY
)
LOC=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://127.0.0.1:3299/authorize?client_id=$CID&response_type=code&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback&code_challenge=$CH&code_challenge_method=S256&state=xyz&scope=user")
chk "/authorize avvia il giro verso GitHub" 'echo "$LOC" | grep -qE "^30[27] .*(github\.com|/consent)"'
TOK=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:3299/token -H 'Content-Type: application/x-www-form-urlencoded' -d "grant_type=authorization_code&code=finto&client_id=$CID&code_verifier=aaa")
chk "/token accetta form-urlencoded (niente 415)" '[ "$TOK" != "415" ] && [ "$TOK" -lt 500 ]'
M=$(curl -s -D- -o /dev/null -X POST http://127.0.0.1:3299/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
chk "/mcp senza token: 401 + WWW-Authenticate resource_metadata" 'echo "$M" | head -1 | grep -q 401 && echo "$M" | grep -qi "www-authenticate:.*resource_metadata"'
B=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:3299/mcp -H 'Authorization: Bearer token-inventato' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
chk "/mcp con token falso: respinto (401)" '[ "$B" = "401" ]'

kill $SRV 2>/dev/null
echo "== HTTP: $pass OK, $fail FAIL =="
[ $fail -eq 0 ]
