#!/usr/bin/env bash
set -euo pipefail

TEMPLATE_DIR=/opt/satosa/etc-src
CONFIG_DIR=/opt/satosa/etc

# O compose monta a config do host (SATOSA/docker/etc) como somente-leitura em
# $TEMPLATE_DIR; copiamos pra $CONFIG_DIR (efêmero, dentro do container) antes de
# aplicar envsubst — nunca sobrescrevemos o template original do host.
echo "Preparando configuracao..."
mkdir -p "$CONFIG_DIR"
cp -a "$TEMPLATE_DIR/." "$CONFIG_DIR/"

export BASE STATE_ENCRYPTION_KEY OIDC_DB_URI OIDC_TOKEN_ENCRYPTION_PASSWORD CUID_ACCESS_TOKEN \
  PERUN_API_URL PERUN_API_USER PERUN_API_PASSWORD PERUN_VO_ID
TEMPLATE_VARS='$BASE $STATE_ENCRYPTION_KEY $OIDC_DB_URI $OIDC_TOKEN_ENCRYPTION_PASSWORD $CUID_ACCESS_TOKEN $PERUN_API_URL $PERUN_API_USER $PERUN_API_PASSWORD $PERUN_VO_ID'

# CAs extras (ex.: CA local do Perun em docker/etc/certs/*.pem). Os plugins usam
# requests, que ignora o trust store do sistema e só lê o bundle do certifi — por
# isso o bundle combinado + REQUESTS_CA_BUNDLE, em vez de update-ca-certificates.
if compgen -G "$CONFIG_DIR/certs/*.pem" > /dev/null; then
  CA_BUNDLE=/opt/satosa/ca-bundle.pem
  cat "$(python -c 'import certifi; print(certifi.where())')" "$CONFIG_DIR"/certs/*.pem > "$CA_BUNDLE"
  export REQUESTS_CA_BUNDLE="$CA_BUNDLE" SSL_CERT_FILE="$CA_BUNDLE"
fi
find "$CONFIG_DIR" -name "*.yaml" -print0 | while IFS= read -r -d '' f; do
  envsubst "$TEMPLATE_VARS" < "$f" > "$f.rendered"
  mv "$f.rendered" "$f"
done

# Chaves persistidas em $KEYS_DIR (bind mount em docker-compose.yaml). O certificado
# do backend SAML fica registrado no metadata da federação: se ele mudar, os IdPs
# continuam cifrando asserções para a chave antiga e o login falha com
# "oaep decoding error". Só gera um par novo se ainda não existir nenhum.
KEYS_DIR=/opt/satosa/keys
mkdir -p "$KEYS_DIR"

# Certificado do backend SAML — self-signed.
if [[ ! -f "$KEYS_DIR/backend.key" || ! -f "$KEYS_DIR/backend.crt" ]]; then
  echo "Gerando certificado self-signed do backend SAML (atualize o registro do SP na federação)..."
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEYS_DIR/backend.key" -out "$KEYS_DIR/backend.crt" \
    -days 3650 -subj "/CN=satosa-backend-local" 2>/dev/null
fi
cp "$KEYS_DIR/backend.key" "$KEYS_DIR/backend.crt" "$CONFIG_DIR/"

# Certificado do frontend SAML (IdP-proxy) — self-signed, separado do backend. Vai
# no metadata do IdP-proxy entregue aos SPs: se mudar, os SPs precisam do metadata novo.
if [[ ! -f "$KEYS_DIR/frontend.key" || ! -f "$KEYS_DIR/frontend.crt" ]]; then
  echo "Gerando certificado self-signed do frontend SAML (redistribua o metadata aos SPs)..."
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEYS_DIR/frontend.key" -out "$KEYS_DIR/frontend.crt" \
    -days 3650 -subj "/CN=satosa-frontend-local" 2>/dev/null
fi
cp "$KEYS_DIR/frontend.key" "$KEYS_DIR/frontend.crt" "$CONFIG_DIR/"

# Certificado TLS pra terminação HTTPS do próprio gunicorn — separado do cert do
# backend SAML acima (aquele assina/decifra SAML, este só serve HTTPS pro
# navegador). Necessário porque satosa/state.py usa SameSite=None por padrão no
# cookie de estado, e todo navegador moderno rejeita SameSite=None sem Secure —
# e Secure exige HTTPS de verdade, mesmo autoassinado (curl não aplica essa
# regra, por isso passou despercebido testando só com curl). Ver
# TESTE_LOGIN_CUID.md.
if [[ ! -f "$KEYS_DIR/tls.key" || ! -f "$KEYS_DIR/tls.crt" ]]; then
  echo "Gerando certificado TLS self-signed para o gunicorn..."
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEYS_DIR/tls.key" -out "$KEYS_DIR/tls.crt" \
    -days 3650 -subj "/CN=localhost" 2>/dev/null
fi
cp "$KEYS_DIR/tls.key" "$KEYS_DIR/tls.crt" "$CONFIG_DIR/"

if [[ "${OIDC_DB_URI:-}" == postgresql* ]]; then
  echo "Aguardando banco de dados do frontend OIDC..."
  python - <<'PY'
import os
import sys
import time

import psycopg2

url = os.environ["OIDC_DB_URI"]
for attempt in range(30):
    try:
        psycopg2.connect(url).close()
        break
    except psycopg2.OperationalError as exc:
        last_error = exc
        time.sleep(1)
else:
    sys.exit(f"Banco de dados do frontend OIDC inacessivel apos 30s: {last_error}")
PY

  echo "Aplicando migracoes do frontend OIDC (satosa-oidc-frontend-db)..."
  satosa-oidc-frontend-db --db-uri "$OIDC_DB_URI" migrate
fi

echo "Iniciando SATOSA..."
exec gunicorn --bind 0.0.0.0:8000 --chdir "$CONFIG_DIR" \
  --certfile "$CONFIG_DIR/tls.crt" --keyfile "$CONFIG_DIR/tls.key" \
  satosa.wsgi:app
