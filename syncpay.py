"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              💳 SYNCPAY INTEGRATION — Maya Bot                              ║
║                                                                              ║
║  Versão corrigida:                                                          ║
║    • Idempotência por identifier (não mais por dia)                         ║
║    • Só marca como "processado" APÓS entrega bem-sucedida                   ║
║    • Permite retry automático se a entrega falhar                           ║
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
_on_payment   = None

_token_cache  = {"token": None, "expires_at": None}

# ═══════════════════════════════════════════════════════════════════════════════
# 🔑 REDIS KEYS
# ═══════════════════════════════════════════════════════════════════════════════

def _k_pix(uid):                  return f"sp:pix:{uid}"
def _k_id_to_uid(identifier):     return f"sp:id2uid:{identifier}"
def _k_id_to_plan(identifier):    return f"sp:id2plan:{identifier}"
def _k_paid(uid):                 return f"sp:paid:{uid}"
def _k_processed(identifier):     return f"sp:processed:{identifier}"
def _k_customer(uid):             return f"sp:customer:{uid}"

# ═══════════════════════════════════════════════════════════════════════════════
# 🔐 AUTENTICAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def _get_token() -> str:
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
    token = _get_token()
    webhook_url = f"{config.WEBHOOK_BASE_URL}{config.SYNCPAY_WEBHOOK_PATH}"

    payload = {
        "amount":      round(amount, 2),
        "description": f"{config.BOT_PERSONA_NAME} VIP — uid {uid} — {plan_id}",
        "webhook_url": webhook_url,
        "client": {
            "name":  nome_cliente or "Cliente",
            "cpf":   "00000000000",
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
    data = _r.get(_k_pix(uid))
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 👤 CUSTOMER DATA
# ═══════════════════════════════════════════════════════════════════════════════

def salvar_customer(uid: int, tg_user, plan_id: str) -> dict:
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
# ✅ CONSULTA DE STATUS / FLAGS
# ═══════════════════════════════════════════════════════════════════════════════

def consultar_status(identifier: str) -> Optional[str]:
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
    """Pagamento foi confirmado (webhook recebeu PAID_OUT)."""
    return bool(_r and _r.exists(_k_paid(uid)))


def foi_entregue(identifier: str) -> bool:
    """Entrega do VIP foi feita com sucesso pra esse pagamento."""
    return bool(_r and _r.exists(_k_processed(identifier)))


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
            return jsonify({"ok": False}), 200


async def _processar_pagamento(identifier: str, amount):
    """
    Pagamento confirmado. Idempotência POR IDENTIFIER:
      - Se já foi entregue antes (mesmo identifier) → ignora (webhook duplicado)
      - Se a entrega falhar → não marca como processado, permite retry
    """
    try:
        uid_raw = _r.get(_k_id_to_uid(identifier))
        if not uid_raw:
            logger.warning(f"[SyncPay] identifier={identifier} sem uid no Redis (expirou?)")
            return
        uid = int(uid_raw)

        plan_id = _r.get(_k_id_to_plan(identifier)) or ""

        # ── Idempotência por identifier (NÃO mais por dia) ────────────────────
        # Protege contra webhook duplicado, mas permite reprocessar pagamentos
        # diferentes do mesmo usuário (ele pode comprar várias vezes no mesmo dia).
        if _r.exists(_k_processed(identifier)):
            logger.info(f"[SyncPay] Webhook duplicado p/ identifier={identifier} — ignorando")
            return

        # Marca pagamento confirmado (diferente de "entregue")
        _r.setex(_k_paid(uid), timedelta(days=365), "1")

        customer = recuperar_customer(uid)
        logger.info(f"[SyncPay] ✅ Pagamento OK: uid={uid} plan={plan_id} R${amount}")

        # ── Dispara entrega ───────────────────────────────────────────────────
        delivery_success = False
        if _on_payment:
            try:
                await _on_payment(
                    uid=uid,
                    plan_id=plan_id,
                    amount=float(amount),
                    identifier=identifier,
                    customer=customer,
                )
                delivery_success = True
            except Exception as cb_err:
                logger.error(f"[SyncPay] ❌ Entrega falhou para uid={uid}: {cb_err}")

        # ── Só marca como processado E limpa caches APÓS entrega OK ──────────
        if delivery_success:
            _r.setex(_k_processed(identifier), timedelta(days=7), "1")
            _r.delete(_k_id_to_uid(identifier))
            _r.delete(_k_id_to_plan(identifier))
            _r.delete(_k_pix(uid))
            _r.delete(_k_customer(uid))
            logger.info(f"[SyncPay] 🎉 Entrega concluída e marcada: id={identifier}")
        else:
            logger.warning(
                f"[SyncPay] ⚠️ Entrega falhou — chaves mantidas pra retry. "
                f"Cliente pode clicar 'Verificar Status' pra tentar de novo."
            )

    except Exception as e:
        logger.error(f"[SyncPay] ❌ Erro _processar_pagamento: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 🔁 RETRY MANUAL DE ENTREGA (usado pelo botão "Verificar Status")
# ═══════════════════════════════════════════════════════════════════════════════

async def retry_entrega(identifier: str) -> bool:
    """
    Reprocessa a entrega de um pagamento já confirmado que falhou.
    Retorna True se a entrega foi bem-sucedida.
    """
    try:
        # Já foi entregue? Não faz nada.
        if _r.exists(_k_processed(identifier)):
            logger.info(f"[SyncPay] retry_entrega: id={identifier} já entregue antes")
            return True

        uid_raw = _r.get(_k_id_to_uid(identifier))
        if not uid_raw:
            logger.warning(f"[SyncPay] retry_entrega: id={identifier} sem uid (expirou)")
            return False
        uid = int(uid_raw)

        plan_id  = _r.get(_k_id_to_plan(identifier)) or ""
        customer = recuperar_customer(uid)

        if not _on_payment:
            return False

        await _on_payment(
            uid=uid,
            plan_id=plan_id,
            amount=0.0,
            identifier=identifier,
            customer=customer,
        )

        _r.setex(_k_processed(identifier), timedelta(days=7), "1")
        _r.delete(_k_id_to_uid(identifier))
        _r.delete(_k_id_to_plan(identifier))
        _r.delete(_k_pix(uid))
        _r.delete(_k_customer(uid))
        logger.info(f"[SyncPay] 🔁 Retry de entrega OK: id={identifier} uid={uid}")
        return True

    except Exception as e:
        logger.error(f"[SyncPay] ❌ retry_entrega erro: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 INIT
# ═══════════════════════════════════════════════════════════════════════════════

def init(flask_app, bot_app, event_loop, redis_conn, on_payment: Callable):
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
