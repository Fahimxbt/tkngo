from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import asyncio
import os
import sys
import random

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
f_msg_id = None

match_active = False
promo_sent = False
sending_lock = asyncio.Lock()
promo_cancelled = False
finding_lock = asyncio.Lock()
waiting_for_partner = False

# Timeout protection
PARTNER_SEARCH_TIMEOUT = 30  # seconds
last_search_start_time = 0
search_timeout_task = None

MIN_PARTNER_INTERVAL = 15
last_partner_time = 0


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
    global sticker_msg_id, heyyy_msg_id, f_msg_id
    try:
        msgs = await client.get_messages('me', limit=50)
        for m in msgs:
            if m.sticker and not sticker_msg_id:
                sticker_msg_id = m.id
                print("[+] Sticker found!")
            if m.text and m.text.lower() == 'heyyy' and not heyyy_msg_id:
                heyyy_msg_id = m.id
                print("[+] 'heyyy' message found!")
            if m.text and m.text.upper() == 'F' and not f_msg_id:
                f_msg_id = m.id
                print("[+] 'F' message found!")

        if all([sticker_msg_id, heyyy_msg_id, f_msg_id]):
            print("[+] All messages found!")
            return True

    except Exception as e:
        print(f"[!] Find error: {e}")

    print("[!] Send 'heyyy', 'F', and a sticker to Saved Messages first!")
    return False


async def dismiss_rating():
    """Click Like or Dislike to dismiss the rating screen."""
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


async def click_next():
    global match_active, promo_sent, last_partner_time, waiting_for_partner, last_search_start_time, search_timeout_task

    if finding_lock.locked():
        print("[*] Already finding partner, skipping...")
        return True

    async with finding_lock:
        # Cancel any existing timeout task
        if search_timeout_task and not search_timeout_task.done():
            search_timeout_task.cancel()
            try:
                await search_timeout_task
            except asyncio.CancelledError:
                pass

        # ANTI-SELF-MATCH: staggered random delay based on BOT_ID
        base_delay = BOT_ID * 1.5
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
                                    match_active = False
                                    promo_sent = False
                                    waiting_for_partner = True
                                    last_partner_time = asyncio.get_event_loop().time()
                                    last_search_start_time = asyncio.get_event_loop().time()
                                    # Start timeout watchdog
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
        # Start timeout watchdog
        search_timeout_task = asyncio.create_task(search_timeout_watchdog())
        await asyncio.sleep(3)
        return True


async def search_timeout_watchdog():
    """If no partner found within PARTNER_SEARCH_TIMEOUT seconds, send /next again."""
    global waiting_for_partner
    try:
        await asyncio.sleep(PARTNER_SEARCH_TIMEOUT)
        if waiting_for_partner and not match_active:
            print(f"[!] Timeout: No partner found in {PARTNER_SEARCH_TIMEOUT}s, retrying...")
            # Try to dismiss rating screen first
            await dismiss_rating()
            # Send /next to kickstart search again
            await safe_send_message(bot_entity, '/next')
            print("[→] /next sent (timeout retry)")
            # Reset timer
            last_search_start_time = asyncio.get_event_loop().time()
            # Restart watchdog
            asyncio.create_task(search_timeout_watchdog())
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[!] Watchdog error: {e}")


async def send_promo():
    global promo_sent, promo_cancelled

    if sending_lock.locked() or promo_sent:
        print("[*] Already sending or already sent, skipping...")
        return

    async with sending_lock:
        promo_cancelled = False
        print("[*] Starting forward sequence...")

        try:
            # Step 1: Forward "heyyy" immediately
            if promo_cancelled:
                print("[!] Promo cancelled before heyyy")
                return

            if heyyy_msg_id:
                await safe_forward_messages(bot_entity, heyyy_msg_id, 'me')
                print("[+] Forwarded: heyyy")
            else:
                await safe_send_message(bot_entity, "heyyy")
                print("[+] Sent: heyyy")

            # Wait 3 seconds
            print("[*] Waiting 3 seconds...")
            await asyncio.sleep(3)

            # Step 2: Forward "F"
            if promo_cancelled:
                print("[!] Promo cancelled before F")
                return

            if f_msg_id:
                await safe_forward_messages(bot_entity, f_msg_id, 'me')
                print("[+] Forwarded: F")
            else:
                await safe_send_message(bot_entity, "F")
                print("[+] Sent: F")

            # Wait 4 seconds
            print("[*] Waiting 4 seconds...")
            await asyncio.sleep(4)

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

            promo_sent = True
            print("[✓] Sticker sent, proceeding to next...")

        except Exception as e:
            print(f"[!] Send error: {e}")
            promo_sent = False


@client.on(events.NewMessage(chats='@tikible_bot'))
async def handler(event):
    global match_active, promo_sent, promo_cancelled, waiting_for_partner, search_timeout_task

    text = event.text or ''

    if event.out:
        return

    # ========== PARTNER LEFT THE CHAT ==========
    if 'partner has left' in text.lower() or 'partner ended' in text.lower():
        print("[✓] Partner left the chat!")
        match_active = False
        promo_sent = False
        waiting_for_partner = False

        if sending_lock.locked():
            print("[!] Cancelling promo...")
            promo_cancelled = True
            for _ in range(100):
                if not sending_lock.locked():
                    break
                await asyncio.sleep(0.1)

        await asyncio.sleep(2)
        # Dismiss rating screen if present
        await dismiss_rating()
        await click_next()
        return

    # ========== YOU LEFT THE CHAT ==========
    if 'you left' in text.lower():
        print("[✓] You left the chat")
        match_active = False
        promo_sent = False
        waiting_for_partner = False
        await asyncio.sleep(2)
        # Dismiss rating screen if present
        await dismiss_rating()
        await click_next()
        return

    # ========== MATCH STARTED ==========
    if 'Match successful' in text:
        print("[+] Match started!")
        match_active = True
        promo_sent = False
        promo_cancelled = False
        waiting_for_partner = False

        # Cancel timeout watchdog since we found a partner
        if search_timeout_task and not search_timeout_task.done():
            search_timeout_task.cancel()

        await asyncio.sleep(1)
        await send_promo()

        # After promo, click next with bot_id wait
        if not promo_cancelled:
            await click_next()
        else:
            print("[!] Promo cancelled, finding next...")
            await asyncio.sleep(1)
            await click_next()
        return

    # ========== FINDING PARTNER ==========
    if 'Finding a random partner' in text:
        print("[...] Searching...")
        match_active = False
        promo_sent = False
        waiting_for_partner = True
        return

    # ========== PARTNER SENT MESSAGE DURING MATCH ==========
    if match_active and not promo_sent and not sending_lock.locked():
        print("[+] Partner messaged first!")
        await send_promo()

        if not promo_cancelled:
            await click_next()
        else:
            print("[!] Promo cancelled, finding next...")
            await asyncio.sleep(1)
            await click_next()
        return


async def main():
    global bot_entity
    await client.start()
    print(f"[*] xbt1-bot (@tikible_bot) started! BOT_ID={BOT_ID}")
    print("[*] Connected to Telegram successfully!")

    bot_entity = await client.get_entity('@tikible_bot')
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
