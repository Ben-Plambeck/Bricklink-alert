"""
BrickLink Price Alert - bat015 Nightwing
Polls BrickLink every 15 minutes and emails you when a New condition
listing appears under your price threshold.

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
    {
        "type":      "minifig",           # M = Minifig
        "no":        "bat015",
        "name":      "Nightwing (bat015)",
        "condition": "N",           # N = New, U = Used
        "threshold": 320.00,        # Alert if any listing is UNDER this price (USD)
    },
    # Add more items here in the same format, e.g.:
    # {
    #     "type":      "S",
    #     "no":        "10179-1",
    #     "name":      "Millennium Falcon (10179-1)",
    #     "condition": "N",
    #     "threshold": 2000.00,
    # },
]

POLL_INTERVAL_SECONDS = 15 * 60   # 15 minutes

# ── BrickLink API ─────────────────────────────────────────────────────────────

def get_oauth_session():
    return OAuth1Session(
        client_key=os.environ["BL_CONSUMER_KEY"],
        client_secret=os.environ["BL_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BL_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BL_TOKEN_SECRET"],
    )

def check_price(session, item):
    """
    Calls the BrickLink price guide endpoint for current stock listings.
    Returns a list of dicts with 'qty' and 'unit_price' for each listing,
    filtered to the item's condition and USD currency.
    """
    url = (
        f"https://api.bricklink.com/api/store/v1"
        f"/items/{item['type']}/{item['no']}/price"
    )
    params = {
        "guide_type":    "stock",          # current listings (not sold history)
        "new_or_used":   item["condition"],
        "currency_code": "USD",
    }
    resp = session.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if data.get("meta", {}).get("code") != 200:
        logging.warning(f"API warning for {item['no']}: {data.get('meta')}")
        return []

    price_details = data.get("data", {}).get("price_detail", [])
    return price_details  # each entry has: qty, unit_price, shipping_available, seller_country_code

# ── Email Alert ───────────────────────────────────────────────────────────────

def send_alert(item, listings_under_threshold):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]

    subject = f"🔔 BrickLink Alert: {item['name']} listed under ${item['threshold']:.2f}!"

    lines = [
        f"<h2>Price Alert: {item['name']}</h2>",
        f"<p>The following <b>{'New' if item['condition'] == 'N' else 'Used'}</b> "
        f"listings are currently under <b>${item['threshold']:.2f}</b>:</p>",
        "<table border='1' cellpadding='6' cellspacing='0'>",
        "<tr><th>Price (USD)</th><th>Qty Available</th><th>Seller Country</th></tr>",
    ]
    for listing in listings_under_threshold:
        lines.append(
            f"<tr>"
            f"<td>${float(listing['unit_price']):.2f}</td>"
            f"<td>{listing['qty']}</td>"
            f"<td>{listing.get('seller_country_code', 'N/A')}</td>"
            f"</tr>"
        )
    lines += [
        "</table>",
        f"<p><a href='https://www.bricklink.com/v2/catalog/catalogitem.page?"
        f"{item['type']}={item['no']}#T=S&O={{\"cond\":\"{item['condition']}\"}}'>"
        f"View listings on BrickLink →</a></p>",
    ]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = alert_to
    msg.attach(MIMEText("\n".join(lines), "html"))

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
                    if float(l["unit_price"]) < item["threshold"]
                ]

                if under:
                    logging.info(f"  ✅ {len(under)} listing(s) found under ${item['threshold']}! Sending alert.")
                    send_alert(item, under)
                else:
                    min_price = min((float(l["unit_price"]) for l in listings), default=None)
                    logging.info(f"  No listings under threshold. Lowest: ${min_price:.2f}" if min_price else "  No listings found.")

            except Exception as e:
                logging.error(f"Error checking {item['name']}: {e}")

        logging.info(f"Sleeping {POLL_INTERVAL_SECONDS // 60} minutes...\n")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
