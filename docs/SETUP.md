# Set up AURUM with Databento and Telegram

Selected on 2026-11-12: no data provider account yet; Telegram is the preferred
notification channel. Start with historical data. AURUM remains research and
paper simulation only.

## What to provide first

1. An original historical export for **one dated MGC contract and one completed
   trading session**, placed under the ignored `data/incoming/` directory.
2. Its local path, vendor, raw contract symbol, requested UTC start/end and any
   export metadata. Preserve the downloaded bytes; do not fill gaps or adjust
   prices. If the download includes instrument metadata, retain that too.
3. A brief confirmation that this is data you are entitled to use for this
   private research project. Keep licensed files out of Git and public sharing.

No API key is needed for local CSV import. I can prepare and review the dated
exchange schedule after the export period is known. You do not need to write
calendar JSON yourself.

One session validates the pipeline; it does not validate profitability. Longer
history, multiple contract periods and a reserved final holdout follow only
after the first import reconciles correctly. Missing minutes remain visible
and may block replay. Do not buy a large export merely to meet a trade count.

## Databento historical setup

1. Create your own account through [Databento](https://databento.com/). Review
   the provider's current agreements and pricing in your account before any
   purchase. AURUM has not opened an account or subscribed for you.
2. Use the CME Globex dataset **GLBX.MDP3**, schema **ohlcv-1m**. Choose a single
   raw, dated **MGC** symbol from the vendor's instrument list. Keep its mapping
   to `MGC-YYYY-MM`. Do not substitute spot XAUUSD, a parent-symbol bundle or an
   adjusted continuous contract. We have not verified the current active month.
3. Choose a small completed interval and review the provider's estimate before
   downloading. Share the estimated scope/cost if you want help reviewing it;
   there is no standing authorization for paid API requests or subscriptions.
4. Retain the original export and request metadata. AURUM's native CSV path
   supports UTC timestamps, symbol mapping and decimal prices. The corresponding
   SDK export options are `pretty_ts=True`, `pretty_px=True`, `map_symbols=True`.
   If your portal export differs, retain it unchanged and tell me the format.
5. Place it in `data/incoming/` and tell me the local path. I will check format,
   dated identity, timestamp meaning, schedule, gaps, prices and provenance
   before attempting research replay.

Databento documents its [CME/COMEX dataset](https://databento.com/docs/datasets/glbx-mdp3),
[minute-bar conventions](https://databento.com/docs/schemas/ohlcv) and
[CSV export options](https://databento.com/docs/api-historical/helpers?lang=python).
The request end is exclusive. Vendor minute timestamps/aggregation conventions
must be preserved rather than inferred from a chart display.

## Telegram setup

1. In Telegram, open the official **@BotFather** account and create a dedicated
   bot using `/newbot`. Keep its token private. Do not send it in this chat or
   paste a token-bearing API URL into a browser.
2. Open your new bot's private chat and send `/start` yourself. AURUM does not
   initiate that conversation or send a test message during setup inspection.
3. In the project directory, create `.env` from `.env.example` only if you do
   not already have one. Keep its permissions private:

   ```sh
   test -e .env || (umask 077; cp .env.example .env)
   chmod 600 .env
   ```

4. Edit the local file in your editor. Set `AURUM_TELEGRAM_TOKEN` to your actual
   token using a quoted value. Leave `AURUM_TELEGRAM_CHAT_ID` empty for now.
   The committed example contains variable names only. Never commit `.env`.
5. Load this trusted local file and run the metadata inspection:

   ```sh
   set -a
   source .env
   set +a
   .venv/bin/aurum telegram-inspect
   ```

The command checks bot identity, checks for an existing webhook and, if none is
configured, inspects at most the first 20 pending updates. It returns private
chat ID candidates without message bodies, names or the token. It sends no
message, passes no update offset and changes no webhook or update filters.
It does not create or modify an AURUM database.

- `select_private_chat`: choose your intended private chat ID and put it in
  `AURUM_TELEGRAM_CHAT_ID` locally. Multiple candidates require your selection.
- `start_private_chat`: verify the bot username and send `/start` yourself.
  The limited pending page is not a complete chat history.
- `webhook_configured`: inspection leaves that webhook untouched and skips
  update polling. Use a fresh dedicated bot or review your existing integration.
- An error reports a sanitized failure code. Do not paste HTTP bodies or token
  URLs here; a bot username and failure code are enough for troubleshooting.

Telegram describes [bot creation](https://core.telegram.org/bots/features#botfather),
[getMe/getWebhookInfo/getUpdates](https://core.telegram.org/bots/api) and
[update acknowledgement](https://core.telegram.org/bots/faq#how-do-i-get-updates).
Inspection does not prove outbound delivery. A later controlled message test
needs your explicit instruction and a reviewed destination.

## Live monitoring comes later

After historical validation, arrange authorized live GLBX.MDP3 access for the
intended CME/COMEX use. Describe automated private research and alerting to the
vendor so it can confirm the applicable entitlement and fees. Do not assume an
existing charting subscription licenses this API use.

Put the vendor key in local `AURUM_DATABENTO_API_KEY`. Do not enter brokerage
credentials. AURUM already supports this key-variable name in provider Settings.
Saving a profile does not start a subscription. We will review the dated
contract and schedule before explicitly launching a monitor, then separately
verify timing, recovery and a controlled Telegram delivery.

## Short reply that unblocks the next step

Tell me: **account created / export local path / requested UTC period / raw MGC
symbol**. Later, confirm **Telegram bot configured locally**. Keep credentials
and chat IDs local. Account creation and payment decisions stay with you.
