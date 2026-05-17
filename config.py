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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", "Maya")

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
FLASK_PORT           = int(os.getenv("FLASK_PORT", "8080"))

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  REDIS
# ═══════════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ═══════════════════════════════════════════════════════════════════════════════
# 🎁 ENTREGÁVEIS (canais VIP e contato whatsapp)
# ═══════════════════════════════════════════════════════════════════════════════

# Link de convite do canal VIP principal — gere pelo Telegram com expiração e
# limite de usos pra cada compra (idealmente 1 uso, válido por 24h).
# Pra começar, pode usar um link fixo, mas o IDEAL é o bot gerar links únicos
# via createChatInviteLink (ver release_vip_access em maya_bot.py).
VIP_CHANNEL_ID         = os.getenv("VIP_CHANNEL_ID", "")          # -1001234567890
VIP_PLUS_CHANNEL_ID    = os.getenv("VIP_PLUS_CHANNEL_ID", "")     # -1001234567891
VIP_CHANNEL_FALLBACK   = os.getenv("VIP_CHANNEL_FALLBACK", "")    # link público backup
WHATSAPP_CONTACT       = os.getenv("WHATSAPP_CONTACT", "https://wa.me/5511999999999")

# ═══════════════════════════════════════════════════════════════════════════════
# 🎬 MÍDIAS (coloque os arquivos na pasta content/)
# ═══════════════════════════════════════════════════════════════════════════════

PHOTO_PROFILE = MEDIA_DIR / "photo_profile.jpg"   # opcional — foto que aparece no chat
AUDIO_START   = MEDIA_DIR / "audio_start.ogg"     # voice message do /start (.ogg ou .mp3)
VIDEO_TEASER  = MEDIA_DIR / "video_teaser.mp4"    # vídeo de prévia mostrado antes do PIX

# ═══════════════════════════════════════════════════════════════════════════════
# 💰 PLANOS DE VENDA
# ═══════════════════════════════════════════════════════════════════════════════
#
# Cada plano tem:
#   - id:           identificador interno
#   - emoji:        ícone do botão
#   - name:         nome visível
#   - price:        valor em reais (float)
#   - duration:     "Vitalício" ou "X dias" — exibido na confirmação
#   - deliverable:  "channel" | "channel_plus" | "whatsapp"
#                   define o que o bot entrega quando o pagamento confirma
# ─────────────────────────────────────────────────────────────────────────────

PLANS = {
    "tudo": {
        "id":          "tudo",
        "emoji":       "🔥",
        "name":        "QUERO TUDO AGORA",
        "price":       12.78,
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

# Ordem dos botões na tela de /start (do maior valor → menor, ou como preferir)
PLANS_DISPLAY_ORDER = ["tudo", "tudo_plus", "whats"]

# ═══════════════════════════════════════════════════════════════════════════════
# ⏱️  PIX
# ═══════════════════════════════════════════════════════════════════════════════

PIX_VALIDADE_MINUTOS = 30

# ═══════════════════════════════════════════════════════════════════════════════
# 📊 SOCIAL PROOF (contador "X pessoas entraram nas últimas 2h")
# ═══════════════════════════════════════════════════════════════════════════════
#
# Pode ser real (contando no Redis) ou simulado. Aqui está simulado entre
# SOCIAL_PROOF_BASE e SOCIAL_PROOF_BASE + SOCIAL_PROOF_VARIANCE.
# Pra tornar real, conte starts únicos nas últimas 2h via Redis sorted set.
# ─────────────────────────────────────────────────────────────────────────────

SOCIAL_PROOF_BASE     = 280
SOCIAL_PROOF_VARIANCE = 80
SOCIAL_PROOF_MEMBERS  = 488   # número fixo "X membros já estão lá dentro"

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
