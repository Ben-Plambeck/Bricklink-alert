"""
BrickLink Price Alert - bat015 Nightwing
Polls BrickLink every 5 minutes and emails you when a New condition
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
        "type":      "minifig",
        "no":        "bat015",
        "name":      "Nightwing (bat015)",
        "condition": "N",           # N = New, U = Used
        "threshold": 330.00,        # Alert if any listing is UNDER this price (USD)
    },
    # Add more items here in the same format, e.g.:
    # {
    #     "type":      "minifig",
    #     "no":        "bat016",
    #     "name":      "Batman (bat016)",
    #     "condition": "N",
    #     "threshold": 100.00,
    # },
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
    url = (
        f"https://api.bricklink.com/api/store/v1"
        f"/items/{item['type']}/{item['no']}/price"
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

    condition_label = "New" if item["condition"] == "N" else "Used"
    subject = f"BrickLink Alert: {item['name']} listed under ${item['threshold']:.2f}!"

    rows = ""
    for listing in listings_under_threshold:
        price   = float(listing.get("unit_price", 0))
        qty     = listing.get("quant", listing.get("quantity", listing.get("qty", "N/A")))
        country = listing.get("seller_country_code", "N/A")
        rows += f"<tr><td>${price:.2f}</td><td>{qty}</td><td>{country}</td></tr>"

    html = f"""
    <h2>Price Alert: {item['name']}</h2>
    <p>The following <b>{condition_label}</b> listings are under <b>${item['threshold']:.2f}</b>:</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Price (USD)</th><th>Qty</th><th>Seller Country</th></tr>
      {rows}
    </table>
    <p><a href="https://www.bricklink.com/v2/catalog/catalogitem.page?M={item['no']}#T=S">
    View listings on BrickLink →</a></p>
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
