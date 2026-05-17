"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              💳 SYNCPAY INTEGRATION — Maya Bot                              ║
║                                                                              ║
║  Versão CAPI-aware:                                                          ║
║    • gerar_pix() agora recebe telefone real coletado no bot                  ║
║    • _processar_pagamento() extrai CPF/nome reais do webhook                 ║
║    • Após entrega bem-sucedida, dispara Purchase via CAPI                    ║
║    • Idempotência por identifier preservada                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import logging
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Optional, Callable

from flask import request as flask_request, jsonify

import config
import capi

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  ESTADO INTERNO
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

def gerar_pix(uid: int, amount: float, plan_id: str,
              nome_cliente: str = "Cliente",
              telefone: Optional[str] = None,
              cpf: Optional[str] = None) -> dict:
    """
    Gera um PIX via SyncPay.
    Agora aceita telefone e CPF reais (vindos da coleta no bot).
    Quando o user pagar o PIX, o CPF/nome reais do pagador volta no webhook.
    """
    token = _get_token()
    webhook_url = f"{config.WEBHOOK_BASE_URL}{config.SYNCPAY_WEBHOOK_PATH}"

    # Telefone normalizado pro formato que a SyncPay espera (só dígitos com DDD)
    tel = telefone or ""
    if tel.startswith("55") and len(tel) > 11:
        tel = tel[2:]  # remove DDI '55' — SyncPay quer só DDD+número

    payload = {
        "amount":      round(amount, 2),
        "description": f"{config.BOT_PERSONA_NAME} VIP — uid {uid} — {plan_id}",
        "webhook_url": webhook_url,
        "client": {
            "name":  nome_cliente or "Cliente",
            "cpf":   cpf or "00000000000",   # SyncPay aceita placeholder; o CPF real vem do pagador
            "email": f"user{uid}@{config.BOT_PERSONA_NAME.lower()}bot.com",
            "phone": tel or "11999999999",
        },
        # ── External reference: amarra esse PIX ao usuário (útil pra reconciliação)
        "external_reference": f"uid_{uid}",
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
        "language_code": getattr(tg_user, "language_code", None) or "pt-br",
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
    return bool(_r and _r.exists(_k_paid(uid)))


def foi_entregue(identifier: str) -> bool:
    return bool(_r and _r.exists(_k_processed(identifier)))


# ═══════════════════════════════════════════════════════════════════════════════
# 🔎 EXTRAÇÃO DE DADOS DO WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_customer_from_webhook(transacao: dict) -> dict:
    """
    Extrai nome, CPF, email, telefone do payload da SyncPay.
    A estrutura exata varia — tenta múltiplas chaves possíveis.

    Ex.: o user mostrou que vem 'Cliente: Vinicius Murilo Pereira Pimenta'
    e 'CPF: 455.620.518-26'.
    """
    client = (
        transacao.get("client")
        or transacao.get("customer")
        or transacao.get("payer")
        or {}
    )

    return {
        "name":  client.get("name")  or client.get("full_name") or transacao.get("client_name")  or "",
        "cpf":   client.get("cpf")   or client.get("document")  or transacao.get("cpf")          or "",
        "email": client.get("email") or transacao.get("email") or "",
        "phone": client.get("phone") or client.get("telefone") or transacao.get("phone")        or "",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 🌐 WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

def _register_webhook_route(flask_app):
    @flask_app.route(config.SYNCPAY_WEBHOOK_PATH, methods=["POST"])
    def syncpay_webhook():
        try:
            data       = flask_request.get_json(silent=True) or {}
            transacao  = data.get("data", data)  # algumas APIs envelopam, outras não
            identifier = transacao.get("id") or transacao.get("identifier")
            status     = transacao.get("status")
            amount     = transacao.get("final_amount") or transacao.get("amount")

            logger.info(f"[SyncPay Webhook] id={identifier} status={status} valor={amount}")

            if status in ["completed", "PAID_OUT"] and identifier:
                # Extrai dados do pagador (CPF, nome reais)
                customer_data = _extract_customer_from_webhook(transacao)

                asyncio.run_coroutine_threadsafe(
                    _processar_pagamento(identifier, amount, customer_data),
                    _loop
                )
            else:
                logger.info(f"[SyncPay] Status ignorado (ainda não pago): {status}")
            return jsonify({"ok": True}), 200
        except Exception as e:
            logger.error(f"[SyncPay Webhook] Erro: {e}")
            return jsonify({"ok": False}), 200


async def _processar_pagamento(identifier: str, amount, customer_data: dict):
    """
    Pagamento confirmado. Fluxo:
      1) Idempotência por identifier
      2) Marca pagamento confirmado
      3) Atualiza PII no capi com dados reais do pagador (CPF, nome)
      4) Dispara entrega VIP
      5) Se entrega OK: dispara Purchase CAPI + limpa caches
    """
    try:
        uid_raw = _r.get(_k_id_to_uid(identifier))
        if not uid_raw:
            logger.warning(f"[SyncPay] identifier={identifier} sem uid no Redis (expirou?)")
            return
        uid = int(uid_raw)

        plan_id = _r.get(_k_id_to_plan(identifier)) or ""

        # ── Idempotência por identifier ──────────────────────────────────────
        if _r.exists(_k_processed(identifier)):
            logger.info(f"[SyncPay] Webhook duplicado p/ identifier={identifier} — ignorando")
            return

        # Marca pagamento confirmado
        _r.setex(_k_paid(uid), timedelta(days=365), "1")

        # ── Atualiza PII no CAPI com dados reais do pagador ─────────────────
        # Isso enriquece o Purchase: CPF + nome reais (vindos do pagamento PIX)
        if customer_data.get("cpf") or customer_data.get("name"):
            capi.save_user_pii(
                uid,
                full_name=customer_data.get("name") or None,
                cpf=customer_data.get("cpf") or None,
            )
            logger.info(
                f"[SyncPay] 📥 PII atualizada do webhook: "
                f"nome={'✓' if customer_data.get('name') else '✗'} "
                f"cpf={'✓' if customer_data.get('cpf') else '✗'}"
            )

        customer = recuperar_customer(uid)
        logger.info(f"[SyncPay] ✅ Pagamento OK: uid={uid} plan={plan_id} R${amount}")

        # ── Dispara entrega ──────────────────────────────────────────────────
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

        # ── Só marca como processado e dispara Purchase APÓS entrega OK ─────
        if delivery_success:
            _r.setex(_k_processed(identifier), timedelta(days=7), "1")

            # ── Dispara Purchase via CAPI (fire-and-forget) ────────────────
            plan = config.PLANS.get(plan_id, {})
            try:
                await asyncio.to_thread(
                    capi.send_purchase,
                    uid,
                    transaction_id=str(identifier),
                    plan_id=plan_id,
                    plan_name=plan.get("name", plan_id),
                    value=float(amount),
                )
            except Exception as capi_err:
                logger.error(f"[SyncPay] ❌ Falha disparando Purchase CAPI: {capi_err}")

            # ── Limpa caches (PII fica preservada pra histórico) ───────────
            _r.delete(_k_id_to_uid(identifier))
            _r.delete(_k_id_to_plan(identifier))
            _r.delete(_k_pix(uid))
            _r.delete(_k_customer(uid))
            logger.info(f"[SyncPay] 🎉 Entrega concluída + Purchase CAPI: id={identifier}")
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
    try:
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

        # Tenta disparar Purchase no retry também (com amount=0 se não temos)
        # Importante: no retry o amount real foi perdido — se quiser preservar,
        # salve o amount junto com _k_id_to_uid quando o webhook chegar.
        # Pra ter dado correto, aqui buscamos o status do pagamento.
        try:
            status_data = _get_payment_full(identifier)
            real_amount = float(status_data.get("amount", 0))
            plan = config.PLANS.get(plan_id, {})
            if real_amount > 0:
                await asyncio.to_thread(
                    capi.send_purchase,
                    uid,
                    transaction_id=str(identifier),
                    plan_id=plan_id,
                    plan_name=plan.get("name", plan_id),
                    value=real_amount,
                )
        except Exception as capi_err:
            logger.error(f"[SyncPay] ❌ Retry CAPI Purchase falhou: {capi_err}")

        _r.delete(_k_id_to_uid(identifier))
        _r.delete(_k_id_to_plan(identifier))
        _r.delete(_k_pix(uid))
        _r.delete(_k_customer(uid))
        logger.info(f"[SyncPay] 🔁 Retry de entrega OK: id={identifier} uid={uid}")
        return True

    except Exception as e:
        logger.error(f"[SyncPay] ❌ retry_entrega erro: {e}")
        return False


def _get_payment_full(identifier: str) -> dict:
    """Busca o pagamento completo (pra pegar o amount real no retry)."""
    try:
        token = _get_token()
        resp = requests.get(
            f"{config.SYNCPAY_BASE_URL}/cash-in/{identifier}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as e:
        logger.error(f"[SyncPay] _get_payment_full erro: {e}")
        return {}


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
