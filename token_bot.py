import requests
import time
import json
import logging
from datetime import datetime
import threading

# ==================== CONFIGURATION - FILL THESE IN! ====================
# Get these from:
# 1. Solscan API Key: https://pro-api.solscan.io/ (your account dashboard)
# 2. Telegram Bot Token: Message @BotFather on Telegram

SOLSCAN_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjcmVhdGVkQXQiOjE3NzE2Njc5NTc2MjgsImVtYWlsIjoiZGl2aW5lc3RlcGhlbjczMTlAZ21haWwuY29tIiwiYWN0aW9uIjoidG9rZW4tYXBpIiwiYXBpVmVyc2lvbiI6InYyIiwiaWF0IjoxNzcxNjY3OTU3fQ.i7J3-SwHl2RNoSb7ulmWy6NuefPgiKN9QRvRnvprftQ"  # <--- REPLACE THIS
TELEGRAM_BOT_TOKEN = "8664060489:AAF58vi4mjr0FpBHvBj5A_I19RKW7G6Tv-Q"  # <--- YOUR TELEGRAM TOKEN

# Optional settings - you can change these
POLL_INTERVAL = 60  # Check for new tokens every 60 seconds
PLATFORM_FILTER = "pumpfun"  # Set to None for all platforms, or "pumpfun", "raydium", etc.
# ========================================================================

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store active chats and seen tokens
active_chats = set()
seen_tokens = set()
tracking_active = False

def fetch_latest_tokens():
    """Fetch the latest tokens from Solscan Pro API"""
    url = "https://pro-api.solscan.io/v2.0/token/latest"
    
    params = {
        "page": 1,
        "page_size": 10  # Get the 10 most recent tokens
    }
    
    # Add platform filter if specified
    if PLATFORM_FILTER:
        params["platform_id"] = PLATFORM_FILTER
    
    headers = {
        "accept": "application/json",
        "token": SOLSCAN_API_KEY  # This is where your API key goes
    }
    
    try:
        logger.info(f"Fetching latest tokens from Solscan...")
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        # Check if API key is valid
        if response.status_code == 401:
            logger.error("❌ Invalid Solscan API key! Please check your key.")
            logger.error("Get your key from: https://pro-api.solscan.io/")
            return []
        
        response.raise_for_status()
        data = response.json()
        tokens = data.get("data", [])
        logger.info(f"✅ Successfully fetched {len(tokens)} tokens")
        return tokens
        
    except requests.exceptions.Timeout:
        logger.error("API request timed out")
        return []
    except Exception as e:
        logger.error(f"Error fetching tokens: {e}")
        return []

def send_telegram_message(chat_id, token_data):
    """Send formatted token info to a specific Telegram chat"""
    # Extract token data with fallbacks
    name = token_data.get('name', 'Unknown')
    symbol = token_data.get('symbol', '???')
    address = token_data.get('token_address', 'N/A')
    decimals = token_data.get('decimals', 'N/A')
    supply = token_data.get('total_supply', 'N/A')
    
    # Convert timestamp to readable date
    creation_time = token_data.get('creation_time', 0)
    if creation_time:
        created_date = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S')
    else:
        created_date = 'Unknown'
    
    platform = token_data.get('platform', 'Unknown')
    
    # Format with HTML for better compatibility
    message = f"""
<b>🚀 NEW TOKEN ALERT! 🚀</b>

<b>Token:</b> {name} ({symbol})
<b>Address:</b> <code>{address}</code>
<b>Decimals:</b> {decimals}
<b>Total Supply:</b> {supply}
<b>Created:</b> {created_date}
<b>Platform:</b> {platform}

🔗 <a href="https://solscan.io/token/{address}">View on Solscan</a>
<i>⚠️ Always DYOR - high risk investment</i>
    """
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"✅ Message sent to chat {chat_id}")
            return True
        elif response.status_code == 401:
            logger.error("❌ Invalid Telegram bot token! Check your token from @BotFather")
            return False
        else:
            logger.error(f"Telegram send failed: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending to Telegram: {e}")
        return False

def broadcast_to_all_chats(token_data):
    """Send token alert to all active chats"""
    if not active_chats:
        logger.info("No active chats to send to")
        return
    
    for chat_id in list(active_chats):  # Convert to list to avoid modification issues
        send_telegram_message(chat_id, token_data)

def token_tracking_loop():
    """Background thread that continuously checks for new tokens"""
    global tracking_active, seen_tokens
    
    logger.info("🟢 Token tracking thread started")
    logger.info(f"Platform filter: {PLATFORM_FILTER if PLATFORM_FILTER else 'All platforms'}")
    
    # Initialize seen tokens with current latest
    initial_tokens = fetch_latest_tokens()
    if initial_tokens:
        for token in initial_tokens:
            token_address = token.get('token_address')
            if token_address:
                seen_tokens.add(token_address)
        logger.info(f"Loaded {len(seen_tokens)} existing tokens into memory")
    else:
        logger.warning("Could not load initial tokens - will start fresh")
    
    while tracking_active:
        try:
            # Fetch latest tokens
            latest_tokens = fetch_latest_tokens()
            
            if not latest_tokens:
                logger.warning("No tokens returned from API, retrying in 30 seconds...")
                time.sleep(30)
                continue
            
            # Check for new tokens (API returns newest first)
            new_tokens_found = 0
            for token in latest_tokens:
                token_address = token.get('token_address')
                if token_address and token_address not in seen_tokens:
                    logger.info(f"🎉 NEW TOKEN DETECTED: {token.get('name')} ({token.get('symbol')})")
                    
                    # Broadcast to all active chats
                    broadcast_to_all_chats(token)
                    
                    # Mark as seen
                    seen_tokens.add(token_address)
                    new_tokens_found += 1
            
            if new_tokens_found > 0:
                logger.info(f"Found {new_tokens_found} new token(s)")
            else:
                logger.debug("No new tokens found")
            
            # Wait before next poll
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in tracking loop: {e}")
            time.sleep(POLL_INTERVAL)
    
    logger.info("Token tracking thread stopped")

def handle_telegram_commands():
    """Poll Telegram for commands and handle them"""
    global tracking_active
    
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    logger.info("🟢 Telegram command handler started")
    logger.info("Bot is ready! Add it to Telegram and send /start")
    
    while True:
        try:
            params = {"offset": offset, "timeout": 30}
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    
                    # Handle message
                    if "message" in update:
                        message = update["message"]
                        chat_id = message["chat"]["id"]
                        text = message.get("text", "")
                        username = message.get("from", {}).get("first_name", "User")
                        
                        # Handle /start command
                        if text == "/start":
                            active_chats.add(chat_id)
                            welcome_msg = (
                                f"👋 <b>Welcome {username} to Solana Token Alert Bot!</b>\n\n"
                                f"I will notify this chat whenever a new token is created on Solana.\n\n"
                                f"<b>📊 Current Settings:</b>\n"
                                f"• Platform filter: {PLATFORM_FILTER if PLATFORM_FILTER else 'All platforms'}\n"
                                f"• Check interval: Every {POLL_INTERVAL} seconds\n"
                                f"• Active chats: {len(active_chats)}\n\n"
                                f"<b>📝 Commands:</b>\n"
                                f"/start - Start receiving alerts\n"
                                f"/stop - Stop receiving alerts\n"
                                f"/status - Check bot status\n"
                                f"/stats - View statistics"
                            )
                            
                            url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": welcome_msg,
                                "parse_mode": "HTML"
                            }
                            requests.post(url_msg, json=payload)
                            logger.info(f"User {username} (chat {chat_id}) started the bot")
                        
                        # Handle /stop command
                        elif text == "/stop":
                            active_chats.discard(chat_id)
                            goodbye_msg = "👋 Alerts stopped for this chat. Send /start to resume."
                            url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": goodbye_msg
                            }
                            requests.post(url_msg, json=payload)
                            logger.info(f"Chat {chat_id} stopped receiving alerts")
                        
                        # Handle /status command
                        elif text == "/status":
                            status_msg = (
                                f"<b>🤖 Bot Status</b>\n\n"
                                f"✅ <b>Active chats:</b> {len(active_chats)}\n"
                                f"👀 <b>Tokens tracked:</b> {len(seen_tokens)}\n"
                                f"⏱️ <b>Poll interval:</b> {POLL_INTERVAL}s\n"
                                f"🎯 <b>Platform filter:</b> {PLATFORM_FILTER if PLATFORM_FILTER else 'None'}\n"
                                f"🟢 <b>Bot is:</b> Running\n"
                                f"🔑 <b>API Status:</b> Connected"
                            )
                            url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": status_msg,
                                "parse_mode": "HTML"
                            }
                            requests.post(url_msg, json=payload)
                        
                        # Handle /stats command
                        elif text == "/stats":
                            stats_msg = (
                                f"<b>📊 Statistics</b>\n\n"
                                f"• Total unique tokens found: {len(seen_tokens)}\n"
                                f"• Currently monitoring: {len(active_chats)} chats\n"
                                f"• Tracking since: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                            url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": stats_msg,
                                "parse_mode": "HTML"
                            }
                            requests.post(url_msg, json=payload)
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error in message handler: {e}")
            time.sleep(5)

def main():
    global tracking_active
    
    # Validate configuration
    print("\n" + "="*50)
    print("SOLANA TOKEN ALERT BOT")
    print("="*50)
    
    # Check Solscan API key
    if SOLSCAN_API_KEY == "YOUR_SOLSCAN_PRO_API_KEY_HERE":
        logger.error("❌ Please set your Solscan API key in the script!")
        logger.error("Get it from: https://pro-api.solscan.io/")
        return
    
    # Check Telegram token
    if TELEGRAM_BOT_TOKEN == "8664060489:AAF58vi4mjr0FpBHvBj5A_I19RKW7G6Tv-Q":
        logger.info("✅ Telegram bot token is set")
    else:
        logger.error("❌ Telegram token issue - check your token")
        return
    
    # Test Solscan API connection
    logger.info("Testing Solscan API connection...")
    test_tokens = fetch_latest_tokens()
    if test_tokens is not None:
        logger.info("✅ Solscan API connection successful!")
    else:
        logger.error("❌ Solscan API connection failed!")
        return
    
    print("\n" + "="*50)
    print("BOT STARTING...")
    print("="*50)
    print(f"Platform filter: {PLATFORM_FILTER if PLATFORM_FILTER else 'All platforms'}")
    print(f"Poll interval: {POLL_INTERVAL} seconds")
    print(f"Telegram bot token: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
    print("="*50 + "\n")
    
    # Start token tracking in background thread
    tracking_active = True
    tracking_thread = threading.Thread(target=token_tracking_loop, daemon=True)
    tracking_thread.start()
    
    # Handle Telegram commands in main thread
    try:
        handle_telegram_commands()
    except KeyboardInterrupt:
        print("\n" + "="*50)
        print("SHUTTING DOWN BOT...")
        print("="*50)
        tracking_active = False
        logger.info("Bot stopped by user")

if __name__ == "__main__":
    main()
