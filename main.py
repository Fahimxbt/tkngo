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
promo_msg_id = None

match_active = False
promo_sent = False
sending_lock = asyncio.Lock()
promo_cancelled = False
finding_lock = asyncio.Lock()

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


async def find_promo_message():
    global promo_msg_id
    try:
        msgs = await client.get_messages('me', limit=50)
        for m in msgs:
            if m.reply_markup and m.reply_markup.rows:
                for row in m.reply_markup.rows:
                    if row.buttons:
                        promo_msg_id = m.id
                        print(f"[+] Button post found! (msg_id={m.id})")
                        return True
    except Exception as e:
        print(f"[!] Find error: {e}")

    print("[!] Send a button post to Saved Messages first!")
    return False


async def click_yes_skip():
    """Handle the 'Are you sure you want to skip?' confirmation dialog."""
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
                                print("[→] Yes, Skip clicked")
                                return True
    except Exception as e:
        print(f"[!] Yes Skip error: {e}")
    return False


async def click_next():
    global match_active, promo_sent, last_partner_time

    if finding_lock.locked():
        print("[*] Already finding partner, skipping...")
        return True

    async with finding_lock:
        base_delay = BOT_ID * 1.5
        random_delay = random.uniform(0, 3)
        total_delay = base_delay + random_delay
        print(f"[*] Anti-self-match: waiting {total_delay:.1f}s (bot_id={BOT_ID})...")
        await asyncio.sleep(total_delay)

        elapsed = asyncio.get_event_loop().time() - last_partner_time
        if elapsed < MIN_PARTNER_INTERVAL:
            wait = MIN_PARTNER_INTERVAL - elapsed
            print(f"[*] Rate limit: waiting {wait:.1f}s...")
            await asyncio.sleep(wait)

        print("[*] Looking for Next button...")

        try:
            msgs = await client.get_messages(bot_entity, limit=10)
            for m in msgs:
                if m.reply_markup:
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            btn_text = btn.text or ''
                            if 'Next' in btn_text or '❤️' in btn_text:
                                result = await safe_click(m, btn.text)
                                if result:
                                    print("[→] Next clicked")
                                    # Wait a moment to see if skip confirmation appears
                                    await asyncio.sleep(2)
                                    # Try to handle skip confirmation
                                    await click_yes_skip()
                                    match_active = False
                                    promo_sent = False
                                    last_partner_time = asyncio.get_event_loop().time()
                                    await asyncio.sleep(3)
                                    return True
        except Exception as e:
            print(f"[!] get_messages error: {e}")

        print("[!] Next button not found, using /next fallback")
        await safe_send_message(bot_entity, '/next')
        print("[→] /next sent")
        match_active = False
        promo_sent = False
        last_partner_time = asyncio.get_event_loop().time()
        await asyncio.sleep(3)
        return True


async def send_promo():
    global promo_sent, promo_cancelled

    if sending_lock.locked() or promo_sent:
        print("[*] Already sending or already sent, skipping...")
        return

    async with sending_lock:
        promo_cancelled = False
        print("[*] Starting promo forward...")

        try:
            if promo_cancelled:
                print("[!] Promo cancelled")
                return

            if promo_msg_id:
                await safe_forward_messages(bot_entity, promo_msg_id, 'me')
                print("[+] Promo forwarded!")
            else:
                await safe_send_message(bot_entity, "💜 @chatxbt_bot\nhttps://t.me/chatxbt_bot")
                print("[+] Text promo sent!")

            print("[*] Waiting 4 seconds before next...")
            await asyncio.sleep(4)

            promo_sent = True
            print("[✓] Promo complete!")

        except Exception as e:
            print(f"[!] Send error: {e}")
            promo_sent = False


@client.on(events.NewMessage(chats='@TalkNGoBot'))
async def handler(event):
    global match_active, promo_sent, promo_cancelled

    text = event.text or ''
    if event.out:
        return

    # ========== SKIP CONFIRMATION DIALOG ==========
    if 'are you sure you want to skip' in text.lower():
        print("[!] Skip confirmation detected!")
        await asyncio.sleep(1)
        await click_yes_skip()
        return

    # ========== PARTNER DISCONNECTED ==========
    if 'your partner has disconnected' in text.lower() or 'partner left' in text.lower():
        print("[✓] Partner disconnected!")
        match_active = False
        promo_sent = False

        if sending_lock.locked():
            promo_cancelled = True
            for _ in range(100):
                if not sending_lock.locked():
                    break
                await asyncio.sleep(0.1)

        await asyncio.sleep(2)
        await click_next()
        return

    # ========== YOU LEFT THE CHAT ==========
    if 'you left' in text.lower() or 'chat ended' in text.lower():
        print("[✓] Chat ended")
        match_active = False
        promo_sent = False
        await asyncio.sleep(2)
        await click_next()
        return

    # ========== MATCH STARTED ==========
    if 'chat connected' in text.lower():
        print("[+] Match started!")
        match_active = True
        promo_sent = False
        promo_cancelled = False

        await asyncio.sleep(1)
        await send_promo()

        if not promo_cancelled:
            await click_next()
        else:
            print("[!] Promo cancelled, finding next...")
            await asyncio.sleep(1)
            await click_next()
        return

    # ========== FINDING PARTNER ==========
    if 'waiting for a partner' in text.lower():
        print("[...] Searching...")
        match_active = False
        promo_sent = False
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
    print(f"[*] ChatBuddy Bot started! BOT_ID={BOT_ID}")

    bot_entity = await client.get_entity('@TalkNGoBot')
    msgs_found = await find_promo_message()

    if not msgs_found:
        print("[!] WARNING: Button post not found! Using text fallback.")

    await safe_send_message(bot_entity, '/next')
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        with client:
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[*] Bot stopped.")
    except Exception as e:
        print(f"[!] Fatal error: {e}")
        sys.exit(1)
