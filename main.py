from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import asyncio
import os
import time
import sys  # BUG FIX #1: Added missing import

# ========== CONFIG FROM ENVIRONMENT VARIABLES ==========
STRING_SESSION = os.environ.get('STRING_SESSION', '')
API_ID = int(os.environ.get('API_ID', '0'))
API_HASH = os.environ.get('API_HASH', '')
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
next_pending = False  # BUG FIX #9: Prevent double click_next


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


async def safe_click(message, data=None, text=None, retries=3):
    """BUG FIX #4: Click by button data or text, avoid double search"""
    for attempt in range(retries):
        try:
            if data is not None:
                return await message.click(data=data)
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
            if m.text and m.text.lower() == 'heyyy' and not heyyy_msg_id:
                heyyy_msg_id = m.id
                print("[+] 'heyyy' message found!")

        if sticker_msg_id and heyyy_msg_id:
            print("[+] All messages found!")
            return True
        elif sticker_msg_id:
            print("[!] 'heyyy' message missing in Saved Messages!")
        elif heyyy_msg_id:
            print("[!] Sticker missing in Saved Messages!")
        else:
            print("[!] Both messages missing!")

    except Exception as e:
        print(f"[!] Find error: {e}")

    print("[!] Send 'heyyy' and a sticker to Saved Messages first!")
    return False


async def click_next():
    global next_pending
    if next_pending:
        print("[*] Next already pending, skipping")
        return False
    next_pending = True
    
    print("[*] Looking for Next button...")
    try:
        msgs = await client.get_messages(bot_entity, limit=10)
        for m in msgs:
            if m.reply_markup:
                for row in m.reply_markup.rows:
                    for btn in row.buttons:
                        if 'Next' in btn.text:
                            result = await safe_click(m, text=btn.text)
                            if result:
                                print("[→] Next clicked")
                                await asyncio.sleep(2)
                                # Click Yes, Skip if confirmation appears
                                msgs2 = await client.get_messages(bot_entity, limit=5)
                                for m2 in msgs2:
                                    if m2.reply_markup:
                                        for row2 in m2.reply_markup.rows:
                                            for btn2 in row2.buttons:
                                                if 'yes' in btn2.text.lower() and 'skip' in btn2.text.lower():
                                                    await safe_click(m2, text=btn2.text)
                                                    print(f"[→] Skip confirmed: {btn2.text}")
                                                    await asyncio.sleep(2)
                                                    break
                                await asyncio.sleep(3)
                                next_pending = False
                                return True
    except Exception as e:
        print(f"[!] get_messages error: {e}")

    print("[!] Next button not found, using /next fallback")
    await safe_send_message(bot_entity, '/next')
    print("[→] /next sent")
    await asyncio.sleep(3)
    next_pending = False
    return True


async def send_promo():
    global promo_sent, match_active

    if sending_lock.locked() or promo_sent:
        return

    async with sending_lock:
        print("[*] Starting promo sequence...")

        try:
            # Step 1: Send "heyyy" immediately
            if heyyy_msg_id:
                await safe_forward_messages(bot_entity, heyyy_msg_id, 'me')
                print("[+] Forwarded: heyyy")
            else:
                await safe_send_message(bot_entity, "heyyy")
                print("[+] Sent: heyyy")

            await asyncio.sleep(4)

            # Step 2: Send "Can you believe what I just saw here"
            if not match_active:
                print("[!] Match ended, aborting")
                return

            await safe_send_message(bot_entity, "Can you believe what I just saw here")
            print("[+] Sent: Can you believe what I just saw here")

            await asyncio.sleep(3)

            # Step 3: Forward sticker
            if not match_active:
                print("[!] Match ended, aborting")
                return

            if sticker_msg_id:
                await safe_forward_messages(bot_entity, sticker_msg_id, 'me')
                print("[+] Sticker forwarded!")
            else:
                await safe_send_message(bot_entity, "💜 @chatxbt_bot\nhttps://t.me/chatxbt_bot")
                print("[+] Text promo sent!")

            # BUG FIX #2: Check match_active after 8s sleep too
            await asyncio.sleep(8)
            if not match_active:
                print("[!] Match ended during final wait, aborting")
                return

            promo_sent = True
            print("[✓] Promo complete")

        except Exception as e:
            print(f"[!] Send error: {e}")
            # BUG FIX: Don't set promo_sent on error, allow retry


@client.on(events.NewMessage(chats='@TalkNGoBot'))
async def handler(event):
    global match_active, promo_sent, next_pending

    text = event.text or ''
    text_lower = text.lower()

    if event.out:
        return

    # ========== PARTNER LEFT / DISCONNECTED / ENDED ==========
    if ('partner has left' in text_lower or 
        'partner ended' in text_lower or 
        'partner has disconnected' in text_lower or
        'your partner has disconnected' in text_lower):
        print("[✓] Partner left!")
        match_active = False
        promo_sent = False
        next_pending = False  # Reset pending state
        await asyncio.sleep(2)
        await click_next()
        return

    # ========== YOU LEFT ==========
    if 'you left' in text_lower:
        print("[✓] You left")
        match_active = False
        promo_sent = False
        next_pending = False
        await asyncio.sleep(2)
        await click_next()
        return

    # ========== NOT IN CHAT ==========
    if "not in a chat" in text_lower:
        print("[!] Not in chat")
        match_active = False
        promo_sent = False
        next_pending = False
        await asyncio.sleep(2)
        await click_next()
        return

    # ========== MATCH STARTED ==========
    if 'Chat Connected!' in text:
        print("[+] Match started!")
        match_active = True
        promo_sent = False
        next_pending = False

        await asyncio.sleep(1)
        await send_promo()

        if match_active:
            await click_next()
        else:
            print("[!] Match ended during promo")
            await asyncio.sleep(1)
            await click_next()
        return

    # ========== FINDING PARTNER ==========
    if 'Waiting for a partner' in text_lower:
        print("[...] Searching...")
        match_active = False
        promo_sent = False
        next_pending = False
        return

    # ========== PARTNER MESSAGED FIRST ==========
    # BUG FIX: Only trigger if promo hasn't started AND next isn't pending
    if match_active and not promo_sent and not sending_lock.locked() and not next_pending:
        print("[+] Partner messaged first!")
        await send_promo()

        if match_active:
            await click_next()
        else:
            print("[!] Match ended during promo")
            await asyncio.sleep(1)
            await click_next()
        return


async def main():
    global bot_entity
    await client.start()
    print("[*] ChatBuddy bot (@TalkNGoBot) started!")
    print("[*] Flow: heyyy → 4s → believe → 3s → sticker → 8s → Next")
    print("[*] Connected to Telegram successfully!")

    bot_entity = await client.get_entity('@TalkNGoBot')
    msgs_found = await find_messages()

    if not msgs_found:
        print("[!] WARNING: Some messages not found in Saved Messages!")

    await safe_send_message(bot_entity, '/next')
    await client.run_until_disconnected()


if __name__ == '__main__':
    while True:
        try:
            with client:
                client.loop.run_until_complete(main())
        except KeyboardInterrupt:
            print("\n[*] Bot stopped by user.")
            break
        except Exception as e:
            print(f"[!] Fatal error: {e}")
            print("[*] Restarting in 10 seconds...")
            time.sleep(10)
