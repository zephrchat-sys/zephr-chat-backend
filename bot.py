"""
zephr.chat — Telegram Bot (aiogram 3.x)
Handles /start, /vip, /help, /report, payments, and admin commands.
Run alongside the FastAPI server.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment, WebAppInfo,
    MenuButtonWebApp, BotCommand, BotCommandScopeDefault,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal, User, VIPPayment, get_or_create_user

log = logging.getLogger("zephr.bot")

bot = None if settings.BOT_TOKEN == 'dev' else Bot(token=settings.BOT_TOKEN, parse_mode=ParseMode.HTML)
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

    prices_map = {
        "trial": (1, "3-Day VIP Trial"),        # $0.01 placeholder for trial
        "monthly": (499, "VIP Monthly"),
        "quarterly": (999, "VIP 3 Months"),
    }
    amount, label = prices_map[plan]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"zephr.chat {label}",
        description="Unlock priority queue, gender/country filters, auto-translate, and more.",
        payload=f"vip_{plan}_{callback.from_user.id}",
        currency="USD",
        prices=[LabeledPrice(label=label, amount=amount)],
        provider_token="",  # Empty = Telegram Stars payment
        start_parameter="vip",
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    """Always approve pre-checkout — validation is in successful_payment."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload  # "vip_monthly_123456"

    parts = payload.split("_")
    plan = parts[1]  # monthly | quarterly | trial

    days_map = {"trial": 3, "monthly": 31, "quarterly": 92}
    days = days_map.get(plan, 31)

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select, update
        result = await db.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()

        if user:
            new_expiry = max(
                user.vip_expires_at or datetime.utcnow(),
                datetime.utcnow()
            ) + timedelta(days=days)

            await db.execute(
                update(User).where(User.id == user.id).values(
                    is_vip=True,
                    vip_expires_at=new_expiry
                )
            )

            # Record payment
            vp = VIPPayment(
                user_id=user.id,
                telegram_charge_id=payment.telegram_payment_charge_id,
                provider_charge_id=payment.provider_payment_charge_id,
                amount=payment.total_amount,
                currency=payment.currency,
                plan=plan,
            )
            db.add(vp)
            await db.commit()

    await message.answer(
        f"🎉 <b>Welcome to VIP!</b>\n\n"
        f"Your {plan} plan is now active until "
        f"<b>{(datetime.utcnow() + timedelta(days=days)).strftime('%b %d, %Y')}</b>.\n\n"
        f"Enjoy priority matching, exclusive filters, and more! 👑"
    )


# ── Back Button ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Welcome back! Tap below to open zephr.chat 👇",
        reply_markup=main_keyboard()
    )
    await callback.answer()


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
    log.info("✅ Bot commands and menu button set")


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
