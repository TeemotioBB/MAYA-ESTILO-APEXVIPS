"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    ⚙️  CONFIG — Maya Bot                                     ║
║                                                                              ║
║  Todas as variáveis sensíveis vêm de variáveis de ambiente (.env).          ║
║  NUNCA hardcode tokens ou client_secrets aqui!                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
from pathlib import Path

# ── Carrega .env se python-dotenv estiver instalado (opcional) ────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════════
# 📍 PATHS
# ═══════════════════════════════════════════════════════════════════════════════

BASE_DIR  = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "content"

# ═══════════════════════════════════════════════════════════════════════════════
# 🤖 TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", "Maya")
# Username do bot SEM @ (ex: "maya_vip_bot") — usado no link t.me/...
BOT_USERNAME     = os.getenv("BOT_USERNAME", "seu_bot_aqui")

# ═══════════════════════════════════════════════════════════════════════════════
# 💳 SYNCPAY
# ═══════════════════════════════════════════════════════════════════════════════

SYNCPAY_CLIENT_ID     = os.getenv("SYNCPAY_CLIENT_ID", "")
SYNCPAY_CLIENT_SECRET = os.getenv("SYNCPAY_CLIENT_SECRET", "")
SYNCPAY_BASE_URL      = os.getenv("SYNCPAY_BASE_URL", "https://api.syncpayments.com.br/api/partner/v1")

# ═══════════════════════════════════════════════════════════════════════════════
# 🌐 WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

# Ex: https://meudominio.com  (sem barra no final)
WEBHOOK_BASE_URL     = os.getenv("WEBHOOK_BASE_URL", "")
SYNCPAY_WEBHOOK_PATH = "/webhook/syncpay"
# Railway define PORT automaticamente. Em dev local usa FLASK_PORT ou 8080.
FLASK_PORT           = int(os.getenv("PORT") or os.getenv("FLASK_PORT") or "8080")

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  REDIS
# ═══════════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ═══════════════════════════════════════════════════════════════════════════════
# 📊 META CONVERSIONS API (CAPI)
# ═══════════════════════════════════════════════════════════════════════════════

META_PIXEL_ID        = os.getenv("META_PIXEL_ID", "")
META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN", "")
META_API_VERSION     = os.getenv("META_API_VERSION", "v22.0")
# Deixe TEST_EVENT_CODE setado durante setup (pega no Events Manager > Test Events).
# REMOVA em produção (deixe string vazia) ou os eventos não contam pra real.
META_TEST_EVENT_CODE = os.getenv("META_TEST_EVENT_CODE", "")

# URL pública da landing page (usada como event_source_url no CAPI)
LANDING_PAGE_URL     = os.getenv("LANDING_PAGE_URL", "https://example.com/")

# ═══════════════════════════════════════════════════════════════════════════════
# 🎁 ENTREGÁVEIS (canais VIP e contato whatsapp)
# ═══════════════════════════════════════════════════════════════════════════════

VIP_CHANNEL_ID         = os.getenv("VIP_CHANNEL_ID", "")
VIP_PLUS_CHANNEL_ID    = os.getenv("VIP_PLUS_CHANNEL_ID", "")
VIP_CHANNEL_FALLBACK   = os.getenv("VIP_CHANNEL_FALLBACK", "")
WHATSAPP_CONTACT       = os.getenv("WHATSAPP_CONTACT", "https://wa.me/5511999999999")

# ═══════════════════════════════════════════════════════════════════════════════
# 🎬 MÍDIAS (coloque os arquivos na pasta content/)
# ═══════════════════════════════════════════════════════════════════════════════

PHOTO_PROFILE = MEDIA_DIR / "photo_profile.jpg"
VIDEO_START   = MEDIA_DIR / "video_start.mp4"
AUDIO_START   = MEDIA_DIR / "audio_start.ogg"
VIDEO_TEASER  = MEDIA_DIR / "video_teaser.mp4"

# ═══════════════════════════════════════════════════════════════════════════════
# 💰 PLANOS DE VENDA
# ═══════════════════════════════════════════════════════════════════════════════

PLANS = {
    "tudo": {
        "id":          "tudo",
        "emoji":       "🔥",
        "name":        "QUERO TUDO AGORA",
        "price":       1.50,
        "duration":    "Vitalício",
        "deliverable": "channel",
    },
    "tudo_plus": {
        "id":          "tudo_plus",
        "emoji":       "💎",
        "name":        "TUDO + WHATS + CHAMADA",
        "price":       19.78,
        "duration":    "Vitalício",
        "deliverable": "channel_plus",
    },
    "whats": {
        "id":          "whats",
        "emoji":       "📱",
        "name":        "MEU WHATSAPP PESSOAL",
        "price":       7.78,
        "duration":    "Vitalício",
        "deliverable": "whatsapp",
    },
}

PLANS_DISPLAY_ORDER = ["tudo", "tudo_plus", "whats"]

# ═══════════════════════════════════════════════════════════════════════════════
# ⏱️  PIX
# ═══════════════════════════════════════════════════════════════════════════════

PIX_VALIDADE_MINUTOS = 30

# ═══════════════════════════════════════════════════════════════════════════════
# 📊 SOCIAL PROOF
# ═══════════════════════════════════════════════════════════════════════════════

SOCIAL_PROOF_BASE     = 280
SOCIAL_PROOF_VARIANCE = 80
SOCIAL_PROOF_MEMBERS  = 488

# ═══════════════════════════════════════════════════════════════════════════════
# 🛡️  VALIDAÇÃO MÍNIMA — falha cedo se faltar config crítica
# ═══════════════════════════════════════════════════════════════════════════════

def validate_required_config():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not SYNCPAY_CLIENT_ID:
        missing.append("SYNCPAY_CLIENT_ID")
    if not SYNCPAY_CLIENT_SECRET:
        missing.append("SYNCPAY_CLIENT_SECRET")
    if not WEBHOOK_BASE_URL:
        missing.append("WEBHOOK_BASE_URL")
    if missing:
        raise RuntimeError(
            f"❌ Faltam variáveis de ambiente obrigatórias: {', '.join(missing)}\n"
            f"   Copie .env.example pra .env e preencha."
        )

    # CAPI — não bloqueia o boot, só avisa
    if not META_PIXEL_ID:
        print("⚠️ AVISO: META_PIXEL_ID não configurado — CAPI desabilitada")
    if not META_ACCESS_TOKEN:
        print("⚠️ AVISO: META_ACCESS_TOKEN não configurado — CAPI desabilitada")
    if not BOT_USERNAME or BOT_USERNAME == "seu_bot_aqui":
        print("⚠️ AVISO: BOT_USERNAME não configurado — landing page vai redirecionar errado")
