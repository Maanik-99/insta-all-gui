import asyncio
import re
import pyotp
from telethon import TelegramClient, events
from telethon.tl.custom import Button

# ==========================================
# আপনার টেলিগ্রাম API ক্রেডেনশিয়াল দিন
# (https://my.telegram.org থেকে পাবেন)
# ==========================================
API_ID = 37965026          # এখানে আপনার API_ID বসান (সংখ্যা)
API_HASH = '24ccd7aff0fbf93babff9bd12eadb6bb' # এখানে আপনার API_HASH বসান (স্ট্রিং)
PHONE_NUMBER = '+8801648266488' # আপনার টেলিগ্রাম নাম্বার

TARGET_BOT = 'PayGoeasy_bot' # অথবা 'PayGoBot'

# অটো রিপিট করতে চাইলে True রাখুন
AUTO_REPEAT_TASKS = False
DELAY_BETWEEN_STEPS = 2.0  # সেকেন্ড

client = TelegramClient('taskly_user_session', API_ID, API_HASH)

# টাস্ক ডাটা স্টোরেজ
current_task = {
    'first_name': None,
    'login': None,
    'password': None,
    'two_fa_key': None
}

async def click_any_button(message, pattern):
    """কিবোর্ড অথবা ইনলাইন বাটন ক্লিক করার ইউনিভার্সাল ফাংশন"""
    if message.buttons:
        for row in message.buttons:
            for btn in row:
                if re.search(pattern, btn.text, re.IGNORECASE):
                    print(f"👉 [Button Click]: '{btn.text}'")
                    try:
                        await btn.click()
                        return True
                    except Exception as e:
                        print(f"⚠️ Button click error: {e}")
    return False

@client.on(events.NewMessage(chats=TARGET_BOT))
async def handle_bot_message(event):
    msg = event.message
    text = msg.text or ""
    print(f"\n📩 [Bot Message]:\n{text}\n" + "-"*40)
    
    # ১. যদি বট ওয়েলকাম মেসেজ দেয় (/start এর উত্তর)
    if "Welcome" in text or "Terms of Use" in text or "earn money" in text:
        print("💡 Step 1: Welcome received -> Opening Tasks menu...")
        await asyncio.sleep(DELAY_BETWEEN_STEPS)
        clicked = await click_any_button(msg, r"Tasks|📋")
        if not clicked:
            await client.send_message(TARGET_BOT, "📋 Tasks")

    # ২. যদি বট টাস্ক সিলেক্ট করতে বলে
    elif "Please select a task" in text or "select a task" in text:
        print("💡 Step 2: Selecting task '🔥Create Inst (2FA)'...")
        await asyncio.sleep(DELAY_BETWEEN_STEPS)
        clicked = await click_any_button(msg, r"Create Inst.*2FA|2FA|No mail|🔥")
        if not clicked:
            await client.send_message(TARGET_BOT, "🔥Create Inst (2FA) ($0.0250)")

    # ৩. যদি টাস্ক ডেসক্রিপশন আসে (Review time) এবং স্টার্ট বাটন চায়
    elif "Review time" in text or "Task: 🔥Create Inst" in text or "Task: 📱 Create Inst" in text or "Create Inst (2FA)" in text or "Task:" in text:
        print("💡 Step 3: Starting the task...")
        await asyncio.sleep(DELAY_BETWEEN_STEPS)
        clicked = await click_any_button(msg, r"Start|▶️")
        if not clicked:
            await client.send_message(TARGET_BOT, "▶️ Start")

    # ৪. যদি লগইন ক্রেডেনশিয়াল দেয় (First name, Login, Password)
    elif "First name:" in text and "Login:" in text:
        print("💡 Step 4: Account Credentials Received!")
        fn_match = re.search(r"First name:\s*(.+)", text)
        login_match = re.search(r"Login:\s*(.+)", text)
        pass_match = re.search(r"Password:\s*(.+)", text)
        
        current_task['first_name'] = fn_match.group(1).strip() if fn_match else "N/A"
        current_task['login'] = login_match.group(1).strip() if login_match else "N/A"
        current_task['password'] = pass_match.group(1).strip() if pass_match else "N/A"
        
        print("\n" + "="*35)
        print("📋 [INSTAGRAM CREDENTIALS]")
        print(f"👤 First Name : {current_task['first_name']}")
        print(f"🔑 Login/User : {current_task['login']}")
        print(f"🔒 Password   : {current_task['password']}")
        print("="*35 + "\n")
        
        # ইনলাইন বাটন থাকলে (যেমন 'Get code')
        if msg.buttons:
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            await click_any_button(msg, r"Get code|📥")

    # ৫. যদি বট 2FA key চায় ("Please enter your 2FA key to get the code:")
    elif "2FA key" in text or "enter your 2FA" in text:
        print("💡 Step 5: Generating and sending 2FA Key...")
        await asyncio.sleep(DELAY_BETWEEN_STEPS)
        
        # একটি ভ্যালিড Base32 2FA Secret Key তৈরি করা
        two_fa_secret = pyotp.random_base32()
        current_task['two_fa_key'] = two_fa_secret
        totp_code = pyotp.TOTP(two_fa_secret).now()
        
        print(f"🔐 Generated 2FA Secret Key: {two_fa_secret}")
        print(f"🔢 Current TOTP (6-digit Code): {totp_code}")
        
        # বটকে 2FA Key পাঠানো
        await client.send_message(TARGET_BOT, two_fa_secret)

    # ৬. যদি সিকিউরিটি কোড রিড করতে বলে বা 'Account Registered' বাটন আসে
    elif "Security Code" in text or "code" in text.lower() or "Account Registered" in text:
        print("💡 Step 6: Verification code received or Ready to confirm registration...")
        code_match = re.search(r"\b\d{6}\b", text)
        if code_match:
            print(f"📩 Verification Code: {code_match.group(0)}")
            
        await asyncio.sleep(DELAY_BETWEEN_STEPS + 1)
        # "Account Registered" বাটনে ক্লিক করা
        clicked = await click_any_button(msg, r"Account Registered|Registered|✅")
        if not clicked:
            print("⚠️ 'Account Registered' button not found on this message.")

    # ৭. যদি টাস্ক সফলভাবে সম্পন্ন হয়
    elif "report approved" in text.lower() or "report received" in text.lower() or "Task Completed" in text or "credited to your balance" in text or "🎉" in text:
        print("\n" + "🎉"*15)
        print("✅ SUCCESS! Task Completed & Balance Credited!")
        print("🎉"*15 + "\n")
        
        if AUTO_REPEAT_TASKS:
            print("⏳ Waiting 5 seconds before starting next task...")
            await asyncio.sleep(5)
            await client.send_message(TARGET_BOT, "📋 Tasks")

    # ৮. যেকোনো সাধারণ বাটন হ্যান্ডলিং
    elif msg.buttons:
        # যদি "Get code" থাকে
        await click_any_button(msg, r"Get code|📥")

async def main():
    print("🚀 Connecting Telegram Userbot...")
    await client.start(phone=PHONE_NUMBER)
    print("✅ Userbot Logged in successfully!")
    
    # কাজ শুরু করতে বটকে /start পাঠানো
    print(f"📨 Sending /start to @{TARGET_BOT}...")
    await client.send_message(TARGET_BOT, "/start")
    
    print("🤖 Userbot is actively listening and running automation...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())

