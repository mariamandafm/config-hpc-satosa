# Mapa de atributos (NameFormat uri) do frontend SAML: o padrão do pysaml2 + nomes extras.
#
# Carregado via attribute_map_dir em plugins/frontends/saml2_frontend.yaml. O filtro do
# frontend (satosa/frontends/saml2.py, _get_approved_attributes) só libera atributos cujo
# nome existe neste mapa — um nome desconhecido é descartado mesmo com
# attribute_restrictions: null.
#
# Com attribute_map_dir o pysaml2 carrega SÓ os mapas deste diretório, e o filtro usa só o
# primeiro conversor uri — por isso este arquivo estende o saml_uri padrão em vez de
# definir um mapa separado. O padrão é importado como módulo (não como dict) para o
# pysaml2 não registrá-lo uma segunda vez.
from saml2.attributemaps import saml_uri as _default

EXTRA = {
    # Esperado pelo SP do Perun (signup.local): entityID do IdP de origem.
    "urn:cesnet:proxyidp:attribute:sourceIdPEntityID": "sourceIdPEntityID",
}

MAP = {
    "identifier": _default.MAP["identifier"],
    "fro": {**_default.MAP["fro"], **EXTRA},
    "to": {**_default.MAP["to"], **{local: uri for uri, local in EXTRA.items()}},
}
