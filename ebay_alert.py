"""
eBay Price Alert Monitor
- Searches eBay for LEGO sets from your Google Sheet watchlist
- Alerts if Buy It Now price is 30% below BrickLink 6-month avg sold price
- Also monitors for LEGO collection/lot listings nationwide
- New/Sealed condition only
- US sellers only
- Tracks seen listings for 3 days to avoid repeat emails
"""

import os
import time
import base64
import smtplib
import logging
import tempfile
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from requests_oauthlib import OAuth1Session

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

DISCOUNT_THRESHOLD = 0.25
COLLECTION_MIN_PRICE = 400.00
COLLECTION_MAX_PRICE = 20000.00
SEEN_EXPIRY_DAYS = 3

COLLECTION_KEYWORDS = [
    "LEGO lot",
    "LEGO collection",
    "LEGO bulk",
    "LEGO haul",
    "LEGO sets lot",
    "LEGO star wars lot",
    "LEGO batman lot",
    "LEGO marvel lot",
    "LEGO minifigures lot",
    "LEGO factory sealed lot",
    "LEGO vintage lot",
    "LEGO retired lot",
    "LEGO Star Wars sealed",
    "LEGO Batman sealed lot",
]

EXCLUDE_KEYWORDS = [
    "instructions only",
    "parts only",
    "incomplete",
    "custom",
    "loose",
    "broken",
    "used",
    "open box",
    "opened",
    "built",
    "assembled",
    "pre-owned",
    "preowned",
    "pre owned",
    "missing",
    "no box",
    "no manual",
    "read description",
    "lot of used",
    "used lot",
    "mixed lot",
    "bulk used",
    "played",
    "display",
    "retired used",
]

REQUIRE_KEYWORDS = [
    "sealed",
    "new",
    "factory sealed",
    "misb",
    "nib",
    "unopened",
]

# ── eBay OAuth ────────────────────────────────────────────────────────────────

def get_ebay_token():
    app_id = os.environ["EBAY_APP_ID"]
    cert_id = os.environ["EBAY_CERT_ID"]
    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data="grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope",
    )
    return resp.json().get("access_token")

def ebay_search(token, query, condition="NEW", limit=50):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "q": query,
        "filter": f"conditions:{{{condition}}},itemLocationCountry:US,buyingOptions:{{FIXED_PRICE}}",
        "limit": limit,
        "sort": "newlyListed",
    }
    resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers=headers,
        params=params,
    )
    data = resp.json()
    return data.get("itemSummaries", [])

def is_new_listing(listing):
    """Returns True if the listing was posted within the last 24 hours."""
    date_str = listing.get("itemCreationDate") or listing.get("listingDate")
    if not date_str:
        return True  # if no date available, include it
    try:
        from dateutil import parser as dateparser
        listed_at = dateparser.parse(date_str)
        if listed_at.tzinfo:
            from datetime import timezone
            now = datetime.now(timezone.utc)
        else:
            now = datetime.utcnow()
        return (now - listed_at).total_seconds() < 86400  # 24 hours
    except:
        return True  # if parsing fails, include it

def filter_collection_listings(listings):
    results = []
    for l in listings:
        title = l.get("title", "").lower()
        price = float(l.get("price", {}).get("value", 0))
        condition = l.get("condition", "").lower()
        if price < COLLECTION_MIN_PRICE or price > COLLECTION_MAX_PRICE:
            continue
        if any(ex in title for ex in EXCLUDE_KEYWORDS):
            continue
        has_sealed_keyword = any(kw in title for kw in REQUIRE_KEYWORDS)
        is_new_condition = condition in ("new", "brand new")
        if not has_sealed_keyword and not is_new_condition:
            continue
        if not is_new_listing(l):
            continue
        results.append(l)
    return results

# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_gspread_client():
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(creds_json)
        creds_path = f.name
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def get_seen_sheet(client):
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "price tracker sheet")
    spreadsheet = client.open(sheet_name)
    try:
        return spreadsheet.worksheet("Seen")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Seen", rows=10000, cols=2)
        ws.append_row(["Listing ID", "Date Seen"])
        return ws

def load_seen_ids(seen_sheet):
    rows = seen_sheet.get_all_records()
    now = datetime.utcnow()
    seen = {}
    for row in rows:
        listing_id = str(row.get("Listing ID", "")).strip()
        date_seen  = str(row.get("Date Seen", "")).strip()
        if listing_id and date_seen:
            try:
                seen[listing_id] = datetime.fromisoformat(date_seen)
            except:
                pass
    # Return only IDs seen within the last 3 days
    return {k: v for k, v in seen.items() if now - v < timedelta(days=SEEN_EXPIRY_DAYS)}

def save_seen_ids(seen_sheet, new_ids):
    """Add new listing IDs to the Seen sheet and clean up expired ones."""
    now = datetime.utcnow()
    rows = seen_sheet.get_all_records()

    # Build current seen dict
    existing = {}
    for row in rows:
        lid = str(row.get("Listing ID", "")).strip()
        ds  = str(row.get("Date Seen", "")).strip()
        if lid and ds:
            try:
                existing[lid] = datetime.fromisoformat(ds)
            except:
                pass

    # Add new IDs
    for lid in new_ids:
        existing[lid] = now

    # Remove expired
    cutoff = now - timedelta(days=SEEN_EXPIRY_DAYS)
    existing = {k: v for k, v in existing.items() if v > cutoff}

    # Rewrite sheet in one batch call (much faster)
    all_rows = [["Listing ID", "Date Seen"]] + [[lid, dt.isoformat()] for lid, dt in existing.items()]
    seen_sheet.clear()
    seen_sheet.update(all_rows, "A1")

    logging.info(f"Seen sheet updated: {len(existing)} active entries.")

def load_watchlist(client):
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
                continue
            if condition not in ("new", "sealed"):
                continue
            watchlist.append({"no": item_no, "type": item_type})
        except Exception as e:
            logging.warning(f"Skipping row {row}: {e}")
    logging.info(f"Loaded {len(watchlist)} item(s) from Google Sheet.")
    return watchlist

# ── BrickLink avg sold price ──────────────────────────────────────────────────

def get_bl_session():
    return OAuth1Session(
        client_key=os.environ["BL_CONSUMER_KEY"],
        client_secret=os.environ["BL_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BL_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BL_TOKEN_SECRET"],
    )

def get_avg_sold_price(bl_session, item):
    url = f"https://api.bricklink.com/api/store/v1/items/{item['type']}/{item['no']}/price"
    for country in ["US", None]:
        params = {"guide_type": "sold", "new_or_used": "N", "currency_code": "USD"}
        if country:
            params["country_code"] = country
        try:
            resp = bl_session.get(url, params=params)
            data = resp.json()
            if data.get("meta", {}).get("code") != 200:
                continue
            avg = data.get("data", {}).get("avg_price")
            if avg:
                return float(avg)
        except:
            pass
    return None

# ── Email ─────────────────────────────────────────────────────────────────────

def send_deal_alert(item, ebay_listings, avg_sold, threshold):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]
    subject = f"eBay Deal: {item['no']} listed 30%+ below BrickLink avg!"
    rows = ""
    for l in ebay_listings:
        price = l.get("price", {}).get("value", "N/A")
        title = l.get("title", "N/A")
        url   = l.get("itemWebUrl", "#")
        pct_off = round((1 - float(price) / avg_sold) * 100, 1) if avg_sold else "N/A"
        rows += f'<tr><td><a href="{url}">{title}</a></td><td>${price}</td><td>{pct_off}% below avg</td></tr>'
    html = f"""
    <h2>🔥 eBay Deal: {item['no']}</h2>
    <p><b>BrickLink 6-month avg sold:</b> ${avg_sold:.2f}</p>
    <p><b>Alert threshold (30% below):</b> ${threshold:.2f}</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Listing</th><th>Price</th><th>Discount</th></tr>
      {rows}
    </table>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = alert_to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_from, smtp_pass)
        server.sendmail(smtp_from, alert_to, msg.as_string())
    logging.info(f"Deal alert sent for {item['no']}!")

def send_collection_alert(keyword, listings):
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    alert_to  = os.environ["ALERT_EMAIL"]
    subject = f"eBay Collection Alert: New '{keyword}' listings!"
    rows = ""
    for l in listings[:10]:
        price = l.get("price", {}).get("value", "N/A")
        title = l.get("title", "N/A")
        url   = l.get("itemWebUrl", "#")
        rows += f'<tr><td><a href="{url}">{title}</a></td><td>${price}</td></tr>'
    html = f"""
    <h2>📦 New LEGO Collection Listings: "{keyword}"</h2>
    <p>Price range: ${COLLECTION_MIN_PRICE:.0f} - ${COLLECTION_MAX_PRICE:.0f} | US sellers | New/Sealed only</p>
    <p>These were just listed on eBay — act fast!</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Listing</th><th>Price</th></tr>
      {rows}
    </table>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = alert_to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_from, smtp_pass)
        server.sendmail(smtp_from, alert_to, msg.as_string())
    logging.info(f"Collection alert sent for '{keyword}'!")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("eBay price alert check started.")

    token = get_ebay_token()
    bl_session = get_bl_session()
    client = get_gspread_client()
    watchlist = load_watchlist(client)
    seen_sheet = get_seen_sheet(client)
    seen_ids = load_seen_ids(seen_sheet)
    new_seen_ids = []
    alerts_sent = 0

    logging.info(f"Loaded {len(seen_ids)} seen listing IDs.")

    # ── Part 1: Collection hunting ─────────────────────────────────────────
    logging.info("Checking collection keywords...")
    for keyword in COLLECTION_KEYWORDS:
        try:
            listings = ebay_search(token, keyword, condition="NEW", limit=50)
            filtered = filter_collection_listings(listings)

            # Filter out already seen listings
            new_listings = [l for l in filtered if l.get("itemId") not in seen_ids]

            if new_listings:
                logging.info(f"  Found {len(new_listings)} new listings for '{keyword}' — sending alert.")
                send_collection_alert(keyword, new_listings)
                alerts_sent += 1
                new_seen_ids.extend([l.get("itemId") for l in new_listings if l.get("itemId")])
            else:
                logging.info(f"  No new qualifying listings for '{keyword}'.")
        except Exception as e:
            logging.error(f"Error searching '{keyword}': {e}")
        time.sleep(0.5)

    # ── Part 2: Individual set deal hunting ────────────────────────────────
    logging.info(f"Checking {len(watchlist)} individual items on eBay...")
    for item in watchlist:
        try:
            if item["type"] != "set":
                continue

            avg_sold = get_avg_sold_price(bl_session, {**item, "condition": "N"})
            if not avg_sold:
                continue

            threshold = avg_sold * (1 - DISCOUNT_THRESHOLD)
            listings = ebay_search(token, f"LEGO {item['no']} sealed", condition="NEW", limit=10)

            listings = [
                l for l in listings
                if not any(ex in l.get("title", "").lower() for ex in EXCLUDE_KEYWORDS)
            ]

            under = [
                l for l in listings
                if float(l.get("price", {}).get("value", 999999)) <= threshold
                and l.get("itemId") not in seen_ids
            ]

            if under:
                logging.info(f"  {item['no']}: {len(under)} new eBay listing(s) 30%+ below avg!")
                send_deal_alert(item, under, avg_sold, threshold)
                alerts_sent += 1
                new_seen_ids.extend([l.get("itemId") for l in under if l.get("itemId")])
            else:
                logging.info(f"  {item['no']}: No new eBay deals found.")

            time.sleep(0.5)

        except Exception as e:
            logging.error(f"Error checking {item['no']} on eBay: {e}")

    # Save all newly seen IDs
    if new_seen_ids:
        save_seen_ids(seen_sheet, new_seen_ids)

    logging.info(f"Done. {alerts_sent} alert(s) sent.")

if __name__ == "__main__":
    main()
