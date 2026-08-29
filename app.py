import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from pywebpush import webpush, WebPushException
from py_vapid import Vapid

# =============================================
# LOGGING SETUP
# =============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================
# APP SETUP
# =============================================
app = Flask(__name__)
CORS(app, origins="*")

# =============================================
# CONFIG — Render pe Environment Variables set karo
# =============================================
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@rakeshmart.com")
AUTH_KEY = os.environ.get("AUTH_KEY", "")

SUBSCRIPTIONS_FILE = "subscriptions.json"


import requests

GAS_SECRET = os.environ.get("GAS_SCHED_NOTIF_SECRET", "")  # SCHED_NOTIF_SECRET wahi value jo GAS Script Properties mein hai

def sync_subscription_to_gas(sub_info):
    if not GAS_SCRIPT_URL or not GAS_SECRET:
        return
    requests.post(GAS_SCRIPT_URL, json={
        "action": "registerPushSubscription",
        "key": GAS_SECRET,
        "subscription": sub_info
    }, timeout=10)

def deactivate_subscription_in_gas(endpoint):
    if not GAS_SCRIPT_URL or not GAS_SECRET:
        return
    requests.post(GAS_SCRIPT_URL, json={
        "action": "deactivatePushSubscription",
        "key": GAS_SECRET,
        "endpoint": endpoint
    }, timeout=10)

def normalize_subscription(sub):
    if not isinstance(sub, dict):
        return None

    endpoint = str(sub.get("endpoint", "")).strip()
    keys = sub.get("keys") or {}

    p256dh = str(
        keys.get("p256dh") or sub.get("p256dh") or ""
    ).strip()

    auth = str(
        keys.get("auth") or sub.get("auth") or ""
    ).strip()

    if not endpoint or not p256dh or not auth:
        logger.warning(
            "Skipping invalid subscription: "
            f"endpoint={bool(endpoint)}, "
            f"p256dh={bool(p256dh)}, "
            f"auth={bool(auth)}"
        )
        return None

    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": p256dh,
            "auth": auth
        }
    }


def load_subscriptions_from_gas():
    """Google Sheet se subscriptions load karke WebPush format mein convert karo."""

    if not GAS_SCRIPT_URL or not GAS_SECRET:
        return None

    try:
        r = requests.get(
            GAS_SCRIPT_URL,
            params={
                "action": "getPushSubscriptions",
                "key": GAS_SECRET
            },
            timeout=10
        )

        r.raise_for_status()

        data = r.json()
        raw_subs = data.get("subscriptions", [])

        if not isinstance(raw_subs, list):
            return []

        normalized = []

        for sub in raw_subs:
            clean = normalize_subscription(sub)

            if clean:
                normalized.append(clean)

        logger.info(
            f"GAS subscriptions loaded: "
            f"{len(raw_subs)} raw → {len(normalized)} valid"
        )

        return normalized

    except Exception as e:
        logger.error(
            f"load_subscriptions_from_gas error: {e}"
        )
        return None
# =============================================
# SUBSCRIPTION STORAGE
# Primary: subscriptions.json file
# Fallback: SUBSCRIPTIONS_BACKUP env var (Render restart pe bhi safe)
# =============================================
def load_subscriptions():
    # STEP 1: GAS sheet — asli persistent storage (Render restart-safe)
    gas_subs = load_subscriptions_from_gas()
    if gas_subs is not None and len(gas_subs) > 0:
        save_subscriptions(gas_subs)  # local file mein bhi cache kar lo (fast reads ke liye)
        return gas_subs

    # STEP 2: local file se load karo (GAS unreachable ho to fallback)
    try:
        if os.path.exists(SUBSCRIPTIONS_FILE):
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
    except Exception as e:
        logger.error(f"Load subscriptions file error: {e}")

    # File nahi mili ya empty — env var se restore karo
    try:
        backup = os.environ.get("SUBSCRIPTIONS_BACKUP", "")
        if backup:
            data = json.loads(backup)
            if isinstance(data, list):
                logger.info(f"Restored {len(data)} subscriptions from env backup")
                # File mein bhi save kar lo
                save_subscriptions(data)
                return data
    except Exception as e:
        logger.error(f"Load subscriptions env error: {e}")

    return []

def save_subscriptions(subs):
    try:
        with open(SUBSCRIPTIONS_FILE, "w") as f:
            json.dump(subs, f, indent=2)
    except Exception as e:
        logger.error(f"Save subscriptions error: {e}")

def add_subscription(sub_info):
    if not isinstance(sub_info, dict):
        return False

    endpoint = str(sub_info.get("endpoint", "")).strip()
    keys = sub_info.get("keys") or {}

    p256dh = str(keys.get("p256dh", "")).strip()
    auth = str(keys.get("auth", "")).strip()

    if not endpoint or not p256dh or not auth:
        logger.warning("Refused invalid subscription")
        return False

    clean = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": p256dh,
            "auth": auth
        }
    }

    subs = load_subscriptions()

    for existing in subs:
        if existing.get("endpoint") == endpoint:
            logger.info(
                f"Subscription already exists: {endpoint[:50]}..."
            )
            return False

    subs.append(clean)
    save_subscriptions(subs)

    logger.info(
        f"New subscription added. Total: {len(subs)}"
    )

    return True

def remove_subscription(endpoint):
    subs = load_subscriptions()
    before = len(subs)
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    save_subscriptions(subs)
    logger.info(f"Removed subscription. {before} -> {len(subs)}")

# =============================================
# AUTH CHECK
# =============================================
def check_auth():
    key = request.headers.get("X-Auth-Key", "")
    return key == AUTH_KEY

# =============================================
# ROUTES
# =============================================

@app.route("/", methods=["GET", "HEAD"])
def home():
    subs = load_subscriptions()
    return jsonify({
        "status": "ok",
        "service": "RakeshMart Push Notification Server",
        "subscriptions": len(subs),
        "vapid_configured": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)
    })

# ---- VAPID Public Key dena (frontend ko chahiye) ----
@app.route("/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

# ---- Admin device ka subscription save karo ----
GAS_SCRIPT_URL = os.environ.get("GAS_SCRIPT_URL", "")  # Apps Script /exec URL — Render env var mein set karo

def verify_admin_reg_token(token):
    """GAS se check karta hai ki ye one-time token valid admin session se bana tha."""
    if not GAS_SCRIPT_URL or not token:
        return False
    try:
        import requests
        r = requests.get(GAS_SCRIPT_URL, params={"action": "verifyPushRegToken", "token": token}, timeout=10)
        return bool(r.json().get("valid"))
    except Exception as e:
        logger.error(f"verify_admin_reg_token error: {e}")
        return False

@app.route("/subscribe", methods=["POST"])
def subscribe():
    try:
        data = request.get_json(silent=True) or {}

        endpoint = str(data.get("endpoint", "")).strip()
        keys = data.get("keys") or {}

        p256dh = str(keys.get("p256dh", "")).strip()
        auth = str(keys.get("auth", "")).strip()

        reg_token = str(data.get("regToken", "")).strip()

        if not endpoint or not p256dh or not auth:
            logger.warning(
                "Rejected /subscribe — endpoint/p256dh/auth missing"
            )

            return jsonify({
                "success": False,
                "error": "Invalid subscription: endpoint/p256dh/auth required"
            }), 400

        if not verify_admin_reg_token(reg_token):
            logger.warning(
                "Rejected /subscribe — invalid/missing regToken"
            )

            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 401

        clean_subscription = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": p256dh,
                "auth": auth
            }
        }

        added = add_subscription(clean_subscription)

        try:
            sync_subscription_to_gas({
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth
            })
        except Exception as e:
            logger.error(
                f"GAS sheet sync error: {e}"
            )

        subs = load_subscriptions()

        logger.info(
            f"/subscribe success | added={added} | total={len(subs)}"
        )

        return jsonify({
            "success": True,
            "added": added,
            "total_subscriptions": len(subs),
            "message": (
                "Subscription saved!"
                if added
                else "Already subscribed"
            )
        })

    except Exception as e:
        logger.exception("Subscribe error")

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# ---- Subscription remove karo (unsubscribe) ----
@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    try:
        data = request.get_json()
        endpoint = data.get("endpoint", "")
        remove_subscription(endpoint)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- Push notification bhejo (Apps Script ya frontend se call hoga) ----
@app.route("/send-notification", methods=["POST"])
def send_notification():
    # Auth check
    if not check_auth():
        logger.warning("Unauthorized notification attempt")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "No data"}), 400

        # Notification content
        title   = body.get("title", "🛒 Rakesh Mart")
        message = body.get("body", "Naya order aaya!")
        order_id   = body.get("orderId", "")
        customer   = body.get("customer", "")
        items_count = body.get("itemsCount", "")
        total      = body.get("total", "")
        image      = body.get("image", "")

        # Dynamic message build karo
        if customer:
            message = f"Customer: {customer}"
            if order_id:
                message += f"\nOrder ID: {order_id}"
            if total:
                message += f"\nTotal: ₹{total}"

        notification_payload = json.dumps({
            "title": title,
            "body": message,
            "icon": "/icon-192.png",
            "badge": "/icon-192.png",
            "image": image,
            "data": {
                "orderId": order_id,
                "customer": customer,
                "total": total,
                "url": "/"
            },
            "requireInteraction": True,
            "vibrate": [200, 100, 200]
        })

        subs = load_subscriptions()
        if not subs:
            logger.info("No subscriptions found")
            return jsonify({"success": True, "sent": 0, "message": "No subscribers"}), 200

        sent = 0
        failed = 0
        expired_endpoints = []

        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=notification_payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={
                        "sub": f"mailto:{VAPID_CLAIM_EMAIL}"
                    }
                )
                sent += 1
                logger.info(f"Notification sent to: {sub.get('endpoint', '')[:50]}...")

            except WebPushException as e:
                failed += 1
                status_code = e.response.status_code if e.response else 0
                logger.error(f"WebPush failed [{status_code}]: {e}")

                # 410 = subscription expired, remove it
                if status_code in [404, 410]:
                    expired_endpoints.append(sub.get("endpoint"))

            except Exception as e:
                failed += 1
                logger.error(f"Push error: {e}")

        # Expired subscriptions clean karo
        for ep in expired_endpoints:
            remove_subscription(ep)
            try:
                deactivate_subscription_in_gas(ep)
            except Exception as e:
                logger.error(f"GAS deactivate sync error: {e}")
            logger.info(f"Removed expired subscription: {ep[:50]}...")

        logger.info(f"Notification results: sent={sent}, failed={failed}")
        return jsonify({
            "success": True,
            "sent": sent,
            "failed": failed,
            "total_subscribers": len(subs),
            "expired_removed": len(expired_endpoints)
        })

    except Exception as e:
        logger.error(f"Send notification error: {e}")
        return jsonify({"error": str(e)}), 500

# ---- Subscriptions list dekho (debug) ----
@app.route("/subscriptions", methods=["GET"])
def list_subscriptions():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    subs = load_subscriptions()
    return jsonify({
        "count": len(subs),
        "subscriptions": [
            {"endpoint": s.get("endpoint", "")[:60] + "..."}
            for s in subs
        ]
    })

# ---- Subscriptions ka full JSON export (Render env var mein paste karo) ----
@app.route("/subscriptions/export", methods=["GET"])
def export_subscriptions():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    subs = load_subscriptions()
    return jsonify({
        "count": len(subs),
        "json_to_copy": json.dumps(subs),
        "instructions": "Upar wala json_to_copy value Render mein SUBSCRIPTIONS_BACKUP env var mein paste karo"
    })

# ---- Health check ----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200

# =============================================
# MAIN
# =============================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting RakeshMart Push Server on port {port}")
    logger.info(f"VAPID configured: {bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)}")
    app.run(host="0.0.0.0", port=port, debug=False)
