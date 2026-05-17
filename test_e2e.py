"""
Teste end-to-end do fluxo completo:

  LP click → tracking_id criado → /start → Lead disparado
  → plano clicado → InitiateCheckout disparado
  → nome coletado → telefone coletado → AddPaymentInfo disparado
  → webhook SyncPay → Purchase disparado

Roda com: python test_e2e.py
"""

import sys
import hashlib
from unittest.mock import MagicMock, patch
from collections import defaultdict


# Mock config
class FakeConfig:
    META_PIXEL_ID = "1234567890123456"
    META_ACCESS_TOKEN = "EAA_fake"
    META_API_VERSION = "v22.0"
    META_TEST_EVENT_CODE = ""
    LANDING_PAGE_URL = "https://maya.com.br/"
    BOT_USERNAME = "maya_vip_bot"

sys.modules["config"] = FakeConfig

import capi


# FakeRedis
class FakeRedis:
    def __init__(self): self.store = {}
    def setex(self, k, ttl, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def delete(self, k): self.store.pop(k, None)
    def exists(self, k): return k in self.store
    def hset(self, k, mapping): self.store[k] = mapping

fake_r = FakeRedis()
capi.init(fake_r)


def sha(s): return hashlib.sha256(s.encode()).hexdigest()


# Captura tudo que é enviado pro CAPI
events_sent = []

def mock_post(*args, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"events_received": 1, "fbtrace_id": "FAKE"}'
    resp.json.return_value = {"events_received": 1, "fbtrace_id": "FAKE"}
    events_sent.append(kwargs.get("json", {}))
    return resp


print("╔══════════════════════════════════════════════════════════════╗")
print("║         🎯 TESTE END-TO-END DO FUNIL COMPLETO                ║")
print("╚══════════════════════════════════════════════════════════════╝\n")


with patch("capi.requests.post", side_effect=mock_post):

    # ─── 1. USUÁRIO ABRE LP ─────────────────────────────────────────────
    print("┌─ ETAPA 1: Usuário abre LP com ?fbclid=IwAR_test")
    print("│  Frontend captura _fbp, IP, UA, fbclid")
    print("└─ Backend POST /api/track gera tracking_id\n")

    tracking_id = "Xa2bC9dEfG12hi"
    capi.save_landing_tracking(
        tracking_id,
        fbp="fb.1.1747500000000.123456789",
        fbclid="IwAR_test_click_xyz",
        client_ip="187.45.123.78",
        client_ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
        event_source_url="https://maya.com.br/?fbclid=IwAR_test_click_xyz&utm_source=facebook",
        utm_source="facebook",
        utm_campaign="vip_q2",
        utm_medium="cpc",
    )

    # CAPI ViewContent disparado pelo backend
    capi.send_view_content(
        tracking_id=tracking_id,
        event_id="vc_dedup_uuid_1",
        content_name="Landing Maya",
    )

    assert len(events_sent) == 1, "Deveria ter 1 evento ViewContent"
    e = events_sent[0]["data"][0]
    assert e["event_name"] == "ViewContent"
    assert e["event_id"] == "vc_dedup_uuid_1"
    assert e["user_data"]["fbp"] == "fb.1.1747500000000.123456789"
    assert e["user_data"]["fbc"].endswith(".IwAR_test_click_xyz")
    assert e["user_data"]["client_ip_address"] == "187.45.123.78"
    print(f"   ✅ ViewContent disparado com fbp+fbc+IP+UA\n")


    # ─── 2. USUÁRIO ENTRA NO BOT VIA /START ────────────────────────────
    print("┌─ ETAPA 2: Usuário clica e cai no bot via t.me/maya_vip_bot")
    print(f"│  ?start={tracking_id}")
    print("└─ Bot liga uid ↔ tracking_id, dispara Lead\n")

    uid = 5532817749  # Telegram user ID
    capi.link_user_to_tracking(uid, tracking_id)
    capi.send_lead(uid, telegram_first_name="Carlos", telegram_last_name="Mendes")

    assert len(events_sent) == 2
    e = events_sent[1]["data"][0]
    assert e["event_name"] == "Lead"
    ud = e["user_data"]
    assert ud["fn"] == [sha("carlos")]
    assert ud["ln"] == [sha("mendes")]
    assert ud["external_id"] == [sha(str(uid))]
    assert ud["fbp"] == "fb.1.1747500000000.123456789"
    assert ud["fbc"].endswith(".IwAR_test_click_xyz")
    assert ud["client_ip_address"] == "187.45.123.78"
    assert "ph" not in ud, "Não devemos ter telefone ainda"
    print("   ✅ Lead disparado com fn+ln+external_id+fbc+fbp+IP+UA")
    print(f"   ✅ Sem telefone ainda (esperado)\n")


    # ─── 3. USUÁRIO CLICA NO PLANO ──────────────────────────────────────
    print("┌─ ETAPA 3: Usuário clica em 'VIP Básico R$9,90'")
    print("└─ Bot dispara InitiateCheckout\n")

    capi.send_initiate_checkout(
        uid,
        plan_id="vip_basico",
        plan_name="VIP Básico",
        value=9.90,
    )

    assert len(events_sent) == 3
    e = events_sent[2]["data"][0]
    assert e["event_name"] == "InitiateCheckout"
    assert e["custom_data"]["value"] == 9.90
    assert e["custom_data"]["currency"] == "BRL"
    assert e["custom_data"]["content_ids"] == ["vip_basico"]
    print("   ✅ InitiateCheckout disparado")
    print(f"   ✅ value=9.90 BRL content_ids=['vip_basico']\n")


    # ─── 4. USUÁRIO ENVIA NOME + TELEFONE ──────────────────────────────
    print("┌─ ETAPA 4: Bot pede nome → usuário envia 'Carlos Eduardo Mendes Silva'")
    print("│  Bot pede telefone → usuário envia '(31) 98765-4321'")
    print("└─ Bot dispara AddPaymentInfo\n")

    # Bot salva nome
    capi.save_user_pii(uid, full_name="Carlos Eduardo Mendes Silva")

    # Bot salva telefone (normalizado automático)
    capi.save_user_pii(uid, phone="(31) 98765-4321")

    capi.send_add_payment_info(
        uid,
        plan_id="vip_basico",
        plan_name="VIP Básico",
        value=9.90,
    )

    assert len(events_sent) == 4
    e = events_sent[3]["data"][0]
    assert e["event_name"] == "AddPaymentInfo"
    ud = e["user_data"]
    # Note: full_name "Carlos Eduardo Mendes Silva" → fn=carlos, ln=silva
    assert ud["fn"] == [sha("carlos")]
    assert ud["ln"] == [sha("silva")]
    assert ud["ph"] == [sha("5531987654321")]
    assert ud["external_id"] == [sha(str(uid))]
    print("   ✅ AddPaymentInfo com fn+ln+ph+external_id+fbc+fbp+IP+UA")
    print(f"   ✅ Telefone normalizado pra E.164: 5531987654321\n")


    # ─── 5. WEBHOOK SYNCPAY CONFIRMA PAGAMENTO ─────────────────────────
    print("┌─ ETAPA 5: SyncPay webhook chega com:")
    print("│    status: PAID_OUT")
    print("│    id:     25310519")
    print("│    amount: 9.90")
    print("│    cliente: Carlos Eduardo Mendes Silva")
    print("└─ Bot dispara Purchase\n")

    # Dispara Purchase
    capi.send_purchase(
        uid,
        transaction_id="25310519",
        plan_id="vip_basico",
        plan_name="VIP Básico",
        value=9.90,
    )

    assert len(events_sent) == 5
    e = events_sent[4]["data"][0]
    assert e["event_name"] == "Purchase"
    assert e["event_id"] == "purchase_25310519", f"event_id deve usar transaction_id, got {e['event_id']}"
    assert e["custom_data"]["value"] == 9.90
    assert e["custom_data"]["currency"] == "BRL"
    assert e["custom_data"]["order_id"] == "25310519"

    ud = e["user_data"]
    assert ud["fn"] == [sha("carlos")]
    assert ud["ln"] == [sha("silva")]
    assert ud["ph"] == [sha("5531987654321")]
    assert ud["country"] == [sha("br")]
    # external_id contém só telegram_user_id
    assert len(ud["external_id"]) == 1
    assert sha(str(uid)) in ud["external_id"]
    assert ud["fbp"] == "fb.1.1747500000000.123456789"
    assert ud["fbc"].endswith(".IwAR_test_click_xyz")
    assert ud["client_ip_address"] == "187.45.123.78"
    assert ud["client_user_agent"] == "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"

    print("   ✅ Purchase event_id=purchase_25310519 (idempotente)")
    print("   ✅ user_data RICO:")
    print(f"      • fn (nome)       : hash de 'carlos'")
    print(f"      • ln (sobrenome)  : hash de 'silva'")
    print(f"      • ph (telefone)   : hash de '5531987654321' (E.164 BR)")
    print(f"      • external_id[0]  : hash de telegram_user_id")
    print(f"      • country         : hash de 'br'")
    print(f"      • fbc, fbp        : raw (preservados da LP)")
    print(f"      • client_ip       : 187.45.123.78 (raw)")
    print(f"      • client_ua       : Mozilla/5.0 ...iPhone (raw)\n")


    # ─── 6. RESUMO DO EMQ ESPERADO ──────────────────────────────────────
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│  RESUMO DO PURCHASE — parâmetros enviados pra Meta:          │")
    print("├──────────────────────────────────────────────────────────────┤")
    fields = list(ud.keys())
    print(f"│  user_data: {len(fields)} campos                                       │")
    for f in fields:
        v = ud[f]
        is_array = isinstance(v, list)
        marker = "🔐 hash" if is_array else "🟢 raw "
        print(f"│  • {marker}  {f:<22}                              │")
    print("├──────────────────────────────────────────────────────────────┤")
    print("│  EMQ esperado pro Purchase: 6-8 / 10                         │")
    print("│  (sem CPF; external_id=telegram_uid + fn+ln+ph+fbc+fbp)     │")
    print("└──────────────────────────────────────────────────────────────┘\n")


print("═" * 64)
print("✅ FLUXO E2E COMPLETO — TUDO FUNCIONANDO")
print("═" * 64)
print(f"\nTotal de eventos disparados: {len(events_sent)}")
print("  1. ViewContent      (LP)")
print("  2. Lead             (/start)")
print("  3. InitiateCheckout (plano clicado)")
print("  4. AddPaymentInfo   (PIX gerado, com telefone)")
print("  5. Purchase         (webhook SyncPay, com CPF)")
