"""
zephr.chat — Telegram Bot (aiogram 3.x)
Handles /start, /vip, /help, /report, payments, and admin commands.
Run alongside the FastAPI server.
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
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment, WebAppInfo,
    MenuButtonWebApp, BotCommand, BotCommandScopeDefault,
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Open zephr.chat",
            web_app=WebAppInfo(url=settings.WEBAPP_URL)
        )],
        [
            InlineKeyboardButton(text="👑 Get VIP", callback_data="vip_info"),
            InlineKeyboardButton(text="❓ Help", callback_data="help"),
        ],
    ])


def vip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎁 Start 3-Day Free Trial",
            callback_data="vip_trial"
        )],
        [
            InlineKeyboardButton(text="📅 Monthly — $4.99", callback_data="vip_monthly"),
            InlineKeyboardButton(text="🔥 3 Months — $9.99", callback_data="vip_quarterly"),
        ],
        [InlineKeyboardButton(text="← Back", callback_data="back_main")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(
            db,
            user_id=message.from_user.id,
            first_name=message.from_user.first_name,
            username=message.from_user.username,
            language_code=message.from_user.language_code,
        )
    
        # Handle deep link parameters: /start PARAMETER
        if message.text and len(message.text.split()) > 1:
            param = message.text.split()[1]
    
        # NEW: Handle VIP deep link from web app
        if param == "vip":
            await cmd_vip(message)  # Show VIP options immediately
            return
    
        # Handle referral links
        if param.startswith("ref_") and not user.referred_by:
            await _handle_referral(db, user, param[4:])
        
        # Handle referral from deep link: /start ref_XXXXX
        if message.text and len(message.text.split()) > 1:
            ref_code = message.text.split()[1]
            if ref_code.startswith("ref_") and not user.referred_by:
                await _handle_referral(db, user, ref_code[4:])

    name = message.from_user.first_name or "there"
    await message.answer(
        f"👋 Hey <b>{name}</b>! Welcome to <b>zephr.chat</b>\n\n"
        f"Faster, safer, and more private than any other Telegram stranger chat.\n\n"
        f"🔒 <b>Zero logs</b> · Chats auto-delete · AI blocks creeps instantly\n\n"
        f"Tap the button below to start chatting anonymously 👇",
        reply_markup=main_keyboard()
    )


async def _handle_referral(db: AsyncSession, new_user: User, referrer_code: str):
    from sqlalchemy import select, update
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
        "• Monthly: <b>$4.99/month</b>\n"
        "• 3 Months: <b>$9.99</b> (save 33%)\n\n"
        "🎁 <b>First 3 days FREE</b> — cancel anytime"
    )
    kb = vip_keyboard()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


# ── VIP Payment Flow ──────────────────────────────────────────────────────────

@router.callback_query(F.data.in_(["vip_trial", "vip_monthly", "vip_quarterly"]))
async def vip_payment(callback: CallbackQuery):
    plan = callback.data.replace("vip_", "")
    
    # Handle free trial directly without payment
    if plan == "trial":
        async with AsyncSessionLocal() as db:
            user = await get_or_create_user(
                db, 
                user_id=callback.from_user.id,
                first_name=callback.from_user.first_name,
                username=callback.from_user.username
            )
            
            # Check if already had trial
            if user.had_vip_trial:
                await callback.answer("⚠️ You've already used your free trial!", show_alert=True)
                return
            
            # Grant 3-day trial
            new_expiry = datetime.utcnow() + timedelta(days=3)
            
            await db.execute(
                update(User).where(User.id == user.id).values(
                    is_vip=True,
                    vip_expires_at=new_expiry,
                    had_vip_trial=True
                )
            )
            await db.commit()
            
            await callback.message.answer(
                "🎉 <b>Free Trial Activated!</b>\n\n"
                "You now have VIP access for 3 days:\n"
                "• ⚡ Priority matching queue\n"
                "• 🌍 Gender & country filters\n"
                "• 🌐 Auto-translate messages\n"
                "• 🎨 Custom themes\n\n"
                f"Expires: {new_expiry.strftime('%B %d, %Y')}"
            )
            await callback.answer()
            return
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PAID PLANS - TELEGRAM STARS (RECOMMENDED METHOD)
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Pricing: 1 Star ≈ $0.025 (2.5 cents)
    # Monthly $4.99 ≈ 200 Stars
    # Quarterly $9.99 ≈ 400 Stars
    prices_map = {
        "monthly": (200, "VIP Monthly", "One month of VIP access"),
        "quarterly": (400, "VIP 3 Months", "Three months of VIP - Save 33%!"),
    }
    
    if plan not in prices_map:
        await callback.answer("Invalid plan selected", show_alert=True)
        return
    
    stars, title, description = prices_map[plan]
    prices = [LabeledPrice(label=title, amount=stars)]
    
    # Generate unique payload to track this payment
    payload = f"vip_{plan}_{callback.from_user.id}_{secrets.token_hex(4)}"
    
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Empty string for Telegram Stars!
            currency="XTR",     # XTR = Telegram Stars currency code
            prices=prices,
            start_parameter=f"vip_{plan}",
        )
        await callback.answer("💳 Payment invoice sent to you!")
        log.info(f"Sent payment invoice to user {callback.from_user.id} for {plan}")
    except Exception as e:
        log.error(f"Failed to send invoice: {e}")
        await callback.answer("❌ Failed to create payment. Please try again.", show_alert=True)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ALTERNATIVE: STRIPE/TRADITIONAL PAYMENT PROCESSOR
    # Uncomment this section and comment out the Telegram Stars section above
    # if you want to use Stripe or another traditional payment processor
    # ═══════════════════════════════════════════════════════════════════════════
    # 
    # # Requires STRIPE_PROVIDER_TOKEN in settings
    # prices_map = {
    #     "monthly": (499, "VIP Monthly", "One month of VIP access"),
    #     "quarterly": (999, "VIP 3 Months", "Three months of VIP - Save 33%!"),
    # }
    #
    # if plan not in prices_map:
    #     await callback.answer("Invalid plan selected", show_alert=True)
    #     return
    #
    # cents, title, description = prices_map[plan]
    # prices = [LabeledPrice(label=title, amount=cents)]
    #
    # payload = f"vip_{plan}_{callback.from_user.id}_{secrets.token_hex(4)}"
    #
    # try:
    #     await bot.send_invoice(
    #         chat_id=callback.from_user.id,
    #         title=title,
    #         description=description,
    #         payload=payload,
    #         provider_token=settings.STRIPE_PROVIDER_TOKEN,  # Your Stripe token
    #         currency="USD",     # USD, EUR, GBP, etc.
    #         prices=prices,
    #         start_parameter=f"vip_{plan}",
    #         # Optional: Add tips support
    #         # max_tip_amount=1000,
    #         # suggested_tip_amounts=[100, 200, 500],
    #     )
    #     await callback.answer("💳 Payment invoice sent to you!")
    #     log.info(f"Sent payment invoice to user {callback.from_user.id} for {plan}")
    # except Exception as e:
    #     log.error(f"Failed to send invoice: {e}")
    #     await callback.answer("❌ Failed to create payment. Please try again.", show_alert=True)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    """
    Always approve pre-checkout — validation is in successful_payment.
    This is called before payment is actually charged.
    """
    await query.answer(ok=True)
    log.info(f"Pre-checkout approved for user {query.from_user.id}")


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    """
    Handle successful payment and grant VIP access.
    This is called after payment is completed and charged.
    """
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload  # Format: "vip_monthly_123456_abc123"
    
    log.info(f"Payment successful: {payload}")
    
    # Extract plan from payload
    parts = payload.split("_")
    if len(parts) < 2:
        log.error(f"Invalid payload format: {payload}")
        return
        
    plan = parts[1]  # "monthly" or "quarterly"

    # Map plan to days
    days_map = {"monthly": 31, "quarterly": 92}
    days = days_map.get(plan, 31)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()

        if user:
            # Calculate new expiry (extend if already VIP)
            new_expiry = max(
                user.vip_expires_at or datetime.utcnow(),
                datetime.utcnow()
            ) + timedelta(days=days)

            # Grant VIP
            await db.execute(
                update(User).where(User.id == user.id).values(
                    is_vip=True,
                    vip_expires_at=new_expiry
                )
            )

            # Record payment in database
            vp = VIPPayment(
                user_id=user.id,
                telegram_charge_id=payment.telegram_payment_charge_id,
                provider_charge_id=payment.provider_payment_charge_id or "",
                amount=payment.total_amount,
                currency=payment.currency,
                plan=plan,
            )
            db.add(vp)
            await db.commit()
            
            log.info(f"VIP granted to user {user.id} until {new_expiry}")

    await message.answer(
        f"🎉 <b>Welcome to VIP!</b>\n\n"
        f"Your {plan} plan is now active until "
        f"<b>{new_expiry.strftime('%B %d, %Y')}</b>.\n\n"
        f"Enjoy:\n"
        f"• ⚡ Priority matching queue\n"
        f"• 🎯 Gender & country filters\n"
        f"• 🌐 Auto-translate messages\n"
        f"• 🏷 Username reveal option\n"
        f"• 🚫 No ads\n\n"
        f"Thanks for supporting zephr.chat! 👑"
    )


# ── Back Button ───────────────────────────────────────────────────────────────

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
                                        text="📅 Monthly — $4.99",
                                        callback_data="vip_monthly"
                                    ),
                                    InlineKeyboardButton(
                                        text="🔥 3 Months — $9.99",
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
