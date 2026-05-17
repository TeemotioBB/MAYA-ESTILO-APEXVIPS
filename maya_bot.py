"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                          🤖 MAYA BOT — Main                                  ║
║                                                                              ║
║  Fluxo:                                                                      ║
║    /start           → foto + voice + texto + 3 botões de plano              ║
║    [plano clicado]  → confirmação + botão "Pagar com Pix"                   ║
║    [Pagar com Pix]  → vídeo teaser + gera PIX + código + 3 botões          ║
║    [Verificar]      → consulta status na SyncPay                            ║
║    [Copiar Chave]   → reenvia código pra usuário copiar                     ║
║    [Mostrar QR]     → gera QR Code e envia                                  ║
║    Webhook          → libera acesso VIP automaticamente                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import io
import asyncio
import logging
import random
import threading
import time
from datetime import datetime, timedelta

import redis
import qrcode
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config
import syncpay

# ═══════════════════════════════════════════════════════════════════════════════
# 📋 LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("maya_bot")

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️ REDIS
# ═══════════════════════════════════════════════════════════════════════════════

r = redis.from_url(config.REDIS_URL, decode_responses=True)

def _k_user(uid):  return f"maya:user:{uid}"

# ═══════════════════════════════════════════════════════════════════════════════
# 🎨 HELPERS DE FORMATAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_brl(value: float) -> str:
    """12.78 → 'R$12,78'"""
    return f"R${value:.2f}".replace(".", ",")


def get_user_handle(tg_user) -> str:
    """Retorna @username ou first_name como fallback."""
    if tg_user.username:
        return f"@{tg_user.username}"
    return tg_user.first_name or "amor"


def social_proof_count() -> int:
    return config.SOCIAL_PROOF_BASE + random.randint(0, config.SOCIAL_PROOF_VARIANCE)

# ═══════════════════════════════════════════════════════════════════════════════
# 📝 COPYWRITING (centralizado pra fácil A/B test depois)
# ═══════════════════════════════════════════════════════════════════════════════

def welcome_text(user_handle: str) -> str:
    return (
        f"🟢 *{config.BOT_PERSONA_NAME} está online agora* 🟢\n\n"
        f"Oi meu gostoso {user_handle} 😈\n\n"
        f"Porra... que tesão você ter entrado justo agora!\n\n"
        f"Tô pelada na cama, buceta molhada e gravando vídeo bem safado!\n\n"
        f"🔥 Vídeos completamente sem censura (eu me fodendo e gozando de verdade)\n"
        f"📸 Minhas fotos mais putas e íntimas\n"
        f"💬 Meu WhatsApp pessoal (pra gente fazer o que quiser)\n\n"
        f"⭐ +{config.SOCIAL_PROOF_MEMBERS} membros já estão lá dentro.\n"
        f"🟢 *{config.BOT_PERSONA_NAME} está online agora* 🟢"
    )


def plan_confirmation_text(plan: dict) -> str:
    return (
        f"🌟 *Plano selecionado:*\n\n"
        f"🎁 Plano: {plan['emoji']} {plan['name']}\n"
        f"💰 Valor: *{fmt_brl(plan['price'])}*\n"
        f"⌛ Duração: {plan['duration']}\n\n"
        f"Escolha o método de pagamento abaixo:"
    )


def pix_intro_text(plan: dict) -> str:
    return (
        f"🌟 *Você selecionou o seguinte plano:*\n\n"
        f"🎁 Plano: {plan['emoji']} {plan['name']}\n"
        f"💰 Valor: *{fmt_brl(plan['price'])}*\n\n"
        f"💎 Pague via Pix Copia e Cola (ou QR Code em alguns bancos):"
    )


def pix_instructions_text() -> str:
    return (
        f"👆 Toque na chave PIX acima para copiá-la\n\n"
        f"‼️ Após o pagamento, clique no botão abaixo para verificar o status:"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: /start
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    uid     = user.id
    chat_id = update.effective_chat.id

    # Captura start parameter (útil pra CAPI / external_id vindo da LP)
    start_param = context.args[0] if context.args else None
    if start_param:
        r.setex(f"maya:start_param:{uid}", 86400, start_param)
        logger.info(f"[/start] uid={uid} start_param={start_param}")

    # Persiste dados básicos do user
    r.hset(_k_user(uid), mapping={
        "first_name":    user.first_name or "",
        "last_name":     user.last_name or "",
        "username":      user.username or "",
        "language_code": user.language_code or "pt-br",
        "started_at":    datetime.utcnow().isoformat(),
    })

    # 1) Vídeo de abertura (opcional)
    if config.VIDEO_START.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            with open(config.VIDEO_START, "rb") as video:
                await context.bot.send_video(chat_id, video=video)
        except Exception as e:
            logger.warning(f"Erro enviando vídeo de abertura: {e}")

    # 2) Voice message (opcional)
    if config.AUDIO_START.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.RECORD_VOICE)
            await asyncio.sleep(1.5)
            with open(config.AUDIO_START, "rb") as audio:
                await context.bot.send_voice(chat_id, voice=audio)
        except Exception as e:
            logger.warning(f"Erro enviando voice: {e}")

    await asyncio.sleep(0.5)

    # 3) Texto + botões dos planos
    keyboard = []
    for plan_id in config.PLANS_DISPLAY_ORDER:
        plan = config.PLANS[plan_id]
        button_text = f"{plan['emoji']} {plan['name']} por {fmt_brl(plan['price'])}"
        keyboard.append([
            InlineKeyboardButton(button_text, callback_data=f"plan:{plan_id}")
        ])

    await context.bot.send_message(
        chat_id=chat_id,
        text=welcome_text(get_user_handle(user)),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: clicou num plano
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan_id = query.data.split(":", 1)[1]
    plan = config.PLANS.get(plan_id)
    if not plan:
        await query.message.reply_text("Plano inválido 😕")
        return

    uid     = query.from_user.id
    chat_id = query.message.chat_id

    # Salva escolha (TTL de 30min)
    r.setex(f"maya:plan_choice:{uid}", 1800, plan_id)

    keyboard = [[
        InlineKeyboardButton("💎  Pagar com Pix", callback_data=f"pix:{plan_id}")
    ]]

    await context.bot.send_message(
        chat_id=chat_id,
        text=plan_confirmation_text(plan),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: clicou em "Pagar com Pix"
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Gerando seu PIX...")

    plan_id = query.data.split(":", 1)[1]
    plan = config.PLANS.get(plan_id)
    if not plan:
        return

    uid     = query.from_user.id
    chat_id = query.message.chat_id

    # 1) Vídeo teaser (opcional)
    if config.VIDEO_TEASER.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            with open(config.VIDEO_TEASER, "rb") as video:
                await context.bot.send_video(chat_id, video=video)
        except Exception as e:
            logger.warning(f"Erro enviando vídeo teaser: {e}")

    # 2) Reusa PIX pendente se existir e for do mesmo plano
    pendente = syncpay.get_pix_pendente(uid)
    if pendente and pendente.get("plan_id") == plan_id:
        logger.info(f"[PIX] ♻️ Reusando PIX pendente uid={uid}")
        pix_data = {"pix_code": pendente["pix_code"], "identifier": pendente["identifier"]}
    else:
        # 3) Gera PIX novo
        try:
            pix_data = syncpay.gerar_pix(
                uid=uid,
                amount=plan["price"],
                plan_id=plan_id,
                nome_cliente=query.from_user.full_name or "Cliente",
            )
        except Exception as e:
            logger.error(f"Erro gerando PIX: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="😔 Tive um problema pra gerar o PIX. Tenta de novo em alguns segundos? 💕"
            )
            return

        # Snapshot do customer pro webhook usar depois
        syncpay.salvar_customer(uid, query.from_user, plan_id)

    # 4) Mensagem de confirmação do plano
    await context.bot.send_message(
        chat_id=chat_id,
        text=pix_intro_text(plan),
        parse_mode=ParseMode.MARKDOWN,
    )

    # 5) Código PIX em monospace
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"`{pix_data['pix_code']}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    # 6) Instruções + 3 botões
    keyboard = [
        [InlineKeyboardButton("Verificar Status do Pagamento", callback_data=f"check:{pix_data['identifier']}")],
        [InlineKeyboardButton("Copiar Chave Pix",               callback_data=f"copy:{pix_data['identifier']}")],
        [InlineKeyboardButton("Mostrar QR Code",                callback_data=f"qr:{pix_data['identifier']}")],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text=pix_instructions_text(),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # 7) Social proof
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ {social_proof_count()} pessoas entraram nas últimas 2 horas!",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: Verificar Status
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    identifier = query.data.split(":", 1)[1]

    status = syncpay.consultar_status(identifier)
    logger.info(f"[CHECK] uid={query.from_user.id} id={identifier} status={status}")

    if status in ["completed", "PAID_OUT"]:
        # Se já foi entregue, só avisa. Senão, tenta entregar (retry).
        if syncpay.foi_entregue(identifier):
            await query.answer("✅ Seu acesso já foi liberado! Confere as mensagens acima.", show_alert=True)
        else:
            await query.answer("✅ Pagamento confirmado! Liberando acesso...", show_alert=True)
            ok = await syncpay.retry_entrega(identifier)
            if not ok:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="⚠️ Pagamento confirmado mas tive um problema pra liberar. Me chama no @suporte."
                )
    elif status in ["pending", "PENDING", "WAITING_FOR_APPROVAL", None]:
        await query.answer("⏳ Pagamento ainda não foi identificado. Aguarde alguns segundos após pagar.", show_alert=True)
    else:
        await query.answer(f"Status atual: {status}", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: Copiar Chave Pix (reenvia o código)
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_copy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Reenviando o código!")

    pendente = syncpay.get_pix_pendente(query.from_user.id)
    if not pendente:
        await query.message.reply_text("⚠️ Seu PIX expirou. Selecione o plano de novo.")
        return

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"`{pendente['pix_code']}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: Mostrar QR Code
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Gerando QR Code...")

    pendente = syncpay.get_pix_pendente(query.from_user.id)
    if not pendente:
        await query.message.reply_text("⚠️ Seu PIX expirou. Selecione o plano de novo.")
        return

    # Gera QR code em memória
    img = qrcode.make(pendente["pix_code"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=buf,
        caption="📲 Escaneie com o app do seu banco",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎁 ENTREGA DO VIP — chamado pelo webhook quando pagamento confirma
# ═══════════════════════════════════════════════════════════════════════════════

async def release_vip_access(uid: int, plan_id: str, amount: float,
                              identifier: str, customer: dict):
    """
    Callback chamado pela syncpay quando o pagamento é confirmado.
    Entrega o conteúdo conforme o tipo do plano.
    """
    plan = config.PLANS.get(plan_id)
    if not plan:
        logger.error(f"Plano desconhecido no release: {plan_id}")

    bot = _application.bot
    deliverable = plan["deliverable"] if plan else "channel"

    # Mensagem comum
    await bot.send_message(
        chat_id=uid,
        text=(
            f"🎉 *PAGAMENTO CONFIRMADO!*\n\n"
            f"💰 Valor: {fmt_brl(amount)}\n\n"
            f"✅ Seu acesso foi liberado! Bem-vindo ao clube exclusivo 💎"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    # Entrega por tipo
    if deliverable in ("channel", "channel_plus"):
        channel_id = (
            config.VIP_PLUS_CHANNEL_ID
            if deliverable == "channel_plus"
            else config.VIP_CHANNEL_ID
        )
        invite_link = await _create_one_time_invite(channel_id) or config.VIP_CHANNEL_FALLBACK

        if invite_link:
            keyboard = [[InlineKeyboardButton("💎 ACESSAR VIP AGORA", url=invite_link)]]
            await bot.send_message(
                chat_id=uid,
                text="Clique abaixo pra entrar no canal exclusivo:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await bot.send_message(
                chat_id=uid,
                text="⚠️ Tive um problema gerando seu link. Me chama no @suporte que libero manualmente.",
            )

        # Pro plano "tudo_plus", manda também o whats
        if deliverable == "channel_plus":
            await bot.send_message(
                chat_id=uid,
                text=f"📱 *Meu WhatsApp pessoal:*\n{config.WHATSAPP_CONTACT}",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif deliverable == "whatsapp":
        keyboard = [[InlineKeyboardButton("📱 ABRIR WHATSAPP", url=config.WHATSAPP_CONTACT)]]
        await bot.send_message(
            chat_id=uid,
            text="Aqui está meu WhatsApp pessoal 💕\nPode me chamar a hora que quiser:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    logger.info(f"🎉 Acesso liberado: uid={uid} plan={plan_id} amount={amount}")


async def _create_one_time_invite(channel_id: str):
    """Gera link único de convite (1 uso, válido 24h)."""
    if not channel_id:
        return None
    try:
        bot = _application.bot
        result = await bot.create_chat_invite_link(
            chat_id=channel_id,
            member_limit=1,
            expire_date=int(time.time()) + 86400,   # 24h
        )
        return result.invite_link
    except Exception as e:
        logger.error(f"Erro gerando invite link pra {channel_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 BOOT
# ═══════════════════════════════════════════════════════════════════════════════

_application: Application = None     # global pra release_vip_access acessar o bot


def run_flask(flask_app: Flask):
    flask_app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False, use_reloader=False)


def main():
    config.validate_required_config()

    # 1) Flask app pra webhook
    flask_app = Flask(__name__)

    @flask_app.route("/health")
    def health():
        return {"status": "ok"}, 200

    # 2) Telegram Application
    global _application
    _application = Application.builder().token(config.BOT_TOKEN).build()

    _application.add_handler(CommandHandler("start", cmd_start))
    _application.add_handler(CallbackQueryHandler(cb_plan,         pattern=r"^plan:"))
    _application.add_handler(CallbackQueryHandler(cb_pix,          pattern=r"^pix:"))
    _application.add_handler(CallbackQueryHandler(cb_check_status, pattern=r"^check:"))
    _application.add_handler(CallbackQueryHandler(cb_copy,         pattern=r"^copy:"))
    _application.add_handler(CallbackQueryHandler(cb_qr,           pattern=r"^qr:"))

    # 3) Sobe o loop do bot numa thread separada e captura a referência
    loop = asyncio.new_event_loop()

    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_application.initialize())
        loop.run_until_complete(_application.start())
        loop.run_until_complete(_application.updater.start_polling())
        loop.run_forever()

    bot_thread = threading.Thread(target=runner, daemon=True)
    bot_thread.start()

    # 4) Espera o bot subir antes de inicializar a integração
    time.sleep(2)

    # 5) Inicializa a SyncPay (registra rota /webhook/syncpay + callback)
    syncpay.init(
        flask_app   = flask_app,
        bot_app     = _application,
        event_loop  = loop,
        redis_conn  = r,
        on_payment  = release_vip_access,
    )

    logger.info(f"🚀 {config.BOT_PERSONA_NAME} Bot rodando na porta {config.FLASK_PORT}")

    # 6) Flask roda no thread principal
    run_flask(flask_app)


if __name__ == "__main__":
    main()
