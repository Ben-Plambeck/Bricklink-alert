"""
BrickLink Price Alert Monitor
Reads watchlist from Google Sheets, checks prices once, and emails alerts.
Designed to be run on a schedule via GitHub Actions.

Logic:
- Gets the 6-month average SOLD price (new condition) for each item
- Alerts if any CURRENT US listing is 30% or more below that average
- Falls back to global listings if no US sold data exists

Environment variables (set as GitHub Secrets):
  BL_CONSUMER_KEY
  BL_CONSUMER_SECRET
  BL_TOKEN_VALUE
  BL_TOKEN_SECRET
  ALERT_EMAIL
  SMTP_FROM
  SMTP_PASSWORD
  GOOGLE_CREDENTIALS
  GOOGLE_SHEET_NAME

Google Sheet format (Sheet1):
  Column A: Item      — BrickLink item number, e.g. bat015 or 7261-1
  Column B: Type      — Minifig or Set
  Column C: Condition — New / Sealed / Used
  Column D: Threshold — ignored (kept for reference only, script uses dynamic 30% below avg)
"""

import os
import time
import smtplib
import logging
import tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from requests_oauthlib import OAuth1Session

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

DISCOUNT_THRESHOLD = 0.26  # Alert if listing is 30% below 6-month avg sold price

# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_gspread_client():
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(creds_json)
        creds_path = f.name
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def load_watchlist():
    client = get_gspread_client()
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "price tracker sheet")
    sheet = client.open(sheet_name).sheet1
    rows = sheet.get_all_records()
    watchlist = []
    for row in rows:
        try:
            item_no   = str(row.get("Item", "")).strip()
            item_type = str(row.get("Type", "")).strip().lower()
            condition = str(row.get("Condition", "")).strip().lower()

            if not item_no:
                continue

            if item_type not in ("set", "minifig"):
                logging.warning(f"Skipping '{item_no}': unknown type '{item_type}'")
                continue

            if condition in ("new", "sealed"):
                bl_condition = "N"
            elif condition == "used":
                bl_condition = "U"
            else:
                logging.warning(f"Skipping '{item_no}': unknown condition '{condition}'")
                continue

            watchlist.append({
                "no":        item_no,
                "type":      item_type,
                "condition": bl_condition,
            })

        except Exception as e:
            logging.warning(f"Skipping row {row}: {e}")

    logging.info(f"Loaded {len(watchlist)} item(s) from Google Sheet.")
    return watchlist

# ── BrickLink API ─────────────────────────────────────────────────────────────

def get_oauth_session():
    return OAuth1Session(
        client_key=os.environ["BL_CONSUMER_KEY"],
        client_secret=os.environ["BL_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BL_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BL_TOKEN_SECRET"],
    )

def get_avg_sold_price(session, item):
    """Get 6-month average sold price for new condition, US first then global."""
    url = f"https://api.bricklink.com/api/store/v1/items/{item['type']}/{item['no']}/price"
    for country in ["US", None]:
        params = {
            "guide_type":  "sold",
            "new_or_used": item["condition"],
            "currency_code": "USD",
        }
        if country:
            params["country_code"] = country
        try:
            resp = session.get(url, params=params)
            data = resp.json()
            if data.get("meta", {}).get("code") != 200:
                continue
            avg = data.get("data", {}).get("avg_price")
            if avg:
                return float(avg), country
        except Exception as e:
            logging.error(f"Error getting sold price for {item['no']}: {e}")
    return None, None

def get_us_listings(session, item):
    """Get current US-only stock listings for new condition."""
    url = f"https://api.bricklink.com/api/store/v1/items/{item['type']}/{item['no']}/price"
    params = {
        "guide_type":    "stock",
        "new_or_used":   item["condition"],
        "currency_code": "USD",
        "country_code":  "US",
    }
    try:
        resp = session.get(url, params=params)
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            return []
        return data.get("data", {}).get("price_detail", [])
    except Exception as e:
        logging.error(f"Error getting listings for {item['no']}: {e}")
        return []

# ── Email Alert ───────────────────────────────────────────────────────────────

def send_alert(item, listings, avg_sold, threshold_price):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]

    condition_label = "New (Sealed)" if item["condition"] == "N" else "Used"
    subject = f"BrickLink Deal: {item['no']} listed 30%+ below 6-month avg!"

    rows = ""
    for listing in listings:
        price   = float(listing.get("unit_price", 0))
        qty     = listing.get("quant", listing.get("quantity", listing.get("qty", "N/A")))
        country = listing.get("seller_country_code", "US")
        pct_off = round((1 - price / avg_sold) * 100, 1)
        rows += f"<tr><td>${price:.2f}</td><td>{qty}</td><td>{country}</td><td>{pct_off}% below avg</td></tr>"

    if item["type"] == "minifig":
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?M={item['no']}#T=S&O={{%22ss%22:%22US%22}}"
    else:
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?S={item['no']}#T=S&O={{%22ss%22:%22US%22}}"

    html = f"""
    <h2>🔥 Deal Alert: {item['no']}</h2>
    <p><b>6-month avg sold price:</b> ${avg_sold:.2f}</p>
    <p><b>Alert threshold (30% below avg):</b> ${threshold_price:.2f}</p>
    <p>The following <b>{condition_label}</b> US listings are 30%+ below the 6-month average:</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Price (USD)</th><th>Qty</th><th>Seller Country</th><th>Discount</th></tr>
      {rows}
    </table>
    <p><a href="{bl_url}">View US listings on BrickLink →</a></p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = alert_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_from, smtp_pass)
        server.sendmail(smtp_from, alert_to, msg.as_string())

    logging.info(f"Alert email sent for {item['no']}!")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("BrickLink price alert check started.")

    watchlist = load_watchlist()
    logging.info(f"Checking {len(watchlist)} item(s)...")

    session = get_oauth_session()
    alerts_sent = 0

    for item in watchlist:
        try:
            logging.info(f"Checking {item['no']}...")

            # Get 6-month avg sold price
            avg_sold, sold_country = get_avg_sold_price(session, item)
            if not avg_sold:
                logging.info(f"  No sold data found, skipping.")
                time.sleep(0.5)
                continue

            threshold_price = avg_sold * (1 - DISCOUNT_THRESHOLD)
            logging.info(f"  Avg sold: ${avg_sold:.2f} | Threshold: ${threshold_price:.2f}")

            # Get current US listings
            listings = get_us_listings(session, item)
            under = [
                l for l in listings
                if float(l.get("unit_price", 999999)) <= threshold_price
            ]

            if under:
                logging.info(f"  {len(under)} US listing(s) found 30%+ below avg! Sending alert.")
                send_alert(item, under, avg_sold, threshold_price)
                alerts_sent += 1
            else:
                us_prices = [float(l.get("unit_price", 0)) for l in listings if l.get("unit_price")]
                if us_prices:
                    logging.info(f"  No deals found. Lowest US listing: ${min(us_prices):.2f}")
                else:
                    logging.info(f"  No US listings found.")

            time.sleep(0.5)

        except Exception as e:
            logging.error(f"Error checking {item['no']}: {e}")

    logging.info(f"Done. {alerts_sent} alert(s) sent.")

if __name__ == "__main__":
    main()
