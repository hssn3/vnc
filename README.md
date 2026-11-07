# VNC Link — لینک ریموت دسکتاپ با Cloudflare

یک برنامه‌ی **پرتابل ویندوز** با رابط گرافیکی: اجرا کنید، رمز عبور تعریف کنید،
دکمه‌ی «ایجاد لینک» را بزنید و یک لینک HTTPS عمومی تحویل بگیرید که هرکس
با داشتن لینک + رمز بتواند از طریق مرورگر (noVNC) دسکتاپ این سیستم را ببیند و کنترل کند.

A portable Windows GUI app that shares your desktop through a password-protected
Cloudflare Quick Tunnel link, viewable in any browser via noVNC.

## نحوه کار / How it works

```
مرورگر راه دور ──HTTPS──> تونل Cloudflare ──> websockify + noVNC (127.0.0.1:6080)
                                                  │ WebSocket
                                                  ▼
                                          TightVNC (127.0.0.1:5900)
                                          احراز هویت با رمز VNC
```

1. برنامه TightVNC را فقط روی `127.0.0.1:5900` اجرا می‌کند (از LAN قابل دسترسی نیست).
2. رمز شما را به فرمت استاندارد VNC در رجیستری `HKCU\Software\TightVNC\Server` می‌نویسد
   و هنگام توقف، تنظیمات قبلی را برمی‌گرداند.
3. noVNC + websockify را روی `127.0.0.1:6080` اجرا می‌کند.
4. با `cloudflared tunnel --url` یک لینک موقت `https://….trycloudflare.com` می‌سازد.
5. با دکمه‌ی «توقف» همه چیز بسته می‌شود؛ لینک هم از کار می‌افتد.

## اجرا / Run

فایل `VNC-Link.exe` را اجرا کنید (نیازی به نصب نیست):

1. رمز عبور وارد کنید — **حداکثر ۸ کاراکتر انگلیسی/عدد** (محدودیت پروتکل VNC).
2. «ایجاد لینک» را بزنید و حدود ۱۰–۳۰ ثانیه صبر کنید.
3. لینک را کپی کنید و برای شخص مقابل بفرستید؛ رمز را جداگانه اعلام کنید.
4. طرف مقابل لینک را در مرورگر باز می‌کند، رمز را وارد می‌کند و دسکتاپ را می‌بیند.

> لینک‌های trycloudflare موقت‌اند؛ با توقف برنامه یا بستن تونل از بین می‌روند.

## ساخت از سورس / Build from source

پیش‌نیازها: Windows 10/11, Python 3.10+

```bat
pip install -r requirements.txt
python scripts\prepare_assets.py   :: دانلود cloudflared (novnc و tvnserver در ریپو هستند)
python scripts\build.py            :: ساخت VNC-Link.exe پرتابل در پوشه dist
```

## تست / Test

```bat
python tests\test_vnc_auth.py      :: تست احراز هویت RFB (نیاز به سرور در حال اجرا)
python tests\test_e2e.py           :: تست کامل: تونل عمومی + noVNC + RFB با رمز درست/غلط
```

## امنیت / Security notes

- ترافیک تا لبه‌ی Cloudflare با TLS رمزنگاری می‌شود؛ خود پروتکل VNC احراز هویت ضعیفی دارد،
  پس لینک را فقط برای افراد مورد اعتماد بفرستید و رمز را جداگانه اعلام کنید.
- سرور VNC فقط روی loopback گوش می‌دهد؛ دسترسی مستقیم از شبکه ممکن نیست.
- برنامه هیچ پورتی را روی فایروال باز نمی‌کند (تونل outbound است).

## Licenses

- کد این پروژه: MIT (see `LICENSE`)
- [TightVNC](https://www.tightvnc.com/) 2.8.85 — GPL-2.0 (`assets/tvnserver/LICENSE.txt`);
  source: https://www.tightvnc.com/download/2.8.90/tightvnc-2.8.90-src-gpl.zip
- [noVNC](https://github.com/novnc/noVNC) 1.6.0 — MPL-2.0 (`assets/novnc/LICENSE.txt`)
- [cloudflared](https://github.com/cloudflare/cloudflared) — Apache-2.0 (دانلود هنگام build)