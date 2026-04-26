"""
Razorpay Payment Integration Routes
Handles order creation, payment verification, and webhooks
"""
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
import razorpay
from typing import Optional

from config import settings
from database import AsyncSessionLocal, User, VIPPayment
from sqlalchemy import select, update
import logging

log = logging.getLogger("zephr.razorpay")

router = APIRouter()

# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


# ── Request/Response Models ───────────────────────────────────────────────────

class OrderRequest(BaseModel):
    amount: int         # Amount in smallest currency unit (paise/cents)
    currency: str       # INR, USD, EUR, GBP, etc.
    plan: str          # 'monthly' or 'quarterly'
    user_id: int       # Telegram user ID


class PaymentVerification(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    user_id: int
    plan: str


# ── API Endpoints ─────────────────────────────────────────────────────────────

@router.post("/api/create-order")
async def create_order(request: OrderRequest):
    """
    Create Razorpay order for payment
    Supports multiple currencies
    """
    try:
        # Validate currency
        supported_currencies = [
            'INR', 'USD', 'EUR', 'GBP', 'AUD', 'CAD', 
            'SGD', 'AED', 'SAR', 'JPY', 'CNY', 'MYR',
            'THB', 'IDR', 'PHP', 'VND', 'ZAR', 'KES', 'NGN'
        ]
        
        if request.currency not in supported_currencies:
            raise HTTPException(
                status_code=400, 
                detail=f"Currency {request.currency} not supported"
            )
        
        # Validate plan
        if request.plan not in ['monthly', 'quarterly']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plan: {request.plan}"
            )
        
        # Generate unique receipt ID (max 40 chars for Razorpay)
        timestamp = int(datetime.utcnow().timestamp())
        plan_code = 'm' if request.plan == 'monthly' else 'q'
        receipt_id = f"{request.user_id}_{plan_code}_{timestamp}_{secrets.token_hex(3)}"
        
        # Create order data
        order_data = {
            "amount": request.amount,
            "currency": request.currency,
            "receipt": receipt_id,
            "notes": {
                "user_id": str(request.user_id),
                "plan": request.plan,
                "app": "zephr_chat"
            }
        }
        
        # Create order in Razorpay
        order = razorpay_client.order.create(data=order_data)
        
        log.info(f"Created order {order['id']} for user {request.user_id}, plan {request.plan}, {request.currency} {request.amount}")
        
        return {
            "success": True,
            "id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "receipt": receipt_id
        }
    
    except razorpay.errors.BadRequestError as e:
        log.error(f"Razorpay Bad Request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Failed to create order: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create order: {str(e)}")


@router.post("/api/verify-payment")
async def verify_payment(verification: PaymentVerification):
    """
    Verify payment signature and grant VIP access
    This is called after user completes payment
    """
    try:
        # Verify payment signature
        params_dict = {
            'razorpay_order_id': verification.razorpay_order_id,
            'razorpay_payment_id': verification.razorpay_payment_id,
            'razorpay_signature': verification.razorpay_signature
        }
        
        # This will raise SignatureVerificationError if signature is invalid
        razorpay_client.utility.verify_payment_signature(params_dict)
        
        # Fetch payment details from Razorpay
        payment = razorpay_client.payment.fetch(verification.razorpay_payment_id)
        
        # Verify payment status
        if payment['status'] != 'captured':
            raise HTTPException(
                status_code=400, 
                detail=f"Payment not captured. Status: {payment['status']}"
            )
        
        # Grant VIP access to user
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.id == verification.user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Calculate VIP expiry date
            days_map = {"monthly": 31, "quarterly": 92}
            days = days_map.get(verification.plan, 31)
            
            # Extend VIP if user already has it, otherwise start from now
            new_expiry = max(
                user.vip_expires_at or datetime.utcnow(),
                datetime.utcnow()
            ) + timedelta(days=days)
            
            # Update user VIP status
            await db.execute(
                update(User).where(User.id == user.id).values(
                    is_vip=True,
                    vip_expires_at=new_expiry
                )
            )
            
            # Record payment in database
            vip_payment = VIPPayment(
                user_id=user.id,
                telegram_charge_id="",  # Not applicable for Razorpay
                provider_charge_id=verification.razorpay_payment_id,
                amount=payment["amount"],
                currency=payment["currency"],
                plan=verification.plan,
            )
            db.add(vip_payment)
            
            await db.commit()
            
            log.info(f"VIP granted to user {user.id} until {new_expiry}. Payment: {verification.razorpay_payment_id}")
        
        return {
            "success": True, 
            "message": "VIP activated successfully",
            "vip_expires_at": new_expiry.isoformat(),
            "plan": verification.plan
        }
    
    except razorpay.errors.SignatureVerificationError:
        log.error(f"Invalid payment signature for payment {verification.razorpay_payment_id}")
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    except razorpay.errors.BadRequestError as e:
        log.error(f"Razorpay Bad Request during verification: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Payment verification failed: {e}")
        raise HTTPException(status_code=500, detail=f"Payment verification failed: {str(e)}")


@router.post("/api/razorpay-webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None)
):
    """
    Handle Razorpay webhooks for payment events
    This provides automatic payment confirmation
    """
    try:
        # Get raw body
        body = await request.body()
        
        # Verify webhook signature
        expected_signature = hmac.new(
            settings.RAZORPAY_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        
        if not x_razorpay_signature:
            log.warning("Webhook received without signature")
            raise HTTPException(status_code=400, detail="Missing webhook signature")
        
        if x_razorpay_signature != expected_signature:
            log.warning("Webhook signature mismatch")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        
        # Parse webhook event
        event = await request.json()
        event_type = event.get("event")
        
        log.info(f"Received webhook event: {event_type}")
        
        # Handle different event types
        if event_type == "payment.captured":
            # Payment successful
            payment_data = event["payload"]["payment"]["entity"]
            
            # Extract user info from notes
            user_id = int(payment_data.get("notes", {}).get("user_id", 0))
            plan = payment_data.get("notes", {}).get("plan", "monthly")
            
            if user_id > 0:
                # Grant VIP access (similar to verify_payment)
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(User).where(User.id == user_id)
                    )
                    user = result.scalar_one_or_none()
                    
                    if user:
                        days_map = {"monthly": 31, "quarterly": 92}
                        days = days_map.get(plan, 31)
                        
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
                        
                        # Check if payment already recorded
                        existing = await db.execute(
                            select(VIPPayment).where(
                                VIPPayment.provider_charge_id == payment_data["id"]
                            )
                        )
                        
                        if not existing.scalar_one_or_none():
                            vip_payment = VIPPayment(
                                user_id=user.id,
                                telegram_charge_id="",
                                provider_charge_id=payment_data["id"],
                                amount=payment_data["amount"],
                                currency=payment_data["currency"],
                                plan=plan,
                            )
                            db.add(vip_payment)
                        
                        await db.commit()
                        
                        log.info(f"Webhook: VIP granted to user {user_id} via payment {payment_data['id']}")
        
        elif event_type == "payment.failed":
            # Payment failed
            payment_data = event["payload"]["payment"]["entity"]
            log.warning(f"Payment failed: {payment_data.get('id')} - {payment_data.get('error_description')}")
        
        return {"status": "ok"}
    
    except Exception as e:
        log.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/payment-status/{payment_id}")
async def get_payment_status(payment_id: str):
    """
    Check payment status
    Useful for debugging or manual verification
    """
    try:
        payment = razorpay_client.payment.fetch(payment_id)
        
        return {
            "success": True,
            "payment_id": payment["id"],
            "status": payment["status"],
            "amount": payment["amount"],
            "currency": payment["currency"],
            "created_at": payment["created_at"]
        }
    
    except razorpay.errors.BadRequestError as e:
        raise HTTPException(status_code=404, detail="Payment not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))