"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              🌐 LANDING ROUTES — Maya Bot                                    ║
║                                                                              ║
║  Rotas Flask adicionadas ao mesmo app que serve o webhook da SyncPay:        ║
║                                                                              ║
║    GET  /              → serve landing/index.html (o botão "Ver Conteúdo")  ║
║    POST /api/track     → recebe fbp/fbclid/UTMs, registra tracking_id,      ║
║                          dispara ViewContent CAPI, devolve URL do bot       ║
║                                                                              ║
║  O frontend dispara ViewContent no Pixel com o MESMO event_id que mandamos  ║
║  via CAPI → Meta deduplica automaticamente.                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import uuid
import logging
import secrets
from pathlib import Path
from flask import request, jsonify, send_from_directory

import config
import capi

logger = logging.getLogger(__name__)

LANDING_DIR = Path(__file__).parent / "landing"


# ═══════════════════════════════════════════════════════════════════════════════
# 🔎  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_client_ip() -> str:
    """
    Pega o IP real do cliente respeitando proxies/CDN.
    Ordem: CF-Connecting-IP > X-Forwarded-For > X-Real-IP > request.remote_addr
    """
    headers = request.headers
    if cf := headers.get("CF-Connecting-IP"):
        return cf.strip()
    if xff := headers.get("X-Forwarded-For"):
        # X-Forwarded-For pode ser "ip1, ip2, ip3" → primeiro é o cliente real
        return xff.split(",")[0].strip()
    if xreal := headers.get("X-Real-IP"):
        return xreal.strip()
    return request.remote_addr or ""


def _bot_url(tracking_id: str) -> str:
    """Monta t.me/BOT_USERNAME?start=tracking_id."""
    bot_username = config.BOT_USERNAME.lstrip("@")
    return f"https://t.me/{bot_username}?start={tracking_id}"


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀 REGISTRO DAS ROTAS
# ═══════════════════════════════════════════════════════════════════════════════

def register_routes(flask_app):
    """Chamado uma vez do bot.py main()."""

    # ─────────────────────────────────────────────────────────────────────────
    # GET /  → serve a landing page
    # ─────────────────────────────────────────────────────────────────────────
    @flask_app.route("/", methods=["GET"])
    def landing_page():
        # Lê o HTML e injeta valores de runtime (pixel id, fallback bot url)
        html = (LANDING_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("YOUR_PIXEL_ID", config.META_PIXEL_ID or "")
        html = html.replace(
            "FALLBACK_BOT_URL",
            f"https://t.me/{config.BOT_USERNAME.lstrip('@')}",
        )
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    # ─────────────────────────────────────────────────────────────────────────
    # POST /api/track  → registra tracking + dispara ViewContent via CAPI
    # ─────────────────────────────────────────────────────────────────────────
    @flask_app.route("/api/track", methods=["POST"])
    def api_track():
        try:
            body = request.get_json(silent=True) or {}

            # tracking_id curto e seguro (16 chars, alfanumérico)
            tracking_id = secrets.token_urlsafe(12)

            client_ip = _get_client_ip()
            client_ua = body.get("user_agent") or request.headers.get("User-Agent", "")

            capi.save_landing_tracking(
                tracking_id,
                fbp           = body.get("fbp") or None,
                fbclid        = body.get("fbclid") or None,
                client_ip     = client_ip,
                client_ua     = client_ua,
                event_source_url = body.get("page_url") or config.LANDING_PAGE_URL,
                utm_source    = body.get("utm_source"),
                utm_medium    = body.get("utm_medium"),
                utm_campaign  = body.get("utm_campaign"),
                utm_content   = body.get("utm_content"),
                utm_term      = body.get("utm_term"),
            )

            # Dispara ViewContent via CAPI (mesmo event_id do Pixel → dedup)
            event_id = body.get("event_id") or f"vc_{tracking_id}"
            try:
                capi.send_view_content(
                    tracking_id = tracking_id,
                    event_id    = event_id,
                    content_name= "Landing Maya",
                )
            except Exception as e:
                # Falha em CAPI não pode bloquear o redirect
                logger.warning(f"[LP] ViewContent CAPI falhou: {e}")

            return jsonify({
                "tracking_id": tracking_id,
                "bot_url":     _bot_url(tracking_id),
            }), 200

        except Exception as e:
            logger.exception(f"[LP] /api/track erro: {e}")
            return jsonify({"error": str(e)}), 500

    logger.info(f"[LP] ✅ Rotas registradas — GET / e POST /api/track")
