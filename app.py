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
AUTH_KEY          = os.environ.get("AUTH_KEY", "R@k3shM4rt#2026$PUSH!9xV7qL_secure%81")

SUBSCRIPTIONS_FILE = "subscriptions.json"

# =============================================
# SUBSCRIPTION STORAGE (JSON file)
# =============================================
def load_subscriptions():
    try:
        if os.path.exists(SUBSCRIPTIONS_FILE):
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Load subscriptions error: {e}")
    return []

def save_subscriptions(subs):
    try:
        with open(SUBSCRIPTIONS_FILE, "w") as f:
            json.dump(subs, f, indent=2)
    except Exception as e:
        logger.error(f"Save subscriptions error: {e}")

def add_subscription(sub_info):
    subs = load_subscriptions()
    endpoint = sub_info.get("endpoint", "")
    # Duplicate check — same endpoint nahi chahiye
    for existing in subs:
        if existing.get("endpoint") == endpoint:
            logger.info(f"Subscription already exists: {endpoint[:50]}...")
            return False
    subs.append(sub_info)
    save_subscriptions(subs)
    logger.info(f"New subscription added. Total: {len(subs)}")
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
@app.route("/subscribe", methods=["POST"])
def subscribe():
    try:
        data = request.get_json()
        if not data or "endpoint" not in data:
            return jsonify({"error": "Invalid subscription data"}), 400

        added = add_subscription(data)
        subs = load_subscriptions()
        return jsonify({
            "success": True,
            "added": added,
            "total_subscriptions": len(subs),
            "message": "Subscription saved!" if added else "Already subscribed"
        })
    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        return jsonify({"error": str(e)}), 500

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
