"""
═══════════════════════════════════════════════════════════════════════════════
ADIÇÕES PARA O config.py — Cole estes blocos no seu config.py atual
═══════════════════════════════════════════════════════════════════════════════

NÃO substitua seu config.py inteiro — apenas adicione os blocos abaixo.
"""

# ────────────────────────────────────────────────────────────────────────────
# BLOCO 1 — Variáveis Meta CAPI
# Adicione perto das outras variáveis lidas do .env (BOT_TOKEN, etc.)
# ────────────────────────────────────────────────────────────────────────────

META_PIXEL_ID        = os.getenv("META_PIXEL_ID", "")
META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN", "")
META_API_VERSION     = os.getenv("META_API_VERSION", "v22.0")
# Deixe TEST_EVENT_CODE setado durante setup (pega no Events Manager > Test Events).
# REMOVA em produção (deixe string vazia) ou os eventos não contam.
META_TEST_EVENT_CODE = os.getenv("META_TEST_EVENT_CODE", "")

# URL pública da landing page (usada como event_source_url no CAPI)
LANDING_PAGE_URL     = os.getenv("LANDING_PAGE_URL", "https://example.com/")

# Username do bot SEM @ (ex: "maya_vip_bot") — usado no link t.me/...
BOT_USERNAME         = os.getenv("BOT_USERNAME", "seu_bot_aqui")


# ────────────────────────────────────────────────────────────────────────────
# BLOCO 2 — Validação dos campos obrigatórios
# Adicione dentro da função validate_required_config() existente
# ────────────────────────────────────────────────────────────────────────────

# Cole DENTRO da função validate_required_config():
"""
    # CAPI — não bloqueia o boot, mas avisa
    if not META_PIXEL_ID:
        print("⚠️ AVISO: META_PIXEL_ID não configurado — CAPI desabilitada")
    if not META_ACCESS_TOKEN:
        print("⚠️ AVISO: META_ACCESS_TOKEN não configurado — CAPI desabilitada")
    if not BOT_USERNAME or BOT_USERNAME == "seu_bot_aqui":
        print("⚠️ AVISO: BOT_USERNAME não configurado — landing page vai redirecionar errado")
"""


# ────────────────────────────────────────────────────────────────────────────
# BLOCO 3 — .env adições
# ────────────────────────────────────────────────────────────────────────────

"""
# Adicione ao seu arquivo .env:

# ── Meta Conversions API ──────────────────────────────────────────────
META_PIXEL_ID=1234567890123456
META_ACCESS_TOKEN=EAAxxxxxxxxxx
META_API_VERSION=v22.0
META_TEST_EVENT_CODE=TEST12345     # Remover em produção!

# ── Landing Page ──────────────────────────────────────────────────────
LANDING_PAGE_URL=https://mayabot.com.br/
BOT_USERNAME=maya_vip_bot          # SEM @
"""
