"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                          🤖 MAYA BOT — Main                                  ║
║                                                                              ║
║  Fluxo atualizado (com CAPI):                                                ║
║                                                                              ║
║  LP → t.me/bot?start=TRACKING_ID                                             ║
║    └─ /start TRACKING_ID  → liga uid ↔ tracking_id + dispara Lead (CAPI)    ║
║                                                                              ║
║  [plano clicado]   → dispara InitiateCheckout (CAPI)                         ║
║                    → confirmação + botão "Pagar com Pix"                     ║
║                                                                              ║
║  [Pagar com Pix]   → entra em modo "aguardando telefone"                     ║
║                                                                              ║
║  [user envia nome] → pede telefone                                           ║
║                                                                              ║
║  [user envia tel.] → valida, gera PIX, dispara AddPaymentInfo (CAPI)         ║
║                    → 3 botões (Verificar / Copiar / QR)                      ║
║                                                                              ║
║  [Verificar]       → consulta status na SyncPay                              ║
║  Webhook           → libera VIP + dispara Purchase (CAPI)                    ║
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
    MessageHandler,
    filters,
    ContextTypes,
)

import config
import syncpay
import capi
import landing_routes

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

def _k_user(uid):           return f"maya:user:{uid}"
def _k_state(uid):          return f"maya:state:{uid}"           # estado do "form" de coleta
def _k_pending_plan(uid):   return f"maya:pending_plan:{uid}"    # plano escolhido aguardando PIX

# Estados possíveis:
STATE_AWAITING_PHONE = "awaiting_phone"


# ═══════════════════════════════════════════════════════════════════════════════
# 🎨 HELPERS DE FORMATAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_brl(value: float) -> str:
    return f"R${value:.2f}".replace(".", ",")


def get_user_handle(tg_user) -> str:
    if tg_user.username:
        return f"@{tg_user.username}"
    return tg_user.first_name or "amor"


def social_proof_count() -> int:
    return config.SOCIAL_PROOF_BASE + random.randint(0, config.SOCIAL_PROOF_VARIANCE)


def _set_state(uid: int, state: str, ttl_minutes: int = 30) -> None:
    r.setex(_k_state(uid), timedelta(minutes=ttl_minutes), state)


def _get_state(uid: int) -> str:
    return r.get(_k_state(uid)) or ""


def _clear_state(uid: int) -> None:
    r.delete(_k_state(uid))


# ═══════════════════════════════════════════════════════════════════════════════
# 📝 COPYWRITING
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


def ask_phone_text() -> str:
    return (
        "📱 Me passa seu *WhatsApp com DDD*\n"
        "_(ex: 31 99999-9999)_\n\n"
        "_É pra te enviar o link do grupo VIP no zap caso o Telegram dê problema._"
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

    # Limpa estado anterior (caso o user tenha ficado pendurado num form)
    _clear_state(uid)

    # ── Capture tracking_id da LP (start_param) ─────────────────────────────
    tracking_id = context.args[0] if context.args else None
    if tracking_id:
        capi.link_user_to_tracking(uid, tracking_id)
        logger.info(f"[/start] uid={uid} tracking_id={tracking_id}")

    # ── Persiste dados básicos do user ──────────────────────────────────────
    r.hset(_k_user(uid), mapping={
        "first_name":    user.first_name or "",
        "last_name":     user.last_name or "",
        "username":      user.username or "",
        "language_code": user.language_code or "pt-br",
        "started_at":    datetime.utcnow().isoformat(),
    })

    # ── Dispara Lead via CAPI (fire-and-forget, não bloqueia UX) ────────────
    asyncio.create_task(asyncio.to_thread(
        capi.send_lead,
        uid,
        telegram_first_name=user.first_name,
        telegram_last_name=user.last_name,
    ))

    # ── 1) Vídeo de abertura ────────────────────────────────────────────────
    if config.VIDEO_START.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            with open(config.VIDEO_START, "rb") as video:
                await context.bot.send_video(chat_id, video=video)
        except Exception as e:
            logger.warning(f"Erro enviando vídeo de abertura: {e}")

    # ── 2) Voice message ────────────────────────────────────────────────────
    if config.AUDIO_START.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.RECORD_VOICE)
            await asyncio.sleep(1.5)
            with open(config.AUDIO_START, "rb") as audio:
                await context.bot.send_voice(chat_id, voice=audio)
        except Exception as e:
            logger.warning(f"Erro enviando voice: {e}")

    await asyncio.sleep(0.5)

    # ── 3) Texto + botões dos planos ────────────────────────────────────────
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

    # Salva escolha (TTL 30min)
    r.setex(f"maya:plan_choice:{uid}", 1800, plan_id)

    # ── Dispara InitiateCheckout via CAPI ──────────────────────────────────
    asyncio.create_task(asyncio.to_thread(
        capi.send_initiate_checkout,
        uid,
        plan_id=plan_id,
        plan_name=plan["name"],
        value=plan["price"],
    ))

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
# 🎯 HANDLER: clicou em "Pagar com Pix" → entra na coleta de nome+telefone
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan_id = query.data.split(":", 1)[1]
    plan = config.PLANS.get(plan_id)
    if not plan:
        return

    uid     = query.from_user.id
    chat_id = query.message.chat_id

    # Guarda qual plano está aguardando coleta
    r.setex(_k_pending_plan(uid), 1800, plan_id)

    # ── Se já temos PII completa, pula direto pro PIX ──────────────────────
    pii = capi.get_user_pii(uid)
    if pii.get("full_name") and pii.get("phone"):
        await _generate_and_send_pix(context, chat_id, uid, plan_id)
        return

    # ── Senão, começa o form: pede nome ────────────────────────────────────
    _set_state(uid, STATE_AWAITING_NAME)
    await context.bot.send_message(
        chat_id=chat_id,
        text=ask_name_text(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HANDLER: mensagens de texto (formulário de coleta)
# ═══════════════════════════════════════════════════════════════════════════════

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg     = update.effective_message
    uid     = update.effective_user.id
    chat_id = update.effective_chat.id
    text    = (msg.text or "").strip()

    state = _get_state(uid)

    # Sem estado → ignora (provavelmente é o user mandando msg aleatória)
    if not state:
        return

    # ── Coleta de NOME ─────────────────────────────────────────────────────
    if state == STATE_AWAITING_NAME:
        if len(text) < 3 or " " not in text:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🤔 Me passa seu *nome completo* (nome e sobrenome).",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        capi.save_user_pii(uid, full_name=text)
        _set_state(uid, STATE_AWAITING_PHONE)
        await context.bot.send_message(
            chat_id=chat_id,
            text=ask_phone_text(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Coleta de TELEFONE ─────────────────────────────────────────────────
    if state == STATE_AWAITING_PHONE:
        normalized = capi.normalize_phone_br(text)

        # Aceita só celular (DDI 55 + 11 dígitos) — fixo dificilmente é WhatsApp
        if not normalized or len(normalized) not in (12, 13):
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🤔 Esse número não parece válido.\n\n"
                    "Me manda o *WhatsApp com DDD*, ex: `31999999999` ou `(31) 99999-9999`"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        capi.save_user_pii(uid, phone=normalized)
        _clear_state(uid)

        # ── Recupera plano pendente e gera o PIX ─────────────────────────
        plan_id = r.get(_k_pending_plan(uid))
        if not plan_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Algo deu errado, selecione um plano de novo. /start",
            )
            return

        await _generate_and_send_pix(context, chat_id, uid, plan_id)
        return


# ═══════════════════════════════════════════════════════════════════════════════
# 💸 GERAR PIX + ENVIAR + DISPARAR AddPaymentInfo
# ═══════════════════════════════════════════════════════════════════════════════

async def _generate_and_send_pix(context, chat_id, uid, plan_id):
    plan = config.PLANS.get(plan_id)
    if not plan:
        await context.bot.send_message(chat_id=chat_id, text="Plano inválido 😕")
        return

    pii = capi.get_user_pii(uid)
    user = await context.bot.get_chat(uid)

    # ── 1) Vídeo teaser antes do PIX ───────────────────────────────────────
    video_path = config.VIDEO_TEASER if config.VIDEO_TEASER.exists() else config.VIDEO_START
    if video_path.exists():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            with open(video_path, "rb") as video:
                await context.bot.send_video(chat_id, video=video)
        except Exception as e:
            logger.warning(f"Erro enviando vídeo teaser: {e}")

    # ── 2) Reusa PIX pendente se existir ───────────────────────────────────
    pendente = syncpay.get_pix_pendente(uid)
    if pendente and pendente.get("plan_id") == plan_id:
        logger.info(f"[PIX] ♻️ Reusando PIX pendente uid={uid}")
        pix_data = {"pix_code": pendente["pix_code"], "identifier": pendente["identifier"]}
    else:
        # ── 3) Gera PIX novo ──────────────────────────────────────────────
        try:
            pix_data = syncpay.gerar_pix(
                uid=uid,
                amount=plan["price"],
                plan_id=plan_id,
                nome_cliente=pii.get("full_name") or (user.full_name or "Cliente"),
                telefone=pii.get("phone"),
            )
        except Exception as e:
            logger.error(f"Erro gerando PIX: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="😔 Tive um problema pra gerar o PIX. Tenta de novo em alguns segundos? 💕"
            )
            return

        syncpay.salvar_customer(uid, user, plan_id)

    # ── 4) Dispara AddPaymentInfo via CAPI ─────────────────────────────────
    asyncio.create_task(asyncio.to_thread(
        capi.send_add_payment_info,
        uid,
        plan_id=plan_id,
        plan_name=plan["name"],
        value=plan["price"],
    ))

    # ── 5) Confirmação do plano ────────────────────────────────────────────
    await context.bot.send_message(
        chat_id=chat_id,
        text=pix_intro_text(plan),
        parse_mode=ParseMode.MARKDOWN,
    )

    # ── 6) Código PIX em monospace ─────────────────────────────────────────
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"`{pix_data['pix_code']}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    # ── 7) Instruções + 3 botões ───────────────────────────────────────────
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

    # ── 8) Social proof ────────────────────────────────────────────────────
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

    pendente = syncpay.get_pix_pendente(query.from_user.id)
    if not pendente:
        await query.answer("⚠️ Seu PIX expirou. Selecione o plano de novo.", show_alert=True)
        return

    await query.answer(
        "✅ Chave PIX copiada!\n\n"
        "👆 Toque no código abaixo pra colar no app do seu banco.",
        show_alert=True,
    )

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

    # ── Mensagem comum ─────────────────────────────────────────────────────
    await bot.send_message(
        chat_id=uid,
        text=(
            f"🎉 *PAGAMENTO CONFIRMADO!*\n\n"
            f"💰 Valor: {fmt_brl(amount)}\n\n"
            f"✅ Seu acesso foi liberado! Bem-vindo ao clube exclusivo 💎"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    # ── Entrega por tipo ───────────────────────────────────────────────────
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
    if not channel_id:
        return None
    try:
        bot = _application.bot
        result = await bot.create_chat_invite_link(
            chat_id=channel_id,
            member_limit=1,
            expire_date=int(time.time()) + 86400,
        )
        return result.invite_link
    except Exception as e:
        logger.error(f"Erro gerando invite link pra {channel_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 BOOT
# ═══════════════════════════════════════════════════════════════════════════════

_application: Application = None


def run_flask(flask_app: Flask):
    flask_app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False, use_reloader=False)


def main():
    config.validate_required_config()

    # ── 1) CAPI init ────────────────────────────────────────────────────────
    capi.init(r)

    # ── 2) Flask app ────────────────────────────────────────────────────────
    flask_app = Flask(__name__)

    @flask_app.route("/health")
    def health():
        return {"status": "ok"}, 200

    # Rotas da landing page (GET / + POST /api/track)
    landing_routes.register_routes(flask_app)

    # ── 3) Telegram Application ────────────────────────────────────────────
    global _application
    _application = Application.builder().token(config.BOT_TOKEN).build()

    _application.add_handler(CommandHandler("start", cmd_start))
    _application.add_handler(CallbackQueryHandler(cb_plan,         pattern=r"^plan:"))
    _application.add_handler(CallbackQueryHandler(cb_pix,          pattern=r"^pix:"))
    _application.add_handler(CallbackQueryHandler(cb_check_status, pattern=r"^check:"))
    _application.add_handler(CallbackQueryHandler(cb_copy,         pattern=r"^copy:"))
    _application.add_handler(CallbackQueryHandler(cb_qr,           pattern=r"^qr:"))

    # MessageHandler pra coleta de nome+telefone — ÚLTIMO handler, baixa prioridade
    _application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        on_text_message,
    ))

    # ── 4) Sobe o loop do bot ──────────────────────────────────────────────
    loop = asyncio.new_event_loop()

    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_application.initialize())
        loop.run_until_complete(_application.start())
        loop.run_until_complete(_application.updater.start_polling())
        loop.run_forever()

    bot_thread = threading.Thread(target=runner, daemon=True)
    bot_thread.start()

    time.sleep(2)

    # ── 5) SyncPay init ────────────────────────────────────────────────────
    syncpay.init(
        flask_app   = flask_app,
        bot_app     = _application,
        event_loop  = loop,
        redis_conn  = r,
        on_payment  = release_vip_access,
    )

    logger.info(f"🚀 {config.BOT_PERSONA_NAME} Bot rodando na porta {config.FLASK_PORT}")

    run_flask(flask_app)


if __name__ == "__main__":
    main()
