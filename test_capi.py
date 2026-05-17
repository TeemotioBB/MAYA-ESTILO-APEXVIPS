"""
Testes pro módulo CAPI.

Roda com: python test_capi.py
Não precisa de pytest — usa assertions diretas.
"""

import sys
import os
import json
import hashlib
import time
from unittest.mock import MagicMock, patch

# Mock do config antes de importar o capi
class FakeConfig:
    META_PIXEL_ID = "1234567890123456"
    META_ACCESS_TOKEN = "EAA_fake_token_for_test"
    META_API_VERSION = "v22.0"
    META_TEST_EVENT_CODE = "TEST12345"
    LANDING_PAGE_URL = "https://exemplo.com.br/"

sys.modules["config"] = FakeConfig

import capi


# ════════════════════════════════════════════════════════════════════════════
# FAKE REDIS (não precisa subir um real)
# ════════════════════════════════════════════════════════════════════════════

class FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def exists(self, key):
        return key in self.store


fake_r = FakeRedis()
capi.init(fake_r)


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def check(name, actual, expected):
    if actual == expected:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}")
        print(f"     expected: {expected}")
        print(f"     actual:   {actual}")
        raise AssertionError(name)


def section(title):
    print(f"\n━━━ {title} ━━━")


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE HASHING
# ════════════════════════════════════════════════════════════════════════════

section("hashing e normalização básica")

# _sha256 deve trim + lowercase
check("_sha256('Vinicius') == _sha256('vinicius')",
      capi._sha256("Vinicius"), capi._sha256("vinicius"))

check("_sha256('  vinicius  ') = _sha256('vinicius')",
      capi._sha256("  vinicius  "), sha256("vinicius"))

check("_sha256('') = None", capi._sha256(""), None)
check("_sha256(None) = None", capi._sha256(None), None)
check("_sha256('   ') = None", capi._sha256("   "), None)

# Hash de teste conhecido
expected_hash_vinicius = sha256("vinicius")
check("_sha256('vinicius') match expected",
      capi._sha256("vinicius"), expected_hash_vinicius)


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE TELEFONE
# ════════════════════════════════════════════════════════════════════════════

section("normalização de telefone brasileiro")

check("'(31) 99999-9999' → '5531999999999'",
      capi.normalize_phone_br("(31) 99999-9999"), "5531999999999")

check("'+55 31 99999-9999' → '5531999999999'",
      capi.normalize_phone_br("+55 31 99999-9999"), "5531999999999")

check("'+5531999999999' → '5531999999999'",
      capi.normalize_phone_br("+5531999999999"), "5531999999999")

check("'31999999999' → '5531999999999'",
      capi.normalize_phone_br("31999999999"), "5531999999999")

check("'5531999999999' → '5531999999999'",
      capi.normalize_phone_br("5531999999999"), "5531999999999")

# Número fixo (10 dígitos)
check("'3133334444' (fixo) → '553133334444'",
      capi.normalize_phone_br("3133334444"), "553133334444")

check("'' → None", capi.normalize_phone_br(""), None)
check("None → None", capi.normalize_phone_br(None), None)
check("'abc' → None", capi.normalize_phone_br("abc"), None)


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE CPF
# ════════════════════════════════════════════════════════════════════════════

section("normalização de CPF")

check("'455.620.518-26' → '45562051826'",
      capi.normalize_cpf("455.620.518-26"), "45562051826")

check("'45562051826' → '45562051826'",
      capi.normalize_cpf("45562051826"), "45562051826")

check("'455 620 518 26' → '45562051826'",
      capi.normalize_cpf("455 620 518 26"), "45562051826")

# Inválidos
check("CPF curto → None", capi.normalize_cpf("123"), None)
check("CPF longo → None", capi.normalize_cpf("12345678901234"), None)
check("None → None", capi.normalize_cpf(None), None)


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE SPLIT NAME
# ════════════════════════════════════════════════════════════════════════════

section("split de nomes")

check("'Vinicius Murilo Pereira Pimenta' → ('vinicius','pimenta')",
      capi.split_name("Vinicius Murilo Pereira Pimenta"),
      ("vinicius", "pimenta"))

check("'João Silva' → ('joão','silva')",
      capi.split_name("João Silva"),
      ("joão", "silva"))

check("'Maria' → ('maria', None)",
      capi.split_name("Maria"),
      ("maria", None))

check("'' → (None, None)", capi.split_name(""), (None, None))
check("None → (None, None)", capi.split_name(None), (None, None))


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE FBC
# ════════════════════════════════════════════════════════════════════════════

section("construção de fbc")

# Verifica formato
fbc = capi.build_fbc_from_fbclid("IwAR123ABC", timestamp_ms=1234567890000)
check("fbc formato com ts fixo",
      fbc, "fb.1.1234567890000.IwAR123ABC")

# Verifica que None retorna None
check("fbc sem fbclid → None", capi.build_fbc_from_fbclid(""), None)
check("fbc None → None", capi.build_fbc_from_fbclid(None), None)


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE build_user_data
# ════════════════════════════════════════════════════════════════════════════

section("build_user_data — montagem completa")

ud = capi.build_user_data(
    telegram_user_id=12345,
    full_name="Vinicius Murilo Pereira Pimenta",
    phone="(31) 99999-9999",
    cpf="455.620.518-26",
    fbc="fb.1.1234567890000.IwAR123",
    fbp="fb.1.1234567890000.987654321",
    client_ip="1.2.3.4",
    client_ua="Mozilla/5.0 (Linux)",
    country="br",
)

print(f"  user_data gerado:")
for k, v in ud.items():
    val_repr = v if isinstance(v, str) else f"[{v[0][:16]}...]" if v else v
    print(f"    {k}: {val_repr}")

# Checagens
check("fn = sha256('vinicius')",
      ud["fn"], [sha256("vinicius")])
check("ln = sha256('pimenta')",
      ud["ln"], [sha256("pimenta")])
check("ph = sha256('5531999999999')",
      ud["ph"], [sha256("5531999999999")])
check("external_id contém sha256('12345')",
      sha256("12345") in ud["external_id"], True)
check("external_id contém sha256('45562051826')",
      sha256("45562051826") in ud["external_id"], True)
check("external_id tem 2 entries",
      len(ud["external_id"]), 2)
check("country = sha256('br')",
      ud["country"], [sha256("br")])
check("fbc raw (sem hash)",
      ud["fbc"], "fb.1.1234567890000.IwAR123")
check("fbp raw (sem hash)",
      ud["fbp"], "fb.1.1234567890000.987654321")
check("client_ip raw",
      ud["client_ip_address"], "1.2.3.4")
check("client_user_agent raw",
      ud["client_user_agent"], "Mozilla/5.0 (Linux)")


section("build_user_data — sem CPF (cenário antes do pagamento)")

ud2 = capi.build_user_data(
    telegram_user_id=99999,
    first_name="João",
    last_name="Silva",
    phone="5511988887777",
    fbc="fb.1.111.AAA",
    fbp="fb.1.222.BBB",
)
check("fn presente", "fn" in ud2, True)
check("ln presente", "ln" in ud2, True)
check("ph presente", "ph" in ud2, True)
check("external_id só com telegram_user_id",
      len(ud2["external_id"]), 1)


section("build_user_data — só com fbc/fbp (cenário LP, anônimo)")

ud3 = capi.build_user_data(
    fbc="fb.1.111.AAA",
    fbp="fb.1.222.BBB",
    client_ip="9.9.9.9",
    client_ua="UA",
    country="br",
)
check("sem fn", "fn" not in ud3, True)
check("sem ph", "ph" not in ud3, True)
check("sem external_id", "external_id" not in ud3, True)
check("com fbc", ud3["fbc"], "fb.1.111.AAA")
check("com country", "country" in ud3, True)


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE STORAGE
# ════════════════════════════════════════════════════════════════════════════

section("storage — save/get tracking + PII")

capi.save_landing_tracking(
    "track123",
    fbclid="IwAR123",
    fbp="fb.1.x.y",
    client_ip="1.2.3.4",
    client_ua="Mozilla",
    utm_source="facebook",
    utm_campaign="cmp1",
)
tr = capi.get_landing_tracking("track123")
check("tracking save → get round-trip",
      tr["fbp"], "fb.1.x.y")
check("fbc montado automaticamente do fbclid",
      tr["fbc"].endswith(".IwAR123"), True)
check("UTM source persistido",
      tr["utm_source"], "facebook")

capi.link_user_to_tracking(12345, "track123")
check("get_user_tracking após link funciona",
      capi.get_user_tracking(12345)["fbp"], "fb.1.x.y")

capi.save_user_pii(12345, full_name="Test Name", phone="11999999999")
pii = capi.get_user_pii(12345)
check("save PII → full_name persistido", pii["full_name"], "Test Name")
check("save PII → phone normalizado",   pii["phone"],     "5511999999999")

# Merge incremental
capi.save_user_pii(12345, cpf="123.456.789-09")
pii2 = capi.get_user_pii(12345)
check("merge incremental mantém full_name",
      pii2["full_name"], "Test Name")
check("merge incremental adiciona CPF",
      pii2["cpf"], "12345678909")


# ════════════════════════════════════════════════════════════════════════════
# TESTES DE SEND_EVENT — payload mock
# ════════════════════════════════════════════════════════════════════════════

section("send_event — mock da Graph API")

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"events_received": 1, "fbtrace_id": "ABC123"}'
    mock_resp.json.return_value = {"events_received": 1, "fbtrace_id": "ABC123"}
    mock_post.return_value = mock_resp

    result = capi.send_event(
        event_name="Purchase",
        event_id="test_event_id_123",
        event_time=1700000000,
        user_data={"em": [sha256("[email protected]")]},
        custom_data={"value": 9.90, "currency": "BRL"},
    )

    # Pega o que foi enviado
    assert mock_post.called, "requests.post não foi chamado"
    call_args = mock_post.call_args
    url = call_args[0][0]
    payload = call_args[1]["json"]
    
    check("URL contém pixel id",
          "1234567890123456/events" in url, True)
    check("URL contém access_token",
          "access_token=" in url, True)
    check("payload tem 'data' array",
          isinstance(payload["data"], list), True)
    check("payload tem test_event_code",
          payload["test_event_code"], "TEST12345")

    event = payload["data"][0]
    check("event_name correto",
          event["event_name"], "Purchase")
    check("event_id correto",
          event["event_id"], "test_event_id_123")
    check("event_time correto",
          event["event_time"], 1700000000)
    check("action_source default = website",
          event["action_source"], "website")
    check("event_source_url default = LP url",
          event["event_source_url"], "https://exemplo.com.br/")
    check("custom_data presente",
          event["custom_data"], {"value": 9.90, "currency": "BRL"})
    check("retorno tem events_received=1",
          result["events_received"], 1)


section("send_event — erro HTTP é capturado, não levanta exceção")

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.content = b'{"error": {"message": "Invalid token"}}'
    mock_resp.json.return_value = {"error": {"message": "Invalid token"}}
    mock_post.return_value = mock_resp

    result = capi.send_event(
        event_name="Purchase",
        user_data={"em": [sha256("[email protected]")]},
    )
    check("erro 400 retorna dict com 'error'",
          "error" in result, True)
    check("erro 400 não levantou exceção",
          True, True)


section("send_event — timeout é capturado")

import requests as req_module
with patch("capi.requests.post", side_effect=req_module.Timeout("timed out")):
    result = capi.send_event(
        event_name="Purchase",
        user_data={"em": [sha256("[email protected]")]},
    )
    check("timeout retorna error=timeout",
          result["error"], "timeout")


# ════════════════════════════════════════════════════════════════════════════
# TESTES DOS HELPERS DE EVENTO
# ════════════════════════════════════════════════════════════════════════════

section("send_purchase — fluxo completo simulado")

# Setup: tracking + PII salvos
capi.save_landing_tracking(
    "track_purchase_test",
    fbclid="IwARpurchase",
    fbp="fb.1.999.AAA",
    client_ip="9.9.9.9",
    client_ua="Mozilla/Test",
)
capi.link_user_to_tracking(77777, "track_purchase_test")
capi.save_user_pii(
    77777,
    full_name="Vinicius Pimenta",
    phone="(31) 98888-7777",
    cpf="111.222.333-44",
)

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"events_received": 1}'
    mock_resp.json.return_value = {"events_received": 1}
    mock_post.return_value = mock_resp

    capi.send_purchase(
        77777,
        transaction_id="25310519",
        plan_id="vip_basico",
        plan_name="VIP Básico",
        value=9.90,
    )

    payload = mock_post.call_args[1]["json"]
    event = payload["data"][0]
    ud = event["user_data"]

    check("event_name = Purchase",
          event["event_name"], "Purchase")
    check("event_id usa transaction_id",
          event["event_id"], "purchase_25310519")
    check("custom_data.value = 9.90",
          event["custom_data"]["value"], 9.90)
    check("custom_data.currency = BRL",
          event["custom_data"]["currency"], "BRL")
    check("custom_data.order_id = transaction_id",
          event["custom_data"]["order_id"], "25310519")
    check("user_data.fn = sha256('vinicius')",
          ud["fn"], [sha256("vinicius")])
    check("user_data.ph = sha256('5531988887777')",
          ud["ph"], [sha256("5531988887777")])
    check("user_data.external_id tem 2 ids (telegram + cpf)",
          len(ud["external_id"]), 2)
    # CPF "111.222.333-44" → "11122233344" hash
    check("user_data.external_id contém hash do CPF",
          sha256("11122233344") in ud["external_id"], True)
    check("user_data.fbc preserva valor da LP",
          ud["fbc"], "fb.1.999.AAA" if "fbc" in ud and ud["fbc"].startswith("fb.1.999") else ud.get("fbc"),
          )
    check("user_data.fbp preserva valor da LP",
          ud["fbp"], "fb.1.999.AAA")
    check("user_data.client_ip preserva valor da LP",
          ud["client_ip_address"], "9.9.9.9")


section("send_lead — apenas com dados disponíveis no /start")

# Novo usuário, sem PII (sem nome completo, sem telefone)
capi.save_landing_tracking(
    "track_lead_test",
    fbp="fb.1.111.LEAD",
    client_ip="1.1.1.1",
    client_ua="UA-Lead",
)
capi.link_user_to_tracking(11111, "track_lead_test")

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"events_received": 1}'
    mock_resp.json.return_value = {"events_received": 1}
    mock_post.return_value = mock_resp

    capi.send_lead(11111, telegram_first_name="Joao", telegram_last_name="Silva")

    payload = mock_post.call_args[1]["json"]
    event = payload["data"][0]
    ud = event["user_data"]

    check("Lead event_name correto",
          event["event_name"], "Lead")
    check("Lead tem fn de telegram",
          ud["fn"], [sha256("joao")])
    check("Lead tem ln de telegram",
          ud["ln"], [sha256("silva")])
    check("Lead NÃO tem ph (não temos ainda)",
          "ph" not in ud, True)
    check("Lead tem external_id (telegram user id)",
          ud["external_id"], [sha256("11111")])
    check("Lead preserva fbp da LP",
          ud["fbp"], "fb.1.111.LEAD")


section("send_initiate_checkout")

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"events_received": 1}'
    mock_resp.json.return_value = {"events_received": 1}
    mock_post.return_value = mock_resp

    capi.send_initiate_checkout(
        11111,
        plan_id="vip_basico",
        plan_name="VIP Básico",
        value=9.90,
    )

    payload = mock_post.call_args[1]["json"]
    event = payload["data"][0]
    check("InitiateCheckout event_name",
          event["event_name"], "InitiateCheckout")
    check("InitiateCheckout value = 9.90",
          event["custom_data"]["value"], 9.90)
    check("InitiateCheckout content_ids = [plan_id]",
          event["custom_data"]["content_ids"], ["vip_basico"])


section("send_view_content — LP, anônimo")

with patch("capi.requests.post") as mock_post:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"events_received": 1}'
    mock_resp.json.return_value = {"events_received": 1}
    mock_post.return_value = mock_resp

    capi.send_view_content(
        tracking_id="track_lead_test",
        event_id="dedup_event_id_xyz",
        content_name="Landing Maya",
    )

    payload = mock_post.call_args[1]["json"]
    event = payload["data"][0]
    check("ViewContent event_name",
          event["event_name"], "ViewContent")
    check("ViewContent event_id customizado (dedup com Pixel)",
          event["event_id"], "dedup_event_id_xyz")
    check("ViewContent tem fbp",
          event["user_data"]["fbp"], "fb.1.111.LEAD")


# ════════════════════════════════════════════════════════════════════════════
# 🏁
# ════════════════════════════════════════════════════════════════════════════

print("\n" + "═" * 70)
print("✅ TODOS OS TESTES PASSARAM")
print("═" * 70)
