"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              📊 META CONVERSIONS API — Maya Bot                              ║
║                                                                              ║
║  Módulo completo de integração com a Meta Conversions API (CAPI).            ║
║                                                                              ║
║  Eventos suportados:                                                         ║
║    • PageView          → fire no Pixel (LP)                                  ║
║    • ViewContent       → LP (clicou em "Ver Conteúdo") — Pixel + CAPI       ║
║    • Lead              → entrou no bot via /start tracking_id (CAPI)         ║
║    • InitiateCheckout  → clicou em um plano (CAPI)                           ║
║    • AddPaymentInfo    → forneceu nome+telefone, PIX gerado (CAPI)           ║
║    • Purchase          → SyncPay confirmou pagamento (CAPI)                  ║
║                                                                              ║
║  Identificadores enviados:                                                   ║
║    • fn, ln            → nome + sobrenome (SHA-256, lowercase)               ║
║    • ph                → telefone E.164 sem '+' (SHA-256) — coletado no bot ║
║    • external_id       → telegram_user_id (SHA-256)                          ║
║    • fbc, fbp          → cookies do navegador (raw)                          ║
║    • client_ip_address → IP capturado na LP (raw)                            ║
║    • client_user_agent → UA capturado na LP (raw)                            ║
║    • country           → "br" (SHA-256)                                      ║
║                                                                              ║
║  Deduplicação: cada evento tem event_id único compartilhado com o Pixel.     ║
║  External_id é sempre o telegram_user_id pra Meta costurar os eventos        ║
║  do mesmo usuário no funil.                                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import uuid
import hashlib
import logging
import re
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from urllib.parse import urlencode

import config

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  ESTADO INTERNO (injetado via init)
# ═══════════════════════════════════════════════════════════════════════════════

_r = None  # Redis connection


# ═══════════════════════════════════════════════════════════════════════════════
# 🔑 REDIS KEYS
# ═══════════════════════════════════════════════════════════════════════════════

def _k_track(tracking_id: str) -> str:
    """Dados de tracking vindos da LP, indexados por tracking_id."""
    return f"capi:track:{tracking_id}"


def _k_user_track(uid: int) -> str:
    """tracking_id atual associado a um telegram user_id (lookup reverso)."""
    return f"capi:user2track:{uid}"


def _k_user_pii(uid: int) -> str:
    """Nome, sobrenome, telefone, CPF coletados do usuário no bot."""
    return f"capi:pii:{uid}"


def _k_event_id(uid: int, event_name: str) -> str:
    """event_id pra reuso/dedup entre Pixel e CAPI quando aplicável."""
    return f"capi:eventid:{uid}:{event_name}"


# ═══════════════════════════════════════════════════════════════════════════════
# 🔐 HASHING & NORMALIZAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def _sha256(value: str) -> str:
    """SHA-256 lowercase hex. Trim + lowercase antes de hashear."""
    if value is None:
        return None
    value = str(value).strip().lower()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_phone_br(phone: str) -> Optional[str]:
    """
    Normaliza telefone brasileiro pra E.164 SEM o '+'.
        '(31) 99999-9999'  → '5531999999999'
        '+55 31 99999-9999'→ '5531999999999'
        '31999999999'      → '5531999999999'
        '5531999999999'    → '5531999999999'

    Aceita também números fixos (sem 9 no início do número).
    Retorna None se inválido.
    """
    if not phone:
        return None

    digits = re.sub(r"\D", "", str(phone))

    if not digits:
        return None

    # Já vem com DDI 55
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits

    # Vem sem DDI: 10 (fixo) ou 11 (celular) dígitos
    if len(digits) in (10, 11):
        return "55" + digits

    # Algum formato estranho — devolve só os dígitos pra Meta tentar igual
    if 10 <= len(digits) <= 15:
        return digits

    return None



def split_name(full_name: str) -> tuple:
    """
    'Vinicius Murilo Pereira Pimenta' → ('vinicius', 'pimenta')
    O Meta usa só fn (primeiro nome) e ln (último sobrenome).
    """
    if not full_name:
        return (None, None)
    parts = [p for p in str(full_name).strip().split() if p]
    if not parts:
        return (None, None)
    if len(parts) == 1:
        return (parts[0].lower(), None)
    return (parts[0].lower(), parts[-1].lower())


def build_fbc_from_fbclid(fbclid: str, timestamp_ms: Optional[int] = None) -> Optional[str]:
    """
    Constrói o cookie _fbc a partir do parâmetro fbclid da URL.
    Formato oficial: fb.{subdomainIndex}.{timestamp}.{fbclid}
    Para Brasil/domínio único, subdomainIndex = 1.
    """
    if not fbclid:
        return None
    ts = timestamp_ms or int(time.time() * 1000)
    return f"fb.1.{ts}.{fbclid}"


# ═══════════════════════════════════════════════════════════════════════════════
# 📦 CONSTRUÇÃO DE user_data
# ═══════════════════════════════════════════════════════════════════════════════

def build_user_data(
    *,
    telegram_user_id: Optional[int] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    fbc: Optional[str] = None,
    fbp: Optional[str] = None,
    client_ip: Optional[str] = None,
    client_ua: Optional[str] = None,
    country: str = "br",
) -> Dict[str, Any]:
    """
    Monta o objeto user_data do CAPI seguindo as regras de hashing do Meta.

    Tudo que é PII vai hasheado em SHA-256 (lowercase hex).
    fbc, fbp, ip e user_agent vão RAW.

    Devolve um dict pronto pra ser colocado em payload['data'][i]['user_data'].
    """
    ud: Dict[str, Any] = {}

    # === Nome ===
    fn, ln = None, None
    if full_name:
        fn, ln = split_name(full_name)
    if first_name:
        fn = first_name.strip().lower()
    if last_name:
        ln = last_name.strip().lower()

    if fn:
        ud["fn"] = [_sha256(fn)]
    if ln:
        ud["ln"] = [_sha256(ln)]

    # === Telefone ===
    norm_phone = normalize_phone_br(phone)
    if norm_phone:
        ud["ph"] = [_sha256(norm_phone)]

    # === External ID ===
    ext_ids: List[str] = []
    if telegram_user_id is not None:
        ext_ids.append(_sha256(str(telegram_user_id)))
    if ext_ids:
        ud["external_id"] = ext_ids

    # === País ===
    if country:
        ud["country"] = [_sha256(country)]

    # === Parâmetros browser (RAW, sem hash) ===
    if fbc:
        ud["fbc"] = fbc
    if fbp:
        ud["fbp"] = fbp
    if client_ip:
        ud["client_ip_address"] = client_ip
    if client_ua:
        ud["client_user_agent"] = client_ua

    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# 📡 DISPATCH — envia evento pra Graph API
# ═══════════════════════════════════════════════════════════════════════════════

def _build_endpoint() -> str:
    return (
        f"https://graph.facebook.com/{config.META_API_VERSION}"
        f"/{config.META_PIXEL_ID}/events"
        f"?access_token={config.META_ACCESS_TOKEN}"
    )


def send_event(
    *,
    event_name: str,
    event_id: Optional[str] = None,
    event_time: Optional[int] = None,
    user_data: Dict[str, Any],
    custom_data: Optional[Dict[str, Any]] = None,
    event_source_url: Optional[str] = None,
    action_source: str = "website",
) -> Dict[str, Any]:
    """
    Envia um evento pra Conversions API. Síncrono, com timeout curto.

    Retorna o JSON da resposta da Meta. Em caso de erro, loga e devolve
    {"error": "..."} sem levantar exceção (não pode quebrar o fluxo do bot).
    """
    if not config.META_PIXEL_ID or not config.META_ACCESS_TOKEN:
        logger.warning("[CAPI] Pixel ID ou Access Token não configurados — evento ignorado")
        return {"error": "not_configured"}

    event_id = event_id or str(uuid.uuid4())
    event_time = event_time or int(time.time())

    event_data = {
        "event_name": event_name,
        "event_time": event_time,
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user_data,
    }

    if event_source_url:
        event_data["event_source_url"] = event_source_url
    elif action_source == "website":
        # Required quando action_source = website
        event_data["event_source_url"] = config.LANDING_PAGE_URL

    if custom_data:
        event_data["custom_data"] = custom_data

    payload: Dict[str, Any] = {"data": [event_data]}

    # Test Events Code (deixar setado em config durante setup, remover em produção)
    if config.META_TEST_EVENT_CODE:
        payload["test_event_code"] = config.META_TEST_EVENT_CODE

    endpoint = _build_endpoint()

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        result = resp.json() if resp.content else {}

        if resp.status_code >= 400:
            logger.error(
                f"[CAPI] ❌ {event_name} HTTP {resp.status_code}: {result}"
            )
            return {"error": f"http_{resp.status_code}", "details": result}

        events_received = result.get("events_received", 0)
        fbtrace_id = result.get("fbtrace_id", "?")
        logger.info(
            f"[CAPI] ✅ {event_name} ok — events_received={events_received} "
            f"event_id={event_id} fbtrace={fbtrace_id}"
        )
        return result

    except requests.Timeout:
        logger.error(f"[CAPI] ⏱️ timeout enviando {event_name}")
        return {"error": "timeout"}
    except Exception as e:
        logger.exception(f"[CAPI] ❌ exceção enviando {event_name}: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# 💾 STORAGE — tracking_id ↔ telegram_user_id ↔ PII
# ═══════════════════════════════════════════════════════════════════════════════

def save_landing_tracking(
    tracking_id: str,
    *,
    fbc: Optional[str] = None,
    fbp: Optional[str] = None,
    fbclid: Optional[str] = None,
    client_ip: Optional[str] = None,
    client_ua: Optional[str] = None,
    event_source_url: Optional[str] = None,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    utm_content: Optional[str] = None,
    utm_term: Optional[str] = None,
    ttl_hours: int = 72,
) -> None:
    """Guarda os parâmetros capturados na LP, indexados pelo tracking_id."""
    # Se veio só fbclid (sem fbc montado), monta aqui
    if not fbc and fbclid:
        fbc = build_fbc_from_fbclid(fbclid)

    data = {
        "fbc": fbc or "",
        "fbp": fbp or "",
        "fbclid": fbclid or "",
        "client_ip": client_ip or "",
        "client_ua": client_ua or "",
        "event_source_url": event_source_url or config.LANDING_PAGE_URL,
        "utm_source": utm_source or "",
        "utm_medium": utm_medium or "",
        "utm_campaign": utm_campaign or "",
        "utm_content": utm_content or "",
        "utm_term": utm_term or "",
        "created_at": datetime.utcnow().isoformat(),
    }
    _r.setex(_k_track(tracking_id), timedelta(hours=ttl_hours), json.dumps(data))
    logger.info(f"[CAPI] 🎯 tracking saved: id={tracking_id} fbc={'✓' if fbc else '✗'} fbp={'✓' if fbp else '✗'}")


def get_landing_tracking(tracking_id: str) -> Optional[Dict[str, Any]]:
    if not tracking_id:
        return None
    raw = _r.get(_k_track(tracking_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def link_user_to_tracking(uid: int, tracking_id: str, ttl_days: int = 30) -> None:
    """Liga telegram_user_id ↔ tracking_id (lookup nos dois sentidos)."""
    _r.setex(_k_user_track(uid), timedelta(days=ttl_days), tracking_id)
    logger.info(f"[CAPI] 🔗 link uid={uid} ↔ tracking_id={tracking_id}")


def get_user_tracking(uid: int) -> Optional[Dict[str, Any]]:
    """Retorna o tracking data salvo pra esse usuário."""
    tid = _r.get(_k_user_track(uid))
    if not tid:
        return None
    return get_landing_tracking(tid)


def save_user_pii(
    uid: int,
    *,
    full_name: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    ttl_days: int = 30,
) -> None:
    """
    Atualiza o pacote de PII coletado do usuário. Merge incremental:
    a cada coleta nova, o que já existia é mantido.
    """
    key = _k_user_pii(uid)
    existing = {}
    raw = _r.get(key)
    if raw:
        try:
            existing = json.loads(raw)
        except Exception:
            pass

    if full_name:    existing["full_name"]  = full_name
    if first_name:   existing["first_name"] = first_name
    if last_name:    existing["last_name"]  = last_name
    if phone:
        normalized = normalize_phone_br(phone)
        if normalized:
            existing["phone"] = normalized

    existing["updated_at"] = datetime.utcnow().isoformat()
    _r.setex(key, timedelta(days=ttl_days), json.dumps(existing))


def get_user_pii(uid: int) -> Dict[str, Any]:
    raw = _r.get(_k_user_pii(uid))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def remember_event_id(uid: int, event_name: str, event_id: str, ttl_hours: int = 24) -> None:
    """Guarda event_id pra reuso/auditoria (Pixel <-> CAPI dedup)."""
    _r.setex(_k_event_id(uid, event_name), timedelta(hours=ttl_hours), event_id)


def get_event_id(uid: int, event_name: str) -> Optional[str]:
    return _r.get(_k_event_id(uid, event_name))


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 HELPERS DE EVENTO — funções high-level pro bot chamar
# ═══════════════════════════════════════════════════════════════════════════════

def _build_user_data_from_storage(
    uid: int,
    *,
    extra_first_name: Optional[str] = None,
    extra_last_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Junta tracking + PII pra montar o user_data mais rico possível."""
    tracking = get_user_tracking(uid) or {}
    pii = get_user_pii(uid)

    return build_user_data(
        telegram_user_id=uid,
        full_name=pii.get("full_name"),
        first_name=extra_first_name or pii.get("first_name"),
        last_name=extra_last_name or pii.get("last_name"),
        phone=pii.get("phone"),
        fbc=tracking.get("fbc") or None,
        fbp=tracking.get("fbp") or None,
        client_ip=tracking.get("client_ip") or None,
        client_ua=tracking.get("client_ua") or None,
        country="br",
    )


def _event_source_url(uid: int) -> str:
    tracking = get_user_tracking(uid) or {}
    return tracking.get("event_source_url") or config.LANDING_PAGE_URL


def send_lead(
    uid: int,
    *,
    telegram_first_name: Optional[str] = None,
    telegram_last_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dispara Lead quando o usuário entra no bot via /start tracking_id.
    Nesse momento ainda não temos telefone, mas temos fbc/fbp/ip/ua da LP
    e first_name/last_name do Telegram → EMQ ~6-7.
    """
    user_data = _build_user_data_from_storage(
        uid,
        extra_first_name=telegram_first_name,
        extra_last_name=telegram_last_name,
    )

    event_id = f"lead_{uid}_{int(time.time())}"
    remember_event_id(uid, "Lead", event_id)

    return send_event(
        event_name="Lead",
        event_id=event_id,
        user_data=user_data,
        event_source_url=_event_source_url(uid),
    )


def send_view_content(
    *,
    tracking_id: str,
    event_id: Optional[str] = None,
    content_name: str = "Landing Page",
) -> Dict[str, Any]:
    """
    Dispara ViewContent quando o usuário clica em "Ver Conteúdo" na LP.
    Esse é o evento de "intenção" antes de entrar no bot.

    O event_id pode ser fornecido pelo frontend (mesmo do Pixel) pra
    deduplicação automática.
    """
    tracking = get_landing_tracking(tracking_id) or {}

    user_data = build_user_data(
        fbc=tracking.get("fbc") or None,
        fbp=tracking.get("fbp") or None,
        client_ip=tracking.get("client_ip") or None,
        client_ua=tracking.get("client_ua") or None,
        country="br",
    )

    return send_event(
        event_name="ViewContent",
        event_id=event_id or f"vc_{tracking_id}",
        user_data=user_data,
        custom_data={"content_name": content_name},
        event_source_url=tracking.get("event_source_url") or config.LANDING_PAGE_URL,
    )


def send_initiate_checkout(
    uid: int,
    *,
    plan_id: str,
    plan_name: str,
    value: float,
    currency: str = "BRL",
) -> Dict[str, Any]:
    """Disparado quando o usuário escolhe um plano."""
    user_data = _build_user_data_from_storage(uid)

    event_id = f"ic_{uid}_{plan_id}_{int(time.time())}"
    remember_event_id(uid, "InitiateCheckout", event_id)

    return send_event(
        event_name="InitiateCheckout",
        event_id=event_id,
        user_data=user_data,
        custom_data={
            "currency": currency,
            "value": round(float(value), 2),
            "content_ids": [plan_id],
            "content_name": plan_name,
            "content_type": "product",
            "num_items": 1,
        },
        event_source_url=_event_source_url(uid),
    )


def send_add_payment_info(
    uid: int,
    *,
    plan_id: str,
    plan_name: str,
    value: float,
    currency: str = "BRL",
) -> Dict[str, Any]:
    """Disparado quando o PIX é gerado (já temos nome+telefone)."""
    user_data = _build_user_data_from_storage(uid)

    event_id = f"api_{uid}_{plan_id}_{int(time.time())}"
    remember_event_id(uid, "AddPaymentInfo", event_id)

    return send_event(
        event_name="AddPaymentInfo",
        event_id=event_id,
        user_data=user_data,
        custom_data={
            "currency": currency,
            "value": round(float(value), 2),
            "content_ids": [plan_id],
            "content_name": plan_name,
            "content_type": "product",
        },
        event_source_url=_event_source_url(uid),
    )


def send_purchase(
    uid: int,
    *,
    transaction_id: str,
    plan_id: str,
    plan_name: str,
    value: float,
    currency: str = "BRL",
    event_time: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Disparado quando o webhook da SyncPay confirma o pagamento.
    Usa transaction_id como event_id (idempotência: Meta dedupa se o mesmo
    transaction_id chegar 2x por retry).
    """
    user_data = _build_user_data_from_storage(uid)

    event_id = f"purchase_{transaction_id}"
    remember_event_id(uid, "Purchase", event_id)

    return send_event(
        event_name="Purchase",
        event_id=event_id,
        event_time=event_time,
        user_data=user_data,
        custom_data={
            "currency": currency,
            "value": round(float(value), 2),
            "content_ids": [plan_id],
            "content_name": plan_name,
            "content_type": "product",
            "num_items": 1,
            "order_id": transaction_id,
        },
        event_source_url=_event_source_url(uid),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 INIT
# ═══════════════════════════════════════════════════════════════════════════════

def init(redis_conn) -> None:
    """Inicializa o módulo CAPI."""
    global _r
    _r = redis_conn

    if not config.META_PIXEL_ID:
        logger.warning("[CAPI] ⚠️ META_PIXEL_ID não configurado — eventos serão ignorados")
    if not config.META_ACCESS_TOKEN:
        logger.warning("[CAPI] ⚠️ META_ACCESS_TOKEN não configurado — eventos serão ignorados")
    if config.META_TEST_EVENT_CODE:
        logger.warning(
            f"[CAPI] 🧪 TEST_EVENT_CODE ativo: {config.META_TEST_EVENT_CODE}\n"
            f"   Eventos NÃO contam pra produção. Remova após validar no Events Manager."
        )

    logger.info(
        f"[CAPI] ✅ inicializado — pixel={config.META_PIXEL_ID[:6] if config.META_PIXEL_ID else '?'}*** "
        f"api={config.META_API_VERSION}"
    )
