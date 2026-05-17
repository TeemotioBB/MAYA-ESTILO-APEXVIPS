"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              💳 SYNCPAY INTEGRATION — Maya Bot                              ║
║                                                                              ║
║  Versão refatorada da integração original. Mudanças:                        ║
║    • Credenciais via env vars (não mais hardcoded)                          ║
║    • Sem dependência de _load_bot_main() (sophia_bot_v7.2_clean.py)         ║
║    • API pública limpa: gerar_pix, salvar_customer, recuperar_customer,     ║
║      registrar webhook, etc.                                                ║
║    • Mapeamento identifier→plan_id pra entregar conteúdo certo              ║
║    • Retry simples no _get_token e _gerar_pix                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import logging
import asyncio
import requests
from datetime import datetime, timedelta, date
from typing import Optional, Callable

from flask import request as flask_request, jsonify

import config

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  ESTADO INTERNO (injetado via init)
# ═══════════════════════════════════════════════════════════════════════════════

_r            = None
_loop         = None
_bot_app      = None
_on_payment   = None   # callback chamado quando pagamento confirma

_token_cache  = {"token": None, "expires_at": None}

# ═══════════════════════════════════════════════════════════════════════════════
# 🔑 REDIS KEYS
# ═══════════════════════════════════════════════════════════════════════════════

def _k_pix(uid):                 return f"sp:pix:{uid}"
def _k_id_to_uid(identifier):    return f"sp:id2uid:{identifier}"
def _k_id_to_plan(identifier):   return f"sp:id2plan:{identifier}"
def _k_paid(uid):                return f"sp:paid:{uid}"
def _k_notified(uid, day):       return f"sp:notified:{uid}:{day}"
def _k_customer(uid):            return f"sp:customer:{uid}"

# ═══════════════════════════════════════════════════════════════════════════════
# 🔐 AUTENTICAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def _get_token() -> str:
    """Cacheia token e renova 5min antes do expiry."""
    now = datetime.utcnow()
    if _token_cache["token"] and _token_cache["expires_at"]:
        if now < _token_cache["expires_at"] - timedelta(minutes=5):
            return _token_cache["token"]

    logger.info("[SyncPay] 🔄 Gerando novo token de autenticação...")

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{config.SYNCPAY_BASE_URL}/auth-token",
                json={
                    "client_id":     config.SYNCPAY_CLIENT_ID,
                    "client_secret": config.SYNCPAY_CLIENT_SECRET,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            _token_cache["token"] = data["access_token"]
            expires_str = data["expires_at"].replace("Z", "+00:00")
            _token_cache["expires_at"] = datetime.fromisoformat(expires_str).replace(tzinfo=None)

            logger.info(f"[SyncPay] ✅ Token OK — expira: {_token_cache['expires_at']}")
            return _token_cache["token"]
        except Exception as e:
            last_err = e
            logger.warning(f"[SyncPay] Token tentativa {attempt+1}/3 falhou: {e}")
            time.sleep(1 + attempt)

    raise RuntimeError(f"[SyncPay] Falha ao obter token após 3 tentativas: {last_err}")


# ═══════════════════════════════════════════════════════════════════════════════
# 💸 GERAÇÃO DE PIX
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_pix(uid: int, amount: float, plan_id: str, nome_cliente: str = "Cliente") -> dict:
    """
    Gera cobrança PIX na SyncPay e armazena o mapeamento identifier→uid→plan_id
    em Redis com TTL. Retorna dict com pix_code e identifier.
    """
    token = _get_token()
    webhook_url = f"{config.WEBHOOK_BASE_URL}{config.SYNCPAY_WEBHOOK_PATH}"

    payload = {
        "amount":      round(amount, 2),
        "description": f"{config.BOT_PERSONA_NAME} VIP — uid {uid} — {plan_id}",
        "webhook_url": webhook_url,
        "client": {
            "name":  nome_cliente or "Cliente",
            "cpf":   "00000000000",                          # SyncPay aceita placeholder
            "email": f"user{uid}@{config.BOT_PERSONA_NAME.lower()}bot.com",
            "phone": "11999999999",
        },
    }

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{config.SYNCPAY_BASE_URL}/cash-in",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept":        "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            resultado = resp.json()
            break
        except Exception as e:
            last_err = e
            logger.warning(f"[SyncPay] PIX tentativa {attempt+1}/3 falhou: {e}")
            time.sleep(1 + attempt)
    else:
        raise RuntimeError(f"[SyncPay] Falha ao gerar PIX após 3 tentativas: {last_err}")

    identifier = resultado["identifier"]
    pix_code   = resultado["pix_code"]

    ttl = timedelta(minutes=config.PIX_VALIDADE_MINUTOS)
    _r.setex(
        _k_pix(uid),
        ttl,
        json.dumps({
            "identifier": identifier,
            "pix_code":   pix_code,
            "amount":     amount,
            "plan_id":    plan_id,
            "created_at": datetime.utcnow().isoformat(),
        })
    )
    _r.setex(_k_id_to_uid(identifier),  timedelta(hours=2), str(uid))
    _r.setex(_k_id_to_plan(identifier), timedelta(hours=2), plan_id)

    logger.info(f"[SyncPay] 💸 PIX gerado: uid={uid} plan={plan_id} id={identifier} R${amount}")
    return {"pix_code": pix_code, "identifier": identifier}


def get_pix_pendente(uid: int) -> Optional[dict]:
    """Retorna o PIX pendente (se houver) pra reuso."""
    data = _r.get(_k_pix(uid))
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 👤 CUSTOMER DATA (snapshot no momento do PIX, lido no webhook)
# ═══════════════════════════════════════════════════════════════════════════════

def salvar_customer(uid: int, tg_user, plan_id: str) -> dict:
    """
    Snapshot dos dados do user Telegram no momento do PIX. O webhook
    não tem acesso ao objeto tg_user, então a gente persiste aqui.
    """
    data = {
        "chat_id":       uid,
        "full_name":     tg_user.full_name or "",
        "first_name":    tg_user.first_name or "",
        "last_name":     tg_user.last_name or "",
        "username":      tg_user.username or "",
        "language_code": tg_user.language_code or "pt-br",
        "plan_id":       plan_id,
        "saved_at":      datetime.utcnow().isoformat(),
    }
    _r.setex(_k_customer(uid), timedelta(hours=2), json.dumps(data))
    return data


def recuperar_customer(uid: int) -> dict:
    raw = _r.get(_k_customer(uid))
    if not raw:
        return {
            "chat_id": uid, "full_name": "", "first_name": "",
            "last_name": "", "username": "", "language_code": "pt-br",
            "plan_id": ""
        }
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# ✅ VERIFICAÇÃO DE PAGAMENTO (consulta ativa)
# ═══════════════════════════════════════════════════════════════════════════════

def consultar_status(identifier: str) -> Optional[str]:
    """Consulta direta o status do PIX. Usado pelo botão Verificar Status."""
    try:
        token = _get_token()
        resp = requests.get(
            f"{config.SYNCPAY_BASE_URL}/cash-in/{identifier}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("status")
    except Exception as e:
        logger.error(f"[SyncPay] Erro consultar_status: {e}")
        return None


def usuario_pagou(uid: int) -> bool:
    return bool(_r and _r.exists(_k_paid(uid)))


# ═══════════════════════════════════════════════════════════════════════════════
# 🌐 WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

def _register_webhook_route(flask_app):
    @flask_app.route(config.SYNCPAY_WEBHOOK_PATH, methods=["POST"])
    def syncpay_webhook():
        try:
            data       = flask_request.get_json(silent=True) or {}
            transacao  = data.get("data", {})
            identifier = transacao.get("id")
            status     = transacao.get("status")
            amount     = transacao.get("final_amount") or transacao.get("amount")

            logger.info(f"[SyncPay Webhook] id={identifier} status={status} valor={amount}")

            if status in ["completed", "PAID_OUT"] and identifier:
                asyncio.run_coroutine_threadsafe(
                    _processar_pagamento(identifier, amount),
                    _loop
                )
            else:
                logger.info(f"[SyncPay] Status ignorado (ainda não pago): {status}")
            return jsonify({"ok": True}), 200
        except Exception as e:
            logger.error(f"[SyncPay Webhook] Erro: {e}")
            return jsonify({"ok": False}), 200   # 200 mesmo em erro pra não disparar retry


async def _processar_pagamento(identifier: str, amount):
    """Pagamento confirmado: chama callback on_payment registrado no init."""
    try:
        uid_raw = _r.get(_k_id_to_uid(identifier))
        if not uid_raw:
            logger.warning(f"[SyncPay] identifier={identifier} sem uid no Redis (expirou?)")
            return
        uid = int(uid_raw)

        plan_id = _r.get(_k_id_to_plan(identifier)) or ""

        # Idempotência — não processa duas vezes no mesmo dia
        notif_key = _k_notified(uid, date.today().isoformat())
        if _r.exists(notif_key):
            logger.info(f"[SyncPay] Pagamento já processado hoje para uid={uid}")
            return

        _r.setex(notif_key, timedelta(hours=48), "1")
        _r.setex(_k_paid(uid), timedelta(days=365), "1")

        customer = recuperar_customer(uid)
        logger.info(f"[SyncPay] ✅ Pagamento OK: uid={uid} plan={plan_id} R${amount}")

        # Dispara callback registrado (release_vip_access em maya_bot.py)
        if _on_payment:
            try:
                await _on_payment(
                    uid=uid,
                    plan_id=plan_id,
                    amount=float(amount),
                    identifier=identifier,
                    customer=customer,
                )
            except Exception as cb_err:
                logger.error(f"[SyncPay] Erro no callback on_payment: {cb_err}")

        # Limpa chaves temporárias
        _r.delete(_k_id_to_uid(identifier))
        _r.delete(_k_id_to_plan(identifier))
        _r.delete(_k_pix(uid))
        _r.delete(_k_customer(uid))

    except Exception as e:
        logger.error(f"[SyncPay] ❌ Erro _processar_pagamento: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 INIT
# ═══════════════════════════════════════════════════════════════════════════════

def init(flask_app, bot_app, event_loop, redis_conn, on_payment: Callable):
    """
    Inicializa a integração:
      flask_app   — instância Flask pra registrar a rota de webhook
      bot_app     — Application do python-telegram-bot
      event_loop  — loop asyncio do bot (pra run_coroutine_threadsafe)
      redis_conn  — conexão Redis (decode_responses=True recomendado)
      on_payment  — async callback(uid, plan_id, amount, identifier, customer)
                    chamado quando o pagamento é confirmado
    """
    global _r, _loop, _bot_app, _on_payment

    if not config.SYNCPAY_CLIENT_ID or not config.SYNCPAY_CLIENT_SECRET:
        raise RuntimeError(
            "❌ [SyncPay] Configure SYNCPAY_CLIENT_ID e SYNCPAY_CLIENT_SECRET no .env"
        )

    _r          = redis_conn
    _loop       = event_loop
    _bot_app    = bot_app
    _on_payment = on_payment

    _register_webhook_route(flask_app)

    logger.info(
        f"[SyncPay] ✅ Integração iniciada\n"
        f"   Webhook URL: {config.WEBHOOK_BASE_URL}{config.SYNCPAY_WEBHOOK_PATH}\n"
        f"   Client ID:   {config.SYNCPAY_CLIENT_ID[:8]}***"
    )
    logger.info("[SyncPay] 🔔 Registre essa URL no painel SyncPay!")
