# DonutSMP AFK Bot — VPS Worker (MCC)

سرویس سبک مدیریت بات‌های **Minecraft Console Client (MCC)** روی سرور **Ubuntu 22.04 / 24.04** (مناسب VPS ۱ هسته / ۱ گیگ رم).

---

## نصب آسان (تک‌دستوری)

روی VPS با دسترسی `root` فقط دستور زیر را اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/amir-hesam/AfkBotSideServer/main/install.sh | sudo bash
```

این اسکریپت به‌صورت خودکار:
- یک **Swap ۲ گیگابایتی** می‌سازد (برای پایداری روی رم ۱ گیگ).
- آخرین نسخه رسمی **Minecraft Console Client (MCC)** را نصب می‌کند.
- سرویس **Worker API (FastAPI)** را روی پورت `8080` با `systemd` فعال می‌کند.
- دامنه cPanel شما را می‌پرسد و در پایان `VPS URL`، `API_KEY` و `WEBHOOK_SECRET` را چاپ می‌کند.

---

## آپدیت به آخرین نسخه

برای دریافت آخرین تغییرات بدون تغییر `API_KEY` و `WEBHOOK_SECRET` قبلی، دوباره همان دستور نصب را اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/amir-hesam/AfkBotSideServer/main/install.sh | sudo bash
```

---

## دستورات کاربردی مدیریت سرویس

- مشاهده وضعیت سرویس:
  ```bash
  sudo systemctl status donut-worker
  ```
- مشاهده لاگ زنده سرویس:
  ```bash
  sudo journalctl -u donut-worker -f
  ```
- ریستارت سرویس:
  ```bash
  sudo systemctl restart donut-worker
  ```
- مشاهده کلیدها و تنظیمات (`API_KEY` و `WEBHOOK_SECRET`):
  ```bash
  cat /opt/donut-afk/config.env
  ```
