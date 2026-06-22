from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import asyncio
import os
import sys
import random
import time

# ========== CONFIG FROM ENVIRONMENT VARIABLES ==========
STRING_SESSION = os.environ.get('STRING_SESSION', '')
API_ID = int(os.environ.get('API_ID', '0'))
API_HASH = os.environ.get('API_HASH', '')
BOT_ID = int(os.environ.get('BOT_ID', '1'))
# ========================================================

if not STRING_SESSION or not API_ID or not API_HASH:
    print("[!] ERROR: Missing environment variables!")
    print("    Required: STRING_SESSION, API_ID, API_HASH")
    sys.exit(1)

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

bot_entity = None
sticker_msg_id = None
heyyy_msg_id = None

match_active = False
promo_sent = False
sending_lock = asyncio.Lock()
promo_cancelled = False
finding_lock = asyncio.Lock()
waiting_for_partner = False
self_match_detected = False
match_start_time = 0

# Timeout protection
PARTNER_SEARCH_TIMEOUT = 45
last_search_start_time = 0
search_timeout_task = None

# ANTI-SELF-MATCH settings
STAGGER_GAP = 8
MIN_PARTNER_INTERVAL = STAGGER_GAP * 10 + 10
last_partner_time = 0

# Our exact promo signatures (lowercase for comparison)
PROMO_TEXT = "can you believe what i just saw here"
HEYYY_TEXT = "heyyy"

# Stuck detection
STUCK_TIMEOUT = 90
stuck_watchdog_task = None


async def safe_send_message(entity, message, retries=3):
    for attempt in range(retries):
        try:
            return await client.send_message(entity, message)
        except FloodWaitError as e:
            print(f"[!] FloodWait: Waiting {e.seconds} seconds...")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            print(f"[!] Send error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    return None


async def safe_forward_messages(entity, msg_id, from_peer, retries=3):
    for attempt in range(retries):
        try:
            return await client.forward_messages(entity, msg_id, from_peer)
        except FloodWaitError as e:
            print(f"[!] FloodWait: Waiting {e.seconds} seconds...")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            print(f"[!] Forward error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    return None


async def safe_click(message, text, retries=3):
    for attempt in range(retries):
        try:
            return await message.click(text=text)
        except FloodWaitError as e:
            print(f"[!] FloodWait on click: Waiting {e.seconds} seconds...")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            print(f"[!] Click error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    return None


async def find_messages():
    global sticker_msg_id, heyyy_msg_id
    try:
        msgs = await client.get_messages('me', limit=50)
        for m in msgs:
            if m.sticker and not sticker_msg_id:
                sticker_msg_id = m.id
                print("[+] Sticker found!")
            if m.text and m.text.lower() == HEYYY_TEXT and not heyyy_msg_id:
                heyyy_msg_id = m.id
                print("[+] 'heyyy' message found!")

        if sticker_msg_id and heyyy_msg_id:
            print("[+] All messages found!")
            return True

    except Exception as e:
        print(f"[!] Find error: {e}")

    print("[!] Send 'heyyy' and a sticker to Saved Messages first!")
    return False


async def dismiss_rating():
    try:
        msgs = await client.get_messages(bot_entity, limit=5)
        for m in msgs:
            if m.reply_markup and m.reply_markup.rows:
                for row in m.reply_markup.rows:
                    for btn in row.buttons:
                        btn_text = btn.text or ''
                        if 'like' in btn_text.lower() or 'dislike' in btn_text.lower():
                            result = await safe_click(m, btn.text)
                            if result:
                                print(f"[→] Rating dismissed: {btn_text}")
                                await asyncio.sleep(2)
                                return True
    except Exception as e:
        print(f"[!] Dismiss rating error: {e}")
    return False


async def click_yes_skip():
    try:
        msgs = await client.get_messages(bot_entity, limit=5)
        for m in msgs:
            if m.reply_markup and m.reply_markup.rows:
                for row in m.reply_markup.rows:
                    for btn in row.buttons:
                        btn_text = btn.text or ''
                        if 'yes, skip' in btn_text.lower() or 'skip' in btn_text.lower():
                            result = await safe_click(m, btn.text)
                            if result:
                                print(f"[→] Skip confirmed: {btn_text}")
                                await asyncio.sleep(2)
                                return True
    except Exception as e:
        print(f"[!] Yes Skip error: {e}")
    return False


async def force_end_chat():
    try:
        await safe_send_message(bot_entity, '/end')
        print("[→] /end sent to force close chat")
        await asyncio.sleep(2)
        await click_yes_skip()
        return True
    except Exception as e:
        print(f"[!] Force end error: {e}")
    return False


async def click_next():
    global match_active, promo_sent, last_partner_time, waiting_for_partner
    global last_search_start_time, search_timeout_task, stuck_watchdog_task

    if finding_lock.locked():
        print("[*] Already finding partner, skipping...")
        return True

    async with finding_lock:
        if search_timeout_task and not search_timeout_task.done():
            search_timeout_task.cancel()
            try:
                await search_timeout_task
            except asyncio.CancelledError:
                pass
        if stuck_watchdog_task and not stuck_watchdog_task.done():
            stuck_watchdog_task.cancel()
            try:
                await stuck_watchdog_task
            except asyncio.CancelledError:
                pass

        # ANTI-SELF-MATCH: Staggered delay based on BOT_ID
        base_delay = (BOT_ID - 1) * STAGGER_GAP
        random_delay = random.uniform(0, 3)
        total_delay = base_delay + random_delay
        print(f"[*] Anti-self-match: waiting {total_delay:.1f}s before clicking (bot_id={BOT_ID})...")
        await asyncio.sleep(total_delay)

        elapsed = asyncio.get_event_loop().time() - last_partner_time
        if elapsed < MIN_PARTNER_INTERVAL:
            wait = MIN_PARTNER_INTERVAL - elapsed
            print(f"[*] Rate limit: waiting {wait:.1f}s before next search...")
            await asyncio.sleep(wait)

        print("[*] Looking for Next button...")

        try:
            msgs = await client.get_messages(bot_entity, limit=10)
            for m in msgs:
                if m.reply_markup:
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            btn_text = btn.text or ''
                            if 'Next' in btn_text:
                                result = await safe_click(m, btn.text)
                                if result:
                                    print("[→] Next clicked")
                                    await asyncio.sleep(2)
                                    await click_yes_skip()
                                    match_active = False
                                    promo_sent = False
                                    waiting_for_partner = True
                                    last_partner_time = asyncio.get_event_loop().time()
                                    last_search_start_time = asyncio.get_event_loop().time()
                                    search_timeout_task = asyncio.create_task(search_timeout_watchdog())
                                    await asyncio.sleep(3)
                                    return True
        except Exception as e:
            print(f"[!] get_messages error: {e}")

        print("[!] Next button not found, using /next fallback")
        await safe_send_message(bot_entity, '/next')
        print("[→] /next sent (fallback)")
        match_active = False
        promo_sent = False
        waiting_for_partner = True
        last_partner_time = asyncio.get_event_loop().time()
        last_search_start_time = asyncio.get_event_loop().time()
        search_timeout_task = asyncio.create_task(search_timeout_watchdog())
        await asyncio.sleep(3)
        return True


async def search_timeout_watchdog():
    global waiting_for_partner
    try:
        await asyncio.sleep(PARTNER_SEARCH_TIMEOUT)
        if waiting_for_partner and not match_active:
            print(f"[!] Timeout: No partner found in {PARTNER_SEARCH_TIMEOUT}s, retrying...")
            await dismiss_rating()
            await safe_send_message(bot_entity, '/next')
            print("[→] /next sent (timeout retry)")
            last_search_start_time = asyncio.get_event_loop().time()
            asyncio.create_task(search_timeout_watchdog())
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[!] Watchdog error: {e}")


async def stuck_watchdog():
    global match_active, promo_sent
    try:
        await asyncio.sleep(STUCK_TIMEOUT)
        if match_active:
            elapsed = time.time() - match_start_time
            if elapsed >= STUCK_TIMEOUT:
                print(f"[!] STUCK DETECTED: Match active for {elapsed:.0f}s, forcing next...")
                match_active = False
                promo_sent = False
                await force_end_chat()
                await asyncio.sleep(3)
                await click_next()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[!] Stuck watchdog error: {e}")


async def send_promo():
    global promo_sent, promo_cancelled

    if sending_lock.locked() or promo_sent:
        print("[*] Already sending or already sent, skipping...")
        return

    async with sending_lock:
        promo_cancelled = False
        print("[*] Starting promo sequence...")

        try:
            # Step 1: Send "heyyy" IMMEDIATELY
            if promo_cancelled:
                print("[!] Promo cancelled before heyyy")
                return

            if heyyy_msg_id:
                await safe_forward_messages(bot_entity, heyyy_msg_id, 'me')
                print("[+] Forwarded: heyyy")
            else:
                await safe_send_message(bot_entity, "heyyy")
                print("[+] Sent: heyyy")

            # Wait 4 seconds
            print("[*] Waiting 4 seconds...")
            await asyncio.sleep(4)

            # Step 2: Send "Can you believe what I just saw here"
            if promo_cancelled:
                print("[!] Promo cancelled before believe message")
                return

            await safe_send_message(bot_entity, "Can you believe what I just saw here")
            print("[+] Sent: Can you believe what I just saw here")

            # Wait 3 seconds
            print("[*] Waiting 3 seconds...")
            await asyncio.sleep(3)

            # Step 3: Forward sticker
            if promo_cancelled:
                print("[!] Promo cancelled before sticker")
                return

            if sticker_msg_id:
                await safe_forward_messages(bot_entity, sticker_msg_id, 'me')
                print("[+] Sticker forwarded!")
            else:
                await safe_send_message(bot_entity, "💜 @chatxbt_bot\nhttps://t.me/chatxbt_bot")
                print("[+] Text promo sent!")

            # Wait 8 seconds after sticker before going next
            print("[*] Waiting 8 seconds after sticker...")
            await asyncio.sleep(8)

            promo_sent = True
            print("[✓] Promo complete, proceeding to next...")

        except Exception as e:
            print(f"[!] Send error: {e}")
            promo_sent = False


async def handle_self_match():
    global match_active, promo_sent, self_match_detected
    print("[!] Handling self-match skip...")
    self_match_detected = True

    if sending_lock.locked():
        global promo_cancelled
        promo_cancelled = True
        for _ in range(100):
            if not sending_lock.locked():
                break
            await asyncio.sleep(0.1)

    match_active = False
    promo_sent = False
    await asyncio.sleep(1)
    await force_end_chat()
    await asyncio.sleep(2)
    await click_next()


@client.on(events.NewMessage(chats='@TalkNGoBot'))
async def handler(event):
    global match_active, promo_sent, promo_cancelled, waiting_for_partner
    global search_timeout_task, stuck_watchdog_task, self_match_detected
    global match_start_time

    text = event.text or ''
    text_lower = text.lower()

    if event.out:
        return

    # ========== ANTI-SELF-MATCH: Only detect OUR exact promo sequence ==========
    # A random user sending "Heyyyy" or "hi" should NOT trigger this
    # Only trigger if partner sends the EXACT believe text OR sticker before we send ours
    if match_active and not event.out and not promo_sent:
        # If partner sends our exact promo text = 100% our bot
        if text_lower == PROMO_TEXT:
            print("[!] SELF-MATCH DETECTED: Partner sent our exact promo text!")
            await handle_self_match()
            return

        # If partner sends sticker before we do = likely our bot
        if event.message.sticker:
            print("[!] SELF-MATCH DETECTED: Partner sent sticker before us!")
            await handle_self_match()
            return

    # ========== PARTNER LEFT THE CHAT ==========
    if 'partner has left' in text_lower or 'partner ended' in text_lower:
        print("[✓] Partner left the chat!")
        match_active = False
        promo_sent = False
        waiting_for_partner = False
        self_match_detected = False

        if sending_lock.locked():
            print("[!] Cancelling promo...")
            promo_cancelled = True
            for _ in range(100):
                if not sending_lock.locked():
                    break
                await asyncio.sleep(0.1)

        await asyncio.sleep(2)
        await dismiss_rating()
        await click_next()
        return

    # ========== YOU LEFT THE CHAT ==========
    if 'you left' in text_lower:
        print("[✓] You left the chat")
        match_active = False
        promo_sent = False
        waiting_for_partner = False
        self_match_detected = False
        await asyncio.sleep(2)
        await dismiss_rating()
        await click_next()
        return

    # ========== MATCH STARTED ==========
    if 'Chat Connected!' in text:
        print("[+] Match started!")
        match_active = True
        promo_sent = False
        promo_cancelled = False
        waiting_for_partner = False
        self_match_detected = False
        match_start_time = time.time()

        # Cancel search timeout
        if search_timeout_task and not search_timeout_task.done():
            search_timeout_task.cancel()

        # Start stuck watchdog
        stuck_watchdog_task = asyncio.create_task(stuck_watchdog())

        # Send heyyy IMMEDIATELY - no decoy delay
        await asyncio.sleep(1)  # Small 1s buffer for bot to stabilize
        await send_promo()

        if not promo_cancelled and not self_match_detected:
            await click_next()
        else:
            print("[!] Promo cancelled or self-match, finding next...")
            await asyncio.sleep(1)
            await click_next()
        return

    # ========== FINDING PARTNER ==========
    if 'Waiting for a partner' in text:
        print("[...] Searching...")
        match_active = False
        promo_sent = False
        waiting_for_partner = True
        return

    # ========== PARTNER SENT MESSAGE DURING MATCH ==========
    if match_active and not promo_sent and not sending_lock.locked():
        print("[+] Partner messaged first!")
        await send_promo()

        if not promo_cancelled and not self_match_detected:
            await click_next()
        else:
            print("[!] Promo cancelled or self-match, finding next...")
            await asyncio.sleep(1)
            await click_next()
        return


async def main():
    global bot_entity
    await client.start()
    print(f"[*] ChatBuddy bot (@TalkNGoBot) started! BOT_ID={BOT_ID}")
    print(f"[*] STAGGER_GAP={STAGGER_GAP}s | MIN_PARTNER_INTERVAL={MIN_PARTNER_INTERVAL}s")
    print(f"[*] STUCK_TIMEOUT={STUCK_TIMEOUT}s")
    print("[*] Flow: heyyy → 4s → believe → 3s → sticker → 8s → Next")
    print("[*] Connected to Telegram successfully!")

    bot_entity = await client.get_entity('@TalkNGoBot')
    msgs_found = await find_messages()

    if not msgs_found:
        print("[!] WARNING: Some messages not found in Saved Messages!")
        print("[!] The bot will use text fallback for missing messages.")

    await safe_send_message(bot_entity, '/next')
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        with client:
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[*] Bot stopped by user.")
    except Exception as e:
        print(f"[!] Fatal error: {e}")
        sys.exit(1)
