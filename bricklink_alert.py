name: BrickLink Price Alert

on:
  schedule:
    - cron: '0 6 * * *'   # 6:00 AM UTC  (11:00 PM PST)
    - cron: '0 12 * * *'  # 12:00 PM UTC (5:00 AM PST)
    - cron: '0 18 * * *'  # 6:00 PM UTC  (11:00 AM PST)
    - cron: '0 0 * * *'   # 12:00 AM UTC (5:00 PM PST)
  workflow_dispatch:       # lets you trigger it manually from GitHub anytime

jobs:
  check-prices:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests requests-oauthlib gspread google-auth

      - name: Run price alert
        env:
          BL_CONSUMER_KEY:    ${{ secrets.BL_CONSUMER_KEY }}
          BL_CONSUMER_SECRET: ${{ secrets.BL_CONSUMER_SECRET }}
          BL_TOKEN_VALUE:     ${{ secrets.BL_TOKEN_VALUE }}
          BL_TOKEN_SECRET:    ${{ secrets.BL_TOKEN_SECRET }}
          ALERT_EMAIL:        ${{ secrets.ALERT_EMAIL }}
          SMTP_FROM:          ${{ secrets.SMTP_FROM }}
          SMTP_PASSWORD:      ${{ secrets.SMTP_PASSWORD }}
          GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
          GOOGLE_SHEET_NAME:  ${{ secrets.GOOGLE_SHEET_NAME }}
        run: python bricklink_alert.py
