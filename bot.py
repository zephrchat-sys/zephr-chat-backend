"""
zephr.chat — Telegram Bot (aiogram 3.x)
Handles /start, /vip, /help, /report, payments, and admin commands.
Run alongside the FastAPI server.

UPDATED: Razorpay multi-currency payment integration via web checkout
"""
import asyncio
import logging
import secrets
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp, BotCommand, BotCommandScopeDefault,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from config import settings
from database import AsyncSessionLocal, User, VIPPayment, get_or_create_user

log = logging.getLogger("zephr.bot")

bot = None if settings.BOT_TOKEN == 'dev' else Bot(
    token=settings.BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ── Helpers ───────────────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard with all key actions"""
    return InlineKeyboardMarkup(inline_keyboard=[
        # Row 1: Find stranger (primary action)
        [InlineKeyboardButton(
            text="🔍 Find a stranger",
            web_app=WebAppInfo(url=settings.WEBAPP_URL)
        )],
        # Row 2: Choose topic and Stop chat (coming soon)
        [
            InlineKeyboardButton(text="📌 Choose topic", callback_data="choose_topic"),
            InlineKeyboardButton(text="🛑 Stop chat", callback_data="stop_chat"),
        ],
        # Row 3: VIP and Invite friends
        [
            InlineKeyboardButton(text="👑 VIP", callback_data="vip_info"),
            InlineKeyboardButton(text="🎁 Invite friends", callback_data="invite_friends"),
        ],
        # Row 4: Update profile
        [InlineKeyboardButton(
            text="👤 Update profile",
            web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}#profile")
        )],
    ])


def vip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎁 Start 3-Day Free Trial",
            callback_data="vip_trial"
        )],
        [
            InlineKeyboardButton(text="📅 Monthly — ₹415/$4.99", callback_data="vip_monthly"),
            InlineKeyboardButton(text="🔥 3 Months — ₹830/$9.99", callback_data="vip_quarterly"),
        ],
        [InlineKeyboardButton(text="← Back", callback_data="back_main")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    """
    Handle /start command with deep link support
    Supports: referrals, VIP links, payment success callbacks
    """
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(
            db,
            user_id=message.from_user.id,
            first_name=message.from_user.first_name,
            username=message.from_user.username,
            language_code=message.from_user.language_code,
        )
    
        # Handle deep link parameters: /start PARAMETER
        param = None
        if message.text and len(message.text.split()) > 1:
            param = message.text.split()[1]
        
        # ─────────────────────────────────────────────────────────────────────────
        # HANDLE PAYMENT SUCCESS RETURN FROM RAZORPAY
        # ─────────────────────────────────────────────────────────────────────────
        if param == "payment_success":
            # User returned from successful Razorpay payment
            result = await db.execute(
                select(User).where(User.id == message.from_user.id)
            )
            user = result.scalar_one_or_none()
            
            if user and user.is_vip:
                expiry_date = user.vip_expires_at.strftime('%B %d, %Y') if user.vip_expires_at else "Unknown"
                
                await message.answer(
                    "🎉 <b>Payment Successful!</b>\n\n"
                    "Your VIP subscription is now active!\n\n"
                    "<b>VIP Benefits:</b>\n"
                    "• ⚡ Priority matching (< 0.5 sec)\n"
                    "• 🎯 Gender & country filters\n"
                    "• 🌐 Auto-translate messages\n"
                    "• 🏷 Username reveal option\n"
                    "• 🚫 Ad-free experience\n\n"
                    f"✨ <b>Active until:</b> {expiry_date}\n\n"
                    "Thank you for supporting zephr.chat! 👑",
                    reply_markup=main_keyboard()
                )
            else:
                await message.answer(
                    "⚠️ <b>Payment Processing</b>\n\n"
                    "Your payment is being processed. VIP access will be activated shortly.\n\n"
                    "If you don't receive VIP within 5 minutes, please contact support.",
                    reply_markup=main_keyboard()
                )
            return
        
        # ─────────────────────────────────────────────────────────────────────────
        # HANDLE VIP DEEP LINK FROM WEB APP
        # ─────────────────────────────────────────────────────────────────────────
        if param and param.startswith("vip"):
            await cmd_vip(message)
            return
        
        # ─────────────────────────────────────────────────────────────────────────
        # HANDLE REFERRAL LINKS
        # ─────────────────────────────────────────────────────────────────────────
        if param and param.startswith("ref_") and not user.referred_by:
            await _handle_referral(db, user, param[4:])

    # Default welcome message
    name = message.from_user.first_name or "there"
    await message.answer(
        f"👋 Hey <b>{name}</b>! Welcome to <b>zephr.chat</b>\n\n"
        f"🚀 The fastest anonymous stranger chat on Telegram\n\n"
        f"✨ <b>What makes us special:</b>\n"
        f"• ⚡ Instant matching in &lt;0.5 sec\n"
        f"• 🔒 Zero logs - chats auto-delete\n"
        f"• 🛡 AI blocks creeps instantly\n"
        f"• 🌐 Talk to anyone worldwide\n\n"
        f"Choose an option below to get started! 👇",
        reply_markup=main_keyboard()
    )


async def _handle_referral(db: AsyncSession, new_user: User, referrer_code: str):
    """Handle referral code and grant bonus VIP to referrer"""
    result = await db.execute(select(User).where(User.referral_code == referrer_code))
    referrer = result.scalar_one_or_none()

    if referrer and referrer.id != new_user.id:
        new_user.referred_by = referrer.id
        await db.execute(
            update(User).where(User.id == referrer.id).values(
                referral_count=User.referral_count + 1
            )
        )

        # Check referral milestone (3 refs = 7 days VIP)
        result2 = await db.execute(
            select(User.referral_count).where(User.id == referrer.id)
        )
        count = result2.scalar() or 0

        if count > 0 and count % 3 == 0:
            new_vip_expiry = max(
                referrer.vip_expires_at or datetime.utcnow(),
                datetime.utcnow()
            ) + timedelta(days=7)
            await db.execute(
                update(User).where(User.id == referrer.id).values(
                    is_vip=True,
                    vip_expires_at=new_vip_expiry
                )
            )
            try:
                await bot.send_message(
                    referrer.id,
                    "🎉 <b>VIP Unlocked!</b>\n\nA friend joined via your link. "
                    "You've earned <b>7 days of VIP</b>! Enjoy priority matching, "
                    "gender & country filters, and more.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        await db.commit()


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
@router.callback_query(F.data == "help")
async def cmd_help(event: Message | CallbackQuery):
    text = (
        "📖 <b>zephr.chat Help</b>\n\n"
        "• <b>/start</b> — Open the chat app\n"
        "• <b>/vip</b> — View VIP plans\n"
        "• <b>/referral</b> — Get your invite link\n"
        "• <b>/stats</b> — Live stats\n"
        "• <b>/help</b> — This message\n\n"
        "🛡 <b>Safety</b>\n"
        "All chats are anonymous and auto-deleted.\n"
        "AI moderation blocks toxic content.\n"
        "Use the Report button in-chat for violations.\n\n"
        "🔒 <b>Privacy</b>\n"
        "We store only your Telegram ID and preferences.\n"
        "Zero chat history. Open-source matching engine."
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Back", callback_data="back_main")]
        ]))
        await event.answer()
    else:
        await event.answer(text)


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    from matching import engine as match_engine
    stats = await match_engine.get_stats()
    await message.answer(
        f"📊 <b>zephr.chat Live Stats</b>\n\n"
        f"👥 Online now: <b>{stats['online']:,}</b>\n"
        f"💬 Active chats: <b>{stats['active_chats']:,}</b>\n"
        f"🔗 Total matches: <b>{stats['total_matches']:,}</b>\n"
        f"⏳ In queue: <b>{stats['queued']}</b>"
    )


# ── /referral ─────────────────────────────────────────────────────────────────

@router.message(Command("referral"))
async def cmd_referral(message: Message):
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, message.from_user.id)
        link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user.referral_code}"
        await message.answer(
            f"🎁 <b>Invite Friends, Earn VIP</b>\n\n"
            f"Share your link:\n<code>{link}</code>\n\n"
            f"👥 Friends referred: <b>{user.referral_count}</b>\n"
            f"🏆 Every <b>3 friends</b> = <b>7 days free VIP</b>\n\n"
            f"Your friends get matched instantly when they join!"
        )


# ── /vip ──────────────────────────────────────────────────────────────────────

@router.message(Command("vip"))
@router.callback_query(F.data == "vip_info")
async def cmd_vip(event: Message | CallbackQuery):
    text = (
        "👑 <b>zephr VIP</b>\n\n"
        "Unlock the full experience:\n\n"
        "🎯 <b>Gender & Country Filter</b> — Match who you want\n"
        "⚡ <b>Priority Queue</b> — Skip the wait, match in &lt;0.5s\n"
        "🌐 <b>Auto-Translate</b> — Talk to anyone, any language\n"
        "🏷 <b>Username Reveal</b> — Share @handle (with consent)\n"
        "🚫 <b>No Ads</b> — Pure chat experience\n\n"
        "💰 <b>Pricing</b>\n"
        "• India: ₹415/month or ₹830/3 months\n"
        "• International: $4.99/month or $9.99/3 months\n"
        "• 100+ currencies supported!\n\n"
        "🎁 <b>First 3 days FREE</b> — cancel anytime"
    )
    kb = vip_keyboard()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


# ══════════════════════════════════════════════════════════════════════════════
# VIP Payment Flow - RAZORPAY WEB CHECKOUT
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.in_(["vip_trial", "vip_monthly", "vip_quarterly"]))
async def vip_payment(callback: CallbackQuery):
    """
    Handle VIP payment selection
    - Free trial: Activate immediately
    - Paid plans: Redirect to Razorpay web checkout with multi-currency support
    """
    plan = callback.data.replace("vip_", "")
    
    # ══════════════════════════════════════════════════════════════════════════
    # HANDLE FREE TRIAL
    # ══════════════════════════════════════════════════════════════════════════
    if plan == "trial":
        try:
            async with AsyncSessionLocal() as db:
                # Get or create user
                user = await get_or_create_user(
                    db, 
                    user_id=callback.from_user.id,
                    first_name=callback.from_user.first_name,
                    username=callback.from_user.username
                )
                
                # Refresh user object to ensure we have latest data from DB
                await db.refresh(user)
                
                # Check if already had trial
                if user.had_vip_trial:
                    await callback.answer("⚠️ You've already used your free trial!", show_alert=True)
                    log.info(f"User {user.id} attempted to claim trial again (already used)")
                    return
                
                # Grant 3-day trial
                new_expiry = datetime.utcnow() + timedelta(days=3)
                
                # Update user object directly instead of using update query
                user.is_vip = True
                user.vip_expires_at = new_expiry
                user.had_vip_trial = True
                
                # Commit changes
                await db.commit()
                await db.refresh(user)
                
                log.info(f"✅ VIP trial activated for user {user.id} (@{user.username}). Expires: {new_expiry}")
                
                await callback.message.answer(
                    "🎉 <b>Free Trial Activated!</b>\n\n"
                    "You now have VIP access for 3 days:\n"
                    "• ⚡ Priority matching queue\n"
                    "• 🌍 Gender & country filters\n"
                    "• 🌐 Auto-translate messages\n"
                    "• 🏷 Username reveal option\n"
                    "• 🚫 No ads\n\n"
                    f"Expires: {new_expiry.strftime('%B %d, %Y')}"
                )
                await callback.answer()
                return
        except Exception as e:
            log.error(f"❌ Failed to activate VIP trial for user {callback.from_user.id}: {e}")
            await callback.answer("❌ Failed to activate trial. Please try again later.", show_alert=True)
            return
    
    # ══════════════════════════════════════════════════════════════════════════
    # HANDLE PAID PLANS - RAZORPAY WEB CHECKOUT
    # ══════════════════════════════════════════════════════════════════════════
    
    # Plan details for display
    plan_details = {
        "monthly": {
            "name": "Monthly VIP Plan",
            "price_inr": "₹415",
            "price_usd": "$4.99",
            "duration": "1 month",
            "emoji": "📅"
        },
        "quarterly": {
            "name": "3 Months VIP Plan",
            "price_inr": "₹830",
            "price_usd": "$9.99",
            "duration": "3 months",
            "badge": "🔥 Save 33%",
            "emoji": "🔥"
        }
    }
    
    details = plan_details.get(plan, plan_details["monthly"])
    
    # Generate checkout URL with user parameters
    checkout_url = (
        f"{settings.WEBAPP_URL}/checkout.html?"
        f"plan={plan}&"
        f"user_id={callback.from_user.id}&"
        f"first_name={callback.from_user.first_name or 'User'}"
    )
    
    # Create inline button to open web checkout
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Proceed to Payment",
            url=checkout_url
        )],
        [InlineKeyboardButton(
            text="← Back to Plans",
            callback_data="vip_info"
        )]
    ])
    
    # Message text
    message_text = (
        f"{details['emoji']} <b>{details['name']}</b>\n\n"
        f"💰 <b>Price:</b>\n"
        f"• 🇮🇳 India: {details['price_inr']}\n"
        f"• 🌍 International: {details['price_usd']}\n"
        f"• Duration: {details['duration']}\n"
    )
    
    if "badge" in details:
        message_text += f"\n{details['badge']}\n"
    
    message_text += (
        f"\n<b>💳 Payment Methods:</b>\n"
        f"🇮🇳 <b>India:</b> UPI • Cards • Net Banking • Wallets\n"
        f"🌍 <b>Global:</b> Credit/Debit Cards (100+ currencies)\n\n"
        f"✅ Secure payment powered by Razorpay\n"
        f"🔒 256-bit encryption • PCI DSS certified\n"
        f"💯 100% money-back guarantee\n\n"
        f"<i>Click below to choose your currency and pay:</i>"
    )
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard
    )
    await callback.answer()


# ── Back Button ───────────────────────────────────────────────────────────────

# ── Additional Menu Callbacks ────────────────────────────────────────────────

@router.callback_query(F.data == "choose_topic")
async def choose_topic_callback(callback: CallbackQuery):
    """Handle Choose Topic button - directs to web app with topic selector"""
    await callback.answer(
        "🔍 Opening topic selector...",
        show_alert=False
    )
    # The web app will handle topic selection
    await callback.message.answer(
        "📌 <b>Choose Your Chat Topic</b>\n\n"
        "Select a topic to match with people who share your interests!\n\n"
        "Tap the button below to open topic selection 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔍 Choose Topic & Start",
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}#topics")
            )],
            [InlineKeyboardButton(text="← Back", callback_data="back_main")]
        ])
    )


@router.callback_query(F.data == "stop_chat")
async def stop_chat_callback(callback: CallbackQuery):
    """Handle Stop Chat button"""
    await callback.answer(
        "🛑 To stop a chat, use the 'Next' button in the chat screen",
        show_alert=True
    )


@router.callback_query(F.data == "invite_friends")
async def invite_friends_callback(callback: CallbackQuery):
    """Handle Invite Friends button - shows referral link"""
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, callback.from_user.id)
        bot_username = (await bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=ref_{user.referral_code}"
        
        await callback.message.edit_text(
            f"🎁 <b>Invite Friends, Earn VIP</b>\n\n"
            f"Share your personal invite link:\n"
            f"<code>{link}</code>\n\n"
            f"👥 Friends referred: <b>{user.referral_count}</b>\n"
            f"🏆 Rewards:\n"
            f"• Every <b>3 friends</b> = <b>7 days FREE VIP</b>\n"
            f"• Every <b>10 friends</b> = <b>30 days FREE VIP</b>\n\n"
            f"💡 Your friends get instant matching when they join!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📤 Share Link",
                    url=f"https://t.me/share/url?url={link}&text=Join me on zephr.chat - anonymous stranger chat that's fast, safe, and private!"
                )],
                [InlineKeyboardButton(text="← Back", callback_data="back_main")]
            ])
        )
        await callback.answer()


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Welcome back! Tap below to open zephr.chat 👇",
        reply_markup=main_keyboard()
    )
    await callback.answer()


# ── VIP Auto-Renewal Reminders ───────────────────────────────────────────────

async def check_vip_renewals():
    """
    Background task to check for expiring VIP subscriptions and send reminders.
    Runs every 6 hours.
    """
    while True:
        try:
            log.info("Checking for VIP renewals...")
            
            async with AsyncSessionLocal() as db:
                # Find users expiring in 3 days
                three_days_from_now = datetime.utcnow() + timedelta(days=3)
                three_days_end = three_days_from_now + timedelta(hours=6)
                
                result = await db.execute(
                    select(User).where(
                        User.is_vip == True,
                        User.vip_expires_at >= three_days_from_now,
                        User.vip_expires_at < three_days_end
                    )
                )
                users_3day = result.scalars().all()
                
                # Find users expiring today
                now = datetime.utcnow()
                end_of_check = now + timedelta(hours=6)
                
                result = await db.execute(
                    select(User).where(
                        User.is_vip == True,
                        User.vip_expires_at >= now,
                        User.vip_expires_at < end_of_check
                    )
                )
                users_today = result.scalars().all()
                
                # Send 3-day reminders
                for user in users_3day:
                    try:
                        await bot.send_message(
                            user.id,
                            "⚠️ <b>VIP Expiring Soon</b>\n\n"
                            f"Your VIP access expires in <b>3 days</b> "
                            f"({user.vip_expires_at.strftime('%B %d, %Y')}).\n\n"
                            "Renew now to keep enjoying:\n"
                            "• ⚡ Priority matching\n"
                            "• 🎯 Gender & country filters\n"
                            "• 🌐 Auto-translate\n\n"
                            "Tap below to renew 👇",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(
                                    text="🔄 Renew VIP",
                                    callback_data="vip_info"
                                )]
                            ])
                        )
                        log.info(f"Sent 3-day reminder to user {user.id}")
                    except Exception as e:
                        log.error(f"Failed to send 3-day reminder to {user.id}: {e}")
                
                # Send expiry day reminders
                for user in users_today:
                    try:
                        await bot.send_message(
                            user.id,
                            "⏰ <b>VIP Expiring Today!</b>\n\n"
                            f"Your VIP access expires <b>today</b> "
                            f"({user.vip_expires_at.strftime('%B %d at %I:%M %p')}).\n\n"
                            "Don't lose access to:\n"
                            "• ⚡ Priority matching\n"
                            "• 🎯 Advanced filters\n"
                            "• 🌐 Auto-translate\n\n"
                            "Renew now with one tap! 👇",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="📅 Monthly — ₹415/$4.99",
                                        callback_data="vip_monthly"
                                    ),
                                    InlineKeyboardButton(
                                        text="🔥 3 Months — ₹830/$9.99",
                                        callback_data="vip_quarterly"
                                    ),
                                ]
                            ])
                        )
                        log.info(f"Sent expiry reminder to user {user.id}")
                    except Exception as e:
                        log.error(f"Failed to send expiry reminder to {user.id}: {e}")
                
                log.info(f"Renewal check complete: {len(users_3day)} 3-day, {len(users_today)} expiring today")
                
        except Exception as e:
            log.error(f"Error in renewal checker: {e}")
        
        # Wait 6 hours before next check
        await asyncio.sleep(6 * 60 * 60)


async def check_expired_vip():
    """
    Background task to disable VIP for expired users.
    Runs every hour.
    """
    while True:
        try:
            log.info("Checking for expired VIP...")
            
            async with AsyncSessionLocal() as db:
                # Find users with expired VIP
                now = datetime.utcnow()
                
                result = await db.execute(
                    select(User).where(
                        User.is_vip == True,
                        User.vip_expires_at < now
                    )
                )
                expired_users = result.scalars().all()
                
                # Disable VIP for expired users
                for user in expired_users:
                    await db.execute(
                        update(User).where(User.id == user.id).values(
                            is_vip=False
                        )
                    )
                    log.info(f"Disabled VIP for expired user {user.id}")
                
                if expired_users:
                    await db.commit()
                    log.info(f"Disabled VIP for {len(expired_users)} expired users")
                
        except Exception as e:
            log.error(f"Error in expiry checker: {e}")
        
        # Wait 1 hour before next check
        await asyncio.sleep(60 * 60)


# ── Bot Startup ───────────────────────────────────────────────────────────────

async def setup_bot():
    """Set bot commands and menu button."""
    await bot.set_my_commands([
        BotCommand(command="start", description="Open zephr.chat"),
        BotCommand(command="vip", description="👑 VIP plans & pricing"),
        BotCommand(command="referral", description="🎁 Invite friends, earn VIP"),
        BotCommand(command="stats", description="📊 Live stats"),
        BotCommand(command="help", description="❓ Help & FAQ"),
    ], scope=BotCommandScopeDefault())

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open zephr.chat",
            web_app=WebAppInfo(url=settings.WEBAPP_URL)
        )
    )
    
    # Start background renewal and expiry checker tasks
    asyncio.create_task(check_vip_renewals())
    asyncio.create_task(check_expired_vip())
    
    log.info("✅ Bot commands and menu button set")
    log.info("✅ Background renewal tasks started")
    log.info("✅ Razorpay web checkout payment flow active")


async def run_bot():
    if bot is None:
        print("⚠️  BOT_TOKEN=dev — bot disabled, running FastAPI only")
        return
    await setup_bot()
    if settings.WEBHOOK_URL:
        await bot.set_webhook(
            url=f"{settings.WEBHOOK_URL}{settings.WEBHOOK_PATH}",
            drop_pending_updates=True,
        )
    else:
        await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())
