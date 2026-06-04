"""
BrickLink Price Alert Monitor
Polls BrickLink every 5 minutes and emails you when a listing
appears under your price threshold.

Setup:
  pip install requests requests-oauthlib

Environment variables (set in Render dashboard):
  BL_CONSUMER_KEY
  BL_CONSUMER_SECRET
  BL_TOKEN_VALUE
  BL_TOKEN_SECRET
  ALERT_EMAIL          (your email to receive alerts)
  SMTP_FROM            (gmail address used to send)
  SMTP_PASSWORD        (gmail App Password, NOT your gmail password)
"""

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from requests_oauthlib import OAuth1Session

# ── Configuration ────────────────────────────────────────────────────────────

WATCH_ITEMS = [
    # ── Minifigs ──────────────────────────────────────────────────────────────
    {"type": "minifig", "no": "bat015",       "name": "Nightwing (bat015)",                    "condition": "N", "threshold": 330.00},

    # ── 2005 LEGO Star Wars Sets ──────────────────────────────────────────────
    {"type": "set",     "no": "6966-1",       "name": "Mini Jedi Starfighter (6966-1)",         "condition": "N", "threshold": 10.93},
    {"type": "set",     "no": "6967-1",       "name": "Mini ARC-170 Starfighter (6967-1)",      "condition": "N", "threshold": 11.28},
    {"type": "set",     "no": "7250-1",       "name": "Clone Scout Walker (7250-1)",            "condition": "N", "threshold": 100.88},
    {"type": "set",     "no": "7251-1",       "name": "Darth Vader Transformation (7251-1)",    "condition": "N", "threshold": 89.81},
    {"type": "set",     "no": "7252-1",       "name": "Droid Tri-Fighter (7252-1)",             "condition": "N", "threshold": 60.00},
    {"type": "set",     "no": "7255-1",       "name": "General Grievous Chase (7255-1)",        "condition": "N", "threshold": 449.88},
    {"type": "set",     "no": "7256-1",       "name": "Jedi Starfighter and Vulture Droid (7256-1)", "condition": "N", "threshold": 160.04},
    {"type": "set",     "no": "7257-1",       "name": "Ultimate Lightsaber Duel (7257-1)",      "condition": "N", "threshold": 641.20},
    {"type": "set",     "no": "7258-1",       "name": "Wookiee Attack (7258-1)",                "condition": "N", "threshold": 315.89},
    {"type": "set",     "no": "7259-1",       "name": "ARC-170 Fighter (7259-1)",               "condition": "N", "threshold": 331.68},
    {"type": "set",     "no": "7260-1",       "name": "Wookiee Catamaran (7260-1)",             "condition": "N", "threshold": 1199.99},
    {"type": "set",     "no": "7261-1",       "name": "Clone Turbo Tank (7261-1)",              "condition": "N", "threshold": 502.71},
    {"type": "set",     "no": "7263-1",       "name": "TIE Fighter (7263-1)",                   "condition": "N", "threshold": 244.00},
    {"type": "set",     "no": "7264-1",       "name": "Imperial Inspection (7264-1)",           "condition": "N", "threshold": 321.65},
    {"type": "set",     "no": "7283-1",       "name": "Ultimate Space Battle (7283-1)",         "condition": "N", "threshold": 979.57},
    {"type": "set",     "no": "10143-1",      "name": "Death Star II (10143-1)",                "condition": "N", "threshold": 3100.00},
    {"type": "set",     "no": "65771-1",      "name": "Episode III Collectors' Set (65771-1)",  "condition": "N", "threshold": 738.22},
]

POLL_INTERVAL_SECONDS = 5 * 60    # 5 minutes

# ── BrickLink API ─────────────────────────────────────────────────────────────

def get_oauth_session():
    return OAuth1Session(
        client_key=os.environ["BL_CONSUMER_KEY"],
        client_secret=os.environ["BL_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BL_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BL_TOKEN_SECRET"],
    )

def check_price(session, item):
    item_type = item["type"]
    url = (
        f"https://api.bricklink.com/api/store/v1"
        f"/items/{item_type}/{item['no']}/price"
    )
    params = {
        "guide_type":    "stock",
        "new_or_used":   item["condition"],
        "currency_code": "USD",
    }
    resp = session.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if data.get("meta", {}).get("code") != 200:
        logging.warning(f"API warning for {item['no']}: {data.get('meta')}")
        return []

    return data.get("data", {}).get("price_detail", [])

# ── Email Alert ───────────────────────────────────────────────────────────────

def send_alert(item, listings_under_threshold):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]

    condition_label = "New (Sealed)" if item["condition"] == "N" else "Used"
    subject = f"BrickLink Alert: {item['name']} listed under ${item['threshold']:.2f}!"

    rows = ""
    for listing in listings_under_threshold:
        price   = float(listing.get("unit_price", 0))
        qty     = listing.get("quant", listing.get("quantity", listing.get("qty", "N/A")))
        country = listing.get("seller_country_code", "N/A")
        rows += f"<tr><td>${price:.2f}</td><td>{qty}</td><td>{country}</td></tr>"

    item_type = item["type"]
    if item_type == "minifig":
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?M={item['no']}#T=S"
    else:
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?S={item['no']}#T=S"

    html = f"""
    <h2>Price Alert: {item['name']}</h2>
    <p>The following <b>{condition_label}</b> listings are under <b>${item['threshold']:.2f}</b>:</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Price (USD)</th><th>Qty</th><th>Seller Country</th></tr>
      {rows}
    </table>
    <p><a href="{bl_url}">View listings on BrickLink →</a></p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = alert_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_from, smtp_pass)
        server.sendmail(smtp_from, alert_to, msg.as_string())

    logging.info(f"Alert email sent for {item['name']}!")

# ── Main Loop ─────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("BrickLink price alert monitor started.")
    logging.info(f"Watching {len(WATCH_ITEMS)} item(s), polling every {POLL_INTERVAL_SECONDS // 60} minutes.")

    session = get_oauth_session()

    while True:
        for item in WATCH_ITEMS:
            try:
                logging.info(f"Checking {item['name']}...")
                listings = check_price(session, item)

                under = [
                    l for l in listings
                    if float(l.get("unit_price", 999999)) < item["threshold"]
                ]

                if under:
                    logging.info(f"  {len(under)} listing(s) found under ${item['threshold']}! Sending alert.")
                    send_alert(item, under)
                else:
                    prices = [float(l.get("unit_price", 0)) for l in listings if l.get("unit_price")]
                    min_price = min(prices) if prices else None
                    if min_price:
                        logging.info(f"  No listings under threshold. Lowest: ${min_price:.2f}")
                    else:
                        logging.info(f"  No listings found.")

            except Exception as e:
                logging.error(f"Error checking {item['name']}: {e}")

        logging.info(f"Sleeping {POLL_INTERVAL_SECONDS // 60} minutes...")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
