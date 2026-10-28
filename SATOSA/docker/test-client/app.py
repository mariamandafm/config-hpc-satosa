#!/usr/bin/env python3
"""
Cliente OIDC minimo (RP de teste) para exercitar manualmente o fluxo de login do
SATOSA descrito em TESTE_LOGIN_CUID.md. NAO faz parte da arquitetura real do
Core AAI Platform -- e so um app de teste, sem dependencias externas (so a
biblioteca padrao do Python).

Uso:
    python app.py
    # abra http://localhost:9999/ no navegador

O client_id/client_secret/redirect_uri abaixo precisam bater com um client ja
cadastrado no satosa-oidc-frontend, por exemplo:

    docker exec satosa-satosa-1 bash -c '
      satosa-oidc-frontend-clients --db-uri "$OIDC_DB_URI" create testclient testsecret \
        --redirect-uris "http://localhost:9999/callback" \
        --response-types "code" \
        --grant-types-supported "authorization_code" \
        --allowed-scopes "openid,profile,email"
    '
"""
import base64
import hashlib
import html
import http.server
import json
import secrets
import socketserver
import ssl
import urllib.error
import urllib.parse
import urllib.request

SATOSA_BASE = "https://localhost:8000"
CLIENT_ID = "testclient"
CLIENT_SECRET = "testsecret"

# SATOSA agora serve HTTPS com um certificado self-signed (necessário para o
# cookie de estado SameSite=None funcionar em navegadores reais -- ver
# TESTE_LOGIN_CUID.md). As chamadas deste cliente para /OIDC/token e
# /OIDC/userinfo são server-to-server (não passam pelo navegador), então
# desativamos a verificação do certificado só para esse contexto. Isso é
# aceitável apenas porque é um certificado local, gerado por nós mesmos, para
# testes -- nunca fazer isso apontando para um SATOSA de produção.
_INSECURE_SSL_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_INSECURE_SSL_CONTEXT.check_hostname = False
_INSECURE_SSL_CONTEXT.verify_mode = ssl.CERT_NONE
REDIRECT_URI = "http://localhost:9999/callback"
SCOPE = "openid profile email"
PORT = 9999

# code_verifier por "state", em memoria -- processo unico, so para uso local.
_pending = {}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


NODE_NAME = "GidLab Test Node"

# Visual inspirado na tela de login de um "nó" de serviço (ex.: EOSC EU Node):
# painel decorativo à esquerda e conteúdo à direita. Só ilustra qual entidade
# (SP/RP) este cliente representa no ambiente de teste.
STYLE = """
:root {
  --bg: #ffffff; --fg: #0b0b3b; --muted: #4a4d68; --link: #1f4fd8;
  --accent: #1f4fd8; --accent-fg: #ffffff; --border: #c9cde0; --code-bg: #f3f5fb;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
.layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); min-height: 100vh; }
.art {
  position: relative; overflow: hidden;
  background: linear-gradient(105deg, #2b3fd6 0%, #5a78f0 45%, #c9d4ff 75%, #ffffff 100%);
}
.art span {
  position: absolute; border-radius: 22%;
  background: linear-gradient(145deg, rgba(110,135,255,.95), rgba(40,55,190,.85));
  box-shadow: inset 0 0 0 3px rgba(20,35,160,.55), 0 20px 40px rgba(10,20,120,.25);
}
.art span:nth-child(1) { width: 300px; height: 300px; left: -90px; top: -60px; transform: rotate(35deg); }
.art span:nth-child(2) { width: 320px; height: 320px; left: 140px; top: 170px; transform: rotate(-30deg); }
.art span:nth-child(3) { width: 340px; height: 340px; left: -150px; top: 380px; transform: rotate(40deg); }
.art span:nth-child(4) { width: 180px; height: 180px; left: 40px; top: 120px; transform: rotate(15deg); opacity: .6; }
.content { display: flex; flex-direction: column; justify-content: center; padding: 48px 6%; }
.inner { width: 100%; max-width: 640px; margin: 0 auto; }
nav { display: flex; flex-wrap: wrap; gap: 24px; margin-bottom: 56px; }
h1 { font-size: clamp(2.2rem, 4.5vw, 3.6rem); line-height: 1.1; margin: 0 0 20px; letter-spacing: -.01em; }
.lead { font-size: 1.2rem; color: var(--fg); margin: 0 0 44px; max-width: 34em; }
h2 { font-size: 1.6rem; margin: 0 0 6px; }
h3 { font-size: 1.1rem; margin: 32px 0 8px; }
.sub { margin: 0 0 28px; }
.label { font-weight: 600; font-size: .95rem; margin: 0 0 10px; }
.btn {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  width: 100%; max-width: 640px; padding: 14px 18px; border: 1px solid var(--accent);
  border-radius: 4px; background: var(--accent); color: var(--accent-fg); font-weight: 600;
}
.btn:hover { text-decoration: none; filter: brightness(1.08); }
.meta { margin-top: 40px; font-size: .85rem; color: var(--muted); }
.meta code, pre { background: var(--code-bg); border-radius: 4px; }
.meta code { padding: 1px 6px; }
pre { padding: 14px; overflow-x: auto; font-size: .85rem; border: 1px solid var(--border); }
.note { color: var(--muted); font-size: .9rem; }
@media (max-width: 860px) {
  .layout { grid-template-columns: 1fr; }
  .art { height: 140px; }
  .content { padding: 28px 16px; }
  nav { margin-bottom: 32px; }
}
"""


def page(title: str, body: str) -> bytes:
    """Layout comum: painel decorativo + coluna de conteúdo do GidLab Test Node."""
    html_doc = f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {NODE_NAME}</title>
<style>{STYLE}</style></head>
<body><div class="layout">
<div class="art" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
<main class="content"><div class="inner">
{body}
</div></main>
</div></body></html>"""
    return html_doc.encode("utf-8")


def pre_json(data) -> str:
    return f"<pre>{html.escape(json.dumps(data, indent=2, ensure_ascii=False))}</pre>"


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, content: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._handle_index()
        elif parsed.path == "/callback":
            self._handle_callback(urllib.parse.parse_qs(parsed.query))
        else:
            self._send(page("404", "<h2>Página não encontrada</h2><p>Volte para o <a href='/'>início</a>.</p>"), 404)

    def _handle_index(self):
        verifier = secrets.token_urlsafe(48)
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(16)
        _pending[state] = verifier

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{SATOSA_BASE}/OIDC/authorization?{urllib.parse.urlencode(params)}"

        body = f"""
        <h1>{NODE_NAME}</h1>
        <h2>Entrar via Core AAI Platform</h2>
        <p class="label">Use as credenciais da sua instituição de origem para entrar</p>
        <a class="btn" href="{html.escape(auth_url)}">Entrar com sua instituição <span aria-hidden="true">&#8594;</span></a>
        <p class="meta">Serviço de teste (OIDC RP) &middot; client_id <code>{CLIENT_ID}</code>
        &middot; proxy <code>{SATOSA_BASE}</code></p>
        """
        self._send(page("Entrar", body))

    def _handle_callback(self, query):
        if "error" in query:
            body = f"<h2>Erro na autorização</h2>{pre_json(query)}<p><a href='/'>Tentar de novo</a></p>"
            self._send(page("Erro na autorização", body), 400)
            return

        state = query.get("state", [None])[0]
        code = query.get("code", [None])[0]
        verifier = _pending.pop(state, None) if state else None

        if not code or not verifier:
            body = "<h2>Estado inválido</h2><p>state desconhecido/expirado (reiniciou o servidor no meio do fluxo?).</p><p><a href='/'>Começar de novo</a></p>"
            self._send(page("Estado inválido", body), 400)
            return

        token_body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code_verifier": verifier,
        }).encode()

        req = urllib.request.Request(
            f"{SATOSA_BASE}/OIDC/token",
            data=token_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10, context=_INSECURE_SSL_CONTEXT) as resp:
                tokens = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            body = f"<h2>Falha ao trocar o code</h2><p>POST /OIDC/token devolveu {e.code}:</p><pre>{html.escape(detail)}</pre>"
            self._send(page("Falha ao trocar o code", body), 502)
            return
        except Exception as e:
            body = f"<h2>Falha ao trocar o code</h2><pre>{html.escape(str(e))}</pre>"
            self._send(page("Falha ao trocar o code", body), 502)
            return

        id_claims = decode_jwt_payload(tokens["id_token"]) if "id_token" in tokens else {}
        access_claims = decode_jwt_payload(tokens.get("access_token", ""))

        userinfo = None
        if "access_token" in tokens:
            ui_req = urllib.request.Request(
                f"{SATOSA_BASE}/OIDC/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            try:
                with urllib.request.urlopen(ui_req, timeout=10, context=_INSECURE_SSL_CONTEXT) as resp:
                    userinfo = json.loads(resp.read())
            except Exception as e:
                userinfo = {"erro": str(e)}

        name = (userinfo or {}).get("name") or id_claims.get("name") or id_claims.get("sub", "")
        body = f"""
        <h1>Bem-vindo(a) ao {NODE_NAME}</h1>
        <p class="lead">Login concluído{f" como <strong>{html.escape(str(name))}</strong>" if name else ""}.</p>
        <h3>/OIDC/userinfo</h3>
        {pre_json(userinfo)}
        <h3>Claims do id_token</h3>
        {pre_json(id_claims)}
        <h3>Claims do access_token</h3>
        {pre_json(access_claims)}
        <h3>Resposta de /OIDC/token</h3>
        {pre_json(tokens)}
        <p class="note">O CUID em si nao aparece necessariamente em "sub" -- o
        satosa-oidc-frontend pode gerar seu proprio identificador de sessao (pairwise).
        Para confirmar que o CUID foi criado de verdade, veja o banco do cuidapp:<br>
        <code>docker exec cuidapp-postgresql-1 psql -U postgres -c 'SELECT * FROM "CUID";'</code></p>
        <p><a href="/">Fazer login de novo</a></p>
        """
        self._send(page("Login concluído", body))

    def log_message(self, fmt, *args):
        print(f"[test-client] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Cliente de teste OIDC em http://localhost:{PORT}/  (Ctrl+C para parar)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
