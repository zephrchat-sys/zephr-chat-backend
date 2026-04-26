@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM ZEPHR CHAT - DEPLOY FIX FOR 404 CHECKOUT ERROR (Windows)
REM ═══════════════════════════════════════════════════════════════════════════
REM 
REM This script will deploy the fixed main.py to Railway
REM Run this from your local Zephr Chat directory
REM
REM ═══════════════════════════════════════════════════════════════════════════

echo.
echo 🚀 Deploying Zephr Chat Checkout Fix...
echo.

REM Step 1: Initialize git if not already done
if not exist ".git" (
    echo 📦 Initializing Git repository...
    git init
    git branch -M main
) else (
    echo ✅ Git repository already initialized
)

REM Step 2: Check Railway remote
echo.
echo 🔗 Checking Railway remote...
git remote | findstr /C:"railway" >nul
if errorlevel 1 (
    echo ⚠️  Railway remote not found.
    echo Please run: railway link
    echo Or add remote manually
    echo.
    pause
)

REM Step 3: Stage all files
echo.
echo 📋 Staging files...
git add .

REM Step 4: Commit changes
echo.
echo 💾 Committing changes...
git commit -m "Fix: Add Razorpay routes and static file serving for checkout page"

REM Step 5: Push to Railway
echo.
echo 🚂 Pushing to Railway...
git push railway main

echo.
echo ═══════════════════════════════════════════════════════════════════════════
echo ✅ DEPLOYMENT COMPLETE!
echo ═══════════════════════════════════════════════════════════════════════════
echo.
echo 🧪 Test your deployment:
echo 1. Visit: https://zephr-chat-backend-production.up.railway.app/checkout.html
echo 2. Should see Razorpay checkout page (not 404!)
echo.
echo 📊 Check Railway logs: railway logs
echo.
echo Look for: ✅ Static files will be served from...
echo.
echo ═══════════════════════════════════════════════════════════════════════════
pause
