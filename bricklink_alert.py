"""
BrickLink Price Alert Monitor
Reads watchlist from Google Sheets, checks prices once, and emails alerts.
Designed to be run on a schedule via GitHub Actions.

Setup:
  pip install requests requests-oauthlib gspread google-auth

Environment variables (set as GitHub Secrets):
  BL_CONSUMER_KEY
  BL_CONSUMER_SECRET
  BL_TOKEN_VALUE
  BL_TOKEN_SECRET
  ALERT_EMAIL          (your email to receive alerts)
  SMTP_FROM            (gmail address used to send)
  SMTP_PASSWORD        (gmail App Password, NOT your gmail password)
  GOOGLE_CREDENTIALS   (contents of your Google service account JSON key)
  GOOGLE_SHEET_NAME    (exact name of your Google Sheet, e.g. "price tracker sheet")

Google Sheet format (Sheet1):
  Column A: Item      — BrickLink item number, e.g. bat015 or 7261-1
  Column B: Type      — Minifig or Set
  Column C: Condition — New / Sealed / Used
  Column D: Threshold — price in USD, e.g. 300 or $330.00
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
            threshold = str(row.get("Threshold", "")).strip().replace("$", "").replace(",", "")
            if not item_no or not threshold:
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
                "threshold": float(threshold),
            })
        except Exception as e:
            logging.warning(f"Skipping row {row}: {e}")
    logging.info(f"Loaded {len(watchlist)} item(s) from Google Sheet.")
    return watchlist

def get_oauth_session():
    return OAuth1Session(
        client_key=os.environ["BL_CONSUMER_KEY"],
        client_secret=os.environ["BL_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BL_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BL_TOKEN_SECRET"],
    )

def check_price(session, item):
    url = f"https://api.bricklink.com/api/store/v1/items/{item['type']}/{item['no']}/price"
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

def send_alert(item, listings_under_threshold):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]
    condition_label = "New (Sealed)" if item["condition"] == "N" else "Used"
    subject = f"BrickLink Alert: {item['no']} listed under ${item['threshold']:.2f}!"
    rows = ""
    for listing in listings_under_threshold:
        price   = float(listing.get("unit_price", 0))
        qty     = listing.get("quant", listing.get("quantity", listing.get("qty", "N/A")))
        country = listing.get("seller_country_code", "N/A")
        rows += f"<tr><td>${price:.2f}</td><td>{qty}</td><td>{country}</td></tr>"
    if item["type"] == "minifig":
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?M={item['no']}#T=S"
    else:
        bl_url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?S={item['no']}#T=S"
    html = f"""
    <h2>Price Alert: {item['no']}</h2>
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
    logging.info(f"Alert email sent for {item['no']}!")

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
            listings = check_price(session, item)
            under = [
                l for l in listings
                if float(l.get("unit_price", 999999)) < item["threshold"]
            ]
            if under:
                logging.info(f"  {len(under)} listing(s) found under ${item['threshold']}! Sending alert.")
                send_alert(item, under)
                alerts_sent += 1
            else:
                prices = [float(l.get("unit_price", 0)) for l in listings if l.get("unit_price")]
                min_price = min(prices) if prices else None
                if min_price:
                    logging.info(f"  No listings under threshold. Lowest: ${min_price:.2f}")
                else:
                    logging.info(f"  No listings found.")
            time.sleep(0.5)
        except Exception as e:
            logging.error(f"Error checking {item['no']}: {e}")
    logging.info(f"Done. {alerts_sent} alert(s) sent.")

if __name__ == "__main__":
    main()
