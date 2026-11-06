# Discord AI Companion OpenStick

[ภาษาไทย](#ภาษาไทย) · [English](#english)

## ภาษาไทย

บอทผู้ช่วยสำหรับ Discord และ Telegram ออกแบบให้รันบน Linux บอร์ดทรัพยากรจำกัดด้วย Python และ systemd โดยไม่ต้องใช้ Docker เป็นโปรเจกต์แยกที่พัฒนาต่อยอดจาก [Discord AI Companion](https://github.com/MrLaPlus/discord-ai-companion)

### ความสามารถ

- **แชตและโมเดล:** ใช้ TokenMe เป็นตัวหลัก โดยเริ่มที่ DeepSeek V4 Flash และเลือก Qwen 3.8 27B ได้ด้วย `/model` หาก TokenMe ใช้งานไม่ได้จะลอง Groq สำรอง พร้อมแจ้งสถานะ กรณีไม่มี TokenMe key จะใช้ Groq โดยตรง
- **Telegram และ Discord:** แชต ค้นเว็บ อ่านลิงก์ ตรวจข่าว สร้างรายงาน `.md` หรือ `.txt` และดูงานตั้งเวลา มีการกำหนดสิทธิ์แยกตามแพลตฟอร์ม
- **ค้นข้อมูล:** ใช้ Tavily และ DuckDuckGo สำรอง รวมถึงดู release ล่าสุดของ GitHub repository สาธารณะด้วย `/skill github owner/repo`
- **รูปและเสียง:** Telegram รับรูปเพื่อวิเคราะห์ผ่าน Groq และถอดข้อความเสียงด้วย Groq Whisper; การแสดงข้อความถอดเสียงเปิดหรือปิดได้
- **งานตั้งเวลา:** เตือนหรือรายงานข่าวในช่องทางเดิม และดูรายการผ่าน `/tasks` หรือ `/schedules` โดยใช้เขตเวลาที่ตั้งไว้
- **สถานะและสถิติ:** ดูสถานะบอร์ดด้วย `/status` และดูยอด TokenMe, Groq และ OpenRouter ที่บอทบันทึกเองด้วย `/usage` หรือ `/usage detail` ตามสิทธิ์ผู้ดูแล

ตัวเลือก `/model` ได้แก่ `auto`, `deepseek`, `qwen` และ `groq` โดยแยกตามผู้ใช้ การวิเคราะห์รูปและเสียงยังใช้ Groq การใช้งาน TokenMe ไม่ได้ตั้งโควตา token หรือจำนวนคำขอฝั่งบอท แต่ข้อจำกัดของผู้ให้บริการยังมีผล

### ติดตั้ง

1. เตรียม Linux, Python 3.13, Discord bot token และ Groq API key ดูการติดตั้งแบบ systemd ใน [คู่มือ OpenStick](docs/USB_LIGHT.th.md)
2. ติดตั้ง dependencies จาก `requirements-usb.txt` ใน virtual environment และตั้งค่า environment file ตามตัวอย่าง `.env.example` โดยเก็บไฟล์จริงให้เฉพาะผู้ดูแลอ่านได้
3. เพิ่ม `TOKENME_API_KEY` หากต้องการใช้ TokenMe; ตั้ง `TELEGRAM_BOT_TOKEN` และ `TELEGRAM_ALLOWED_USER_IDS` หากต้องการ Telegram และ `TELEGRAM_USAGE_ADMIN_IDS` สำหรับผู้ดูแลที่ดูสถิติ
4. ตรวจ path และสิทธิ์ใน `deploy/systemd/` ให้ตรงกับเครื่องก่อนเปิดบริการ ตัวช่วย `scripts/setup_tokenme_key.py` สำหรับบอร์ดที่ติดตั้งแล้วจะรับคีย์โดยไม่แสดงขณะพิมพ์

Tavily เป็นทางเลือก การค้นเว็บและอ่านลิงก์รองรับข้อมูลสาธารณะ เว็บไซต์ที่ต้องเข้าสู่ระบบหรือพึ่ง JavaScript อาจอ่านไม่ได้ `/usage` นับเฉพาะข้อมูลที่บอทบันทึกเอง จึงอาจไม่ตรงกับยอดใน dashboard ของผู้ให้บริการ และจะแจ้งเมื่อไม่ทราบจำนวน token

### ทดสอบ

```sh
python -m unittest discover -s tests
```

## English

A Discord and Telegram assistant designed for resource-constrained Linux boards using Python and systemd, without Docker. This is a standalone project derived from [Discord AI Companion](https://github.com/MrLaPlus/discord-ai-companion).

### Features

- **Chat and models:** TokenMe is the primary provider, defaulting to DeepSeek V4 Flash with Qwen 3.8 27B selectable through `/model`. Groq is used as a fallback when TokenMe fails, with a status notice. Without a TokenMe key, the bot uses Groq directly.
- **Telegram and Discord:** Chat, search the web, read links, check claims, produce `.md` or `.txt` reports, and view scheduled tasks. Access controls are scoped by platform.
- **Research:** Tavily search with DuckDuckGo fallback; `/skill github owner/repo` retrieves the latest release of a public GitHub repository.
- **Images and voice:** Telegram images are analyzed through Groq, and voice messages are transcribed with Groq Whisper. Transcript display can be toggled.
- **Scheduled tasks:** Deliver reminders or news to the originating channel and list them with `/tasks` or `/schedules`, using the configured timezone.
- **Status and usage:** `/status` reports board status. Authorized administrators can use `/usage` or `/usage detail` to see locally recorded TokenMe, Groq, and OpenRouter usage.

The `/model` choices are `auto`, `deepseek`, `qwen`, and `groq`, scoped per user. Image analysis and voice transcription still use Groq. The bot does not impose a TokenMe token or request quota; provider-side limits still apply.

### Installation

1. Prepare Linux, Python 3.13, a Discord bot token, and a Groq API key. See the [OpenStick systemd guide](docs/USB_LIGHT.th.md) for setup details.
2. Install `requirements-usb.txt` in a virtual environment and configure the environment file using `.env.example`. Restrict access to the real configuration file.
3. Set `TOKENME_API_KEY` to enable TokenMe. For Telegram, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`; configure `TELEGRAM_USAGE_ADMIN_IDS` for usage administrators.
4. Review the paths and permissions in `deploy/systemd/` before enabling the services. On an already installed board, `scripts/setup_tokenme_key.py` accepts the key without echoing it.

Tavily is optional. Search and link reading access public content; pages requiring login or JavaScript may not be readable. `/usage` reports only data recorded by this bot, which may differ from the provider dashboard, and marks unknown token usage.

### Tests

```sh
python -m unittest discover -s tests
```

Do not commit API keys, environment files, databases, chat history, or backups.
