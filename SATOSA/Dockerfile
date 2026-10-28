# Contexto de build = diretório PAI deste repositório (core-aai-platform/), não
# SATOSA/ sozinho — precisa alcançar os 4 diretórios-irmãos abaixo. Ver
# docker-compose.yaml (build.context: ..) e PLANO_SATOSA_INSTALACAO.md.
#
# requires-python mais restritivo entre as 5 peças: satosa-micro_services (>=3.9).
FROM python:3.11-slim

# xmlsec1: pysaml2 usa o binário via subprocess, não o binding Python
# (src/saml2/sigver.py: "Based on the use of xmlsec1 binaries and not the python
# xmlsec module."). openssl: gera o certificado do backend SAML no entrypoint.
# gettext-base: fornece o envsubst usado no entrypoint pra renderizar a config.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xmlsec1 openssl gettext-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# --- Camada de protocolo, dos forks GEANT já clonados localmente neste monorepo
# (evita depender de git+ssh://gitlab.geant.org/... durante o build — ver Seção 3
# do plano sobre a troca feita e por quê).
COPY pysaml2/ ./pysaml2/
RUN pip install --no-cache-dir ./pysaml2

COPY idpy-oidc/ ./idpy-oidc/
RUN pip install --no-cache-dir ./idpy-oidc

# --- Núcleo SATOSA (fork GEANT v8.5.1-CoreAAI, mesmo checkout usado pelas análises
# anteriores). pysaml2 já instalado acima satisfaz o "pysaml2>=6.5.1" do SATOSA.
COPY SATOSA/ ./SATOSA/
RUN pip install --no-cache-dir ./SATOSA

# --- Frontend OIDC: --no-deps porque seu setup.py aponta idpyoidc para
# git+ssh://gitlab.geant.org/... (sem credencial disponível no build) — já
# instalamos o mesmo fork acima. Demais dependências soltas, instaladas à parte.
# psycopg2-binary no lugar do extra [postgres] (psycopg2 puro exigiria libpq-dev
# + gcc para compilar; mesma troca já feita no cuidapp).
# sqlalchemy<2.1: a partir da 2.1 o driver padrão de "postgresql://" passou a ser
# o psycopg 3 ("No module named 'psycopg'" no migrate); ficamos no psycopg2.
COPY satosa-oidc-frontend/ ./satosa-oidc-frontend/
RUN pip install --no-cache-dir --no-deps ./satosa-oidc-frontend \
    && pip install --no-cache-dir \
        "cryptojwt==1.8.4" alembic "sqlalchemy>=2.0,<2.1" click tabulate cachetools bcrypt \
        psycopg2-binary

# --- Microserviços CoreAAI: mesmo motivo do --no-deps (pysaml2/SATOSA já
# instalados dos forks locais; aarc-entitlement vem do PyPI normalmente).
#
# O pacote declara "pydantic>1.8.2,<2", mas isso é conservador demais para o único
# plugin usado aqui (CUIDClient): testei importar coreaai_proxy_plugins.satosa.
# micro_services.cuid sob pydantic 2.13.5 (o que a cadeia do SATOSA/pyop/oic already
# resolve) e funciona — só um UserWarning inofensivo ("allow_mutation has been
# removed"). O pacote como um todo é inconsistente (helpers/config.py e
# helpers/iamapi.py usam validator/root_validator, exclusivos do v1;
# attribute_exchange/v2.py usa ConfigDict, exclusivo do v2) — cuid.py não toca nos
# arquivos v1-only de um jeito que quebre, e o pydantic v2 mantém validator/
# pydantic.json como shims de compatibilidade. Forçar pydantic<2 globalmente foi
# o que causava o build falhar (empurra a resolução do pyop/oic pra versões
# antigas cujo PyYAML transitivo não tem wheel pra Python 3.11 e quebra
# compilando do zero) — não vale a pena para o que usamos agora. Se algum dia
# ativarmos um plugin que exija de fato pydantic v1 (algo em helpers/iamapi.py,
# por exemplo), isso precisa ser revisitado.
COPY satosa-micro_services/ ./satosa-micro_services/
RUN pip install --no-cache-dir --no-deps ./satosa-micro_services \
    && pip install --no-cache-dir \
        "aarc-entitlement>=1.0.5" ldap3 cryptojwt requests validators toolz \
        requests-toolbelt "PyJWT>=2.0.0,<3.0.0" "typing_extensions>=4.0.0,<5.0.0"

COPY SATOSA/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Config montada via bind mount (docker-compose.yaml) em vez de embutida na imagem —
# SATOSA_CONFIG e todos os caminhos relativos dentro de proxy_conf.yaml (chaves,
# certificados, metadata, arquivos de plugin) resolvem a partir daqui.
WORKDIR /opt/satosa/etc
ENV SATOSA_CONFIG=/opt/satosa/etc/proxy_conf.yaml

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
