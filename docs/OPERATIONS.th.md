# คู่มือติดตั้งและดูแลบอท

รุ่นนี้รันใน Docker แบบไม่มี GUI/เว็บจัดการ ใช้ Groq `qwen/qwen3.8-27b` และเก็บข้อมูลใน SQLite บน volume ของ Docker

## 1. สร้างบอทและเตรียมสิทธิ์

1. ไปที่ [Discord Developer Portal](https://discord.com/developers/applications) สร้าง Application และตั้งชื่อบอท
2. หน้า **Bot** สร้าง/รีเซ็ต token แล้วเก็บใน `.env` เท่านั้น ไม่ส่งขึ้น Git หรือโพสต์ในแชท
3. เปิด **Message Content Intent** ในหน้า Bot รุ่นนี้ไม่ต้องใช้ Server Members Intent
4. สร้างลิงก์ติดตั้งแบบ **Guild Install** โดยมี scopes `bot` และ `applications.commands`
5. ให้บอทมี **View Channel**, **Read Message History**, **Send Messages** ในห้องที่จะใช้ ไม่จำเป็นต้องให้ Administrator
6. ผู้ตั้งค่าบอทในเซิร์ฟเวอร์ต้องมี **Manage Server**
7. ขอคีย์ใหม่จาก [Groq Console](https://console.groq.com/keys) และตรวจแผน/โควตาจริงของบัญชี ไม่ใช้คีย์ที่เคยเผยแพร่ในแชท

## 2. ตั้งค่าและเปิด Docker

ต้องมี Docker Engine และ Compose plugin บนเครื่องที่เชื่อม Discord และ Groq ทางอินเทอร์เน็ตได้

```bash
git clone https://github.com/MrLaPlus/discord-ai-companion.git
cd discord-ai-companion
cp .env.example .env
```

หาก `.env` มีอยู่แล้ว ให้แก้ไฟล์เดิม ไม่คัดลอกทับ ใส่ `DISCORD_TOKEN` และ `GROQ_API_KEY` ใน editor บนเครื่อง คง `GROQ_MODEL=qwen/qwen3.8-27b` ไว้

แนะนำตั้ง `DISCORD_GUILD_ID` เป็น Server ID ของเซิร์ฟเวอร์ทดสอบ เพื่อให้ slash commands ลงทะเบียนเฉพาะเซิร์ฟเวอร์นั้นและพร้อมใช้เร็ว เปิด Developer Mode ใน Discord เพื่อ Copy Server ID ถ้าเว้นค่าว่างจะลงทะเบียนแบบ global ซึ่งอาจใช้เวลาปรากฏ อย่าสลับขอบเขต guild/global โดยไม่ตรวจคำสั่งเก่าที่อาจยังแสดง

```bash
docker compose config --quiet
docker compose build
docker compose run --rm bot python -m bot --check
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f bot
```

`--check` ตรวจโครง config/ฐานข้อมูล/คำสั่งโดยไม่เชื่อมต่อเครือข่าย และแสดงเฉพาะว่ามีคีย์หรือไม่ ต้องเป็น `Discord=True; AI=True` ก่อนเปิดจริง การตรวจนี้ไม่ยืนยันว่าคีย์ถูกต้อง

ทุกห้องปิดการอ่านและตอบเป็นค่าเริ่มต้น บอทจะเริ่มคุยหลังแอดมินใช้คำสั่งด้านล่าง การที่ container เป็น healthy ยืนยันการเชื่อม Discord/worker/heartbeat ไม่ใช่การตรวจ AI ด้วยคำขอเสียโควตาทุกครั้ง

## 3. เปิดห้องใช้งาน

ในห้องทั่วไปที่อยากให้ตอบเฉพาะแท็ก/ตอบกลับ:

```text
/settings channel readable:true respond_here:true bot_room:false
```

ในห้องสำหรับคุยกับบอทโดยเฉพาะ:

```text
/settings channel readable:true respond_here:true bot_room:true
```

เลือกห้องอื่นผ่านพารามิเตอร์ `channel` ได้ หากอยากคุยโดยไม่เก็บประวัติห้อง ให้ใช้ `readable:false respond_here:true bot_room:false` ซึ่งยังส่งข้อความที่เรียกบอทไปให้ Groq แต่ไม่ใช้ความจำ/ประวัติของห้องนั้น

ปรับบุคลิก:

```text
/settings tone level:2
/tone level:2
```

ใช้ค่าต่ำกว่าระหว่างระดับห้องกับระดับของคนคุย ค่าเริ่มต้นทั้งคู่คือ 1 ระดับ 0 คือหยุดแซว ระดับ 3 คือกวนขึ้นแต่ยังมีขอบเขต

## 4. การอ่านข้ามห้อง

ตั้ง `share_group` เดียวกันให้แต่ละห้องที่ต้องการแชร์ พร้อมระบุค่าห้องอื่นให้ครบ เพราะคำสั่ง channel เป็นการบันทึกค่าชุดใหม่:

```text
/settings channel channel:#general readable:true respond_here:true bot_room:false share_group:friends
/settings channel channel:#games readable:true respond_here:true bot_room:false share_group:friends
```

ข้อความปกติแชร์ข้ามได้เมื่อพิสูจน์ว่าคนในห้องปลายทางทุกคนอ่านประวัติต้นทางได้ รวมถึงกรณีห้องจำกัดสิทธิ์ที่เข้ากันได้ ใช้ `/ask question:...` เพื่อตอบส่วนตัวเมื่อคนอ่านต่างกัน ผู้ถามและบอทต้องมีสิทธิ์ต้นทางและอยู่กลุ่มแชร์เดียวกัน ข้อมูล NSFW ไม่ไหลไปห้องทั่วไป ดู [การค้นอัตโนมัติ](SMART_HISTORY.th.md)

ตรวจสิทธิ์ก่อนส่งบริบทให้ AI และก่อนตอบ ตรวจว่าข้อความต้นทางยังมีและไม่ถูกแก้ ถ้าสถานะ/ความจำ/สิทธิ์เปลี่ยนระหว่างงาน อาจยกเลิกคำตอบนั้น ให้ส่งคำถามใหม่ได้ ไม่ถือว่าโมเดลได้รับข้อมูลที่ไม่เคยส่งไปแล้วกลับคืนได้

## 5. นำเข้าประวัติ

```text
/history import channel:#games limit:500 days:7
```

สูงสุด 5,000 ข้อความต่อคำสั่ง เลือกช่วงด้วย days และ before_message อายุแคชนับจากตอนดึงตาม HISTORY_RETENTION_DAYS ข้ามข้อความบอท/webhook และคนที่ปิดการเก็บข้อมูล มีความคืบหน้าแบบส่วนตัว งานหยุดเมื่อสถานะเปลี่ยนหรือเกิน 10 นาที ปกติถามบอทให้ดึงเองได้โดยไม่ต้องใช้คำสั่งนี้

เมื่อนำเข้าชุดแรกเสร็จจะบอก `before_message` สำหรับอ่านชุดเก่ากว่านั้น:

```text
/history import channel:#games limit:500 days:30 before_message:MESSAGE_ID_FROM_PREVIOUS_RESULT
```

รหัสต้องเป็นตัวเลข message ID จริง ไม่ใช่ข้อความตัวอย่างด้านบน การนำเข้าซ้ำอัปเดตรหัสเดิมแทนการเพิ่มแถวซ้ำ การค้นย้อนหลังใช้ substring และส่วนคำภาษาไทย ไม่ใช่ semantic search จึงอาจไม่พบคำพ้อง/การสะกดต่างกัน

```text
/catchup hours:24
```

สรุปตัวอย่างจากข้อความที่เก็บไว้และเปิดเผยได้ ไม่รับประกันว่าครบทุกข้อความ คำตอบ catch-up ส่งแบบส่วนตัว

## 6. ความจำและข้อมูลส่วนตัว

```text
/remember text:เรียกเราว่านากิ
/memory list page:1
/memory show memory_id:1
/memory edit memory_id:1 text:เรียกเราว่านากิ ชอบเกมแมว
/forget memory_id:1
/privacy store_data:false
```

ความจำคนผูกกับเซิร์ฟเวอร์ คน และช่องต้นทาง ใช้เฉพาะบริบทที่มีสิทธิ์ นอกจาก `/remember` รุ่นใหม่เสนอจำความชอบที่เจ้าตัวบอกโดยตรงและบันทึกหลังเจ้าตัวกดปุ่มยืนยันเท่านั้น ดู [คู่มือฟีเจอร์ต่อยอด](COMPANION_FEATURES.th.md)

`/privacy store_data:false` ปิดการเก็บและลบความจำ/ประวัติของคุณในฐานข้อมูลปัจจุบันในธุรกรรมเดียวกัน รวมถึงป้องกันนำเข้าประวัติของคุณกลับมา ยังเรียกบอทได้ โดยข้อความที่เรียกจะส่งให้ Groq เพื่อการตอบครั้งนั้น

`/memory clear confirm:true` ลบความจำและประวัติของคุณ แต่ไม่ปิดการเก็บในอนาคต ใช้ `/privacy` ถ้าต้องการปิดด้วย ข้อความของคนอื่นที่กล่าวถึงคุณและบันทึกห้องที่แอดมินเพิ่มไม่ได้ถูกตรวจจำแนกชื่อโดยอัตโนมัติ ให้ผู้ดูแลลบบันทึกเหล่านั้นด้วยหากเกี่ยวข้อง

แอดมินเพิ่มกติกาหรือประเด็นห้องด้วย `/room-memory add text:...` ดูด้วย `list page:1`/`show memory_id:...` และลบด้วย `forget memory_id:...` อย่าใช้บันทึกห้องเก็บข้อมูลส่วนตัวคนอื่น บันทึกห้องถือเป็นโน้ตแอดมิน ไม่ใช่ข้อเท็จจริงที่ตรวจแล้ว

บอทไม่เก็บคำตอบที่ AI สร้างไว้ในฐานข้อมูล แต่ใช้ RAM สำหรับบทสนทนาล่าสุด อายุเริ่มต้น 30 นาที สูงสุด 3 รอบ แยกคน/ห้อง/คำตอบส่วนตัวและอยู่ในงบขนาดบริบท ล้างเมื่อปิดเก็บข้อมูล/ข้อมูลเปลี่ยน/รีสตาร์ต ข้อมูลใน Discord ต้นทางไม่ได้ถูกลบโดยคำสั่งของบอทนี้

## 7. แทรกคุย ช่วงเงียบ และพักบอท

```text
/settings interject probability:0.1 cooldown:300 hourly:4 quiet_start:23 quiet_end:7
```

หมายถึงโอกาส 10% ต่อข้อความใหม่ที่ผ่านเงื่อนไข พักอย่างน้อย 300 วินาที สูงสุด 4 ครั้งต่อชั่วโมง ไม่แทรก 23:00–07:00 ตาม `TIMEZONE` ใช้กับห้องที่เปิดตอบและ `bot_room:false` เท่านั้น โหมดห้องบอทตอบเต็มที่จึงมีลำดับสูงกว่า interjection ตั้ง probability เป็น 0 เพื่อปิด

quiet_start/quiet_end เป็น -1 ทั้งคู่คือไม่ตั้งช่วงเงียบ ตั้งเท่ากันคือเงียบทั้งวัน ช่วงเงียบมีผลกับการแทรกคุย ไม่ห้ามการตอบแท็ก/ตอบกลับ

```text
/pause paused:true whole_server:true
/pause paused:false whole_server:true
/status
```

pause หยุดการตอบ แต่ยังเก็บแชทในห้องที่เปิด readable; ปิด readable หากต้องการหยุดเก็บด้วย โควตาใน `/status` รวมทั้ง instance และนับตามหน้าต่างเลื่อน 24 ชั่วโมง ไม่ได้รีเซ็ตตามเที่ยงคืนไทย

## 8. โควตา การปรับแต่ง และข้อจำกัด

ค่าตัวอย่างเป็นเพดานในเครื่อง ไม่ใช่การยืนยันแผนฟรีของบัญชี ตรวจ [Groq Limits](https://console.groq.com/settings/limits) ก่อนใช้งานจริง

- `REQUESTS_PER_MINUTE`, `REQUESTS_PER_DAY`, `TOKENS_PER_MINUTE`, `TOKENS_PER_DAY`: เพดานทุกเซิร์ฟเวอร์ใน instance รวมกัน
- `MAX_CONTEXT_BYTES`: ขนาดบริบทที่ค้น; `MAX_OUTPUT_TOKENS`: เพดานคำตอบ
- `QUEUE_SIZE`, `WORKERS`, `USER_COOLDOWN_SECONDS`: คิว/การทำงานพร้อมกัน; เริ่มด้วย worker 1
- `HISTORY_RETENTION_DAYS`=30, `MEMORY_RETENTION_DAYS`=180: อายุข้อมูลเริ่มต้น ตรวจอายุระหว่างค้นและกวาดข้อมูลทุกชั่วโมง
- `MAX_MESSAGES_PER_GUILD`=100000: เพดานข้อความหลัง maintenance
- `DATABASE_FILENAME`: ชื่อฐานข้อมูลภายใน Docker volume; `DATABASE_PATH` สำหรับรัน Python บนเครื่องโดยตรง

หลังแก้ `.env` ใช้ `docker compose up -d --force-recreate` เพดานโควตาใช้การประเมินเผื่อสูงจาก UTF-8 bytes + framing + output ทำให้จำนวนครั้งใช้ได้อาจต่ำกว่าโควตาผู้ให้บริการ นับคำขอที่ล้มเหลวด้วย และพักตาม Retry-After เมื่อได้ 429 ไม่ยิงซ้ำรัวหรือเปลี่ยนไปเสียเงินเอง

สถานะ unhealthy ไม่ทำให้ Docker restart เองโดยอัตโนมัติ restart policy ใช้เมื่อโปรเซสออก ให้ตรวจ log และ `docker compose restart bot` เมื่อแก้เหตุแล้ว คำเตือนว่าไม่มี PyNaCl/davey เป็นเรื่อง voice ที่รุ่นนี้ไม่ได้ใช้

## 9. สำรองและกู้ข้อมูล

สำรอง SQLite ด้วย backup API ได้ขณะบอทรัน โดยรวมข้อมูลที่ commit ใน WAL แล้ว:

```bash
docker compose exec bot python -m bot.backup create /app/data/backups/manual-2026-10-27.sqlite3
mkdir -p backups
docker compose cp bot:/app/data/backups/manual-2026-10-27.sqlite3 ./backups/
```

ใช้ชื่อไฟล์ใหม่ทุกครั้ง คำสั่งไม่เขียนทับไฟล์เดิม ควรคัดลอกออกไปอีกเครื่อง/ดิสก์ด้วย เพราะ backup ภายใน volume เดียวไม่ช่วยกรณี volume สูญหาย ไฟล์สำรองมีข้อมูลแชทส่วนตัว ต้องเก็บให้เหมาะสม

กู้ลงฐานข้อมูลใหม่เท่านั้น ไม่ทับไฟล์เก่าหรือ WAL:

```bash
docker compose stop bot
docker compose run --rm bot python -m bot.backup restore /app/data/backups/manual-2026-10-27.sqlite3 --database /app/data/restored.sqlite3
```

แก้ `.env` เป็น `DATABASE_FILENAME=restored.sqlite3` แล้ว:

```bash
docker compose up -d --force-recreate
```

เก็บไฟล์เก่าไว้จนตรวจข้อมูลใหม่ผ่าน การกู้ backup เก่าอาจย้อนสถานะ opt-out/การลบ ให้ใช้ข้อมูลสำรองที่ใหม่และทบทวนคำขอลบก่อนเปิดบอทอีกครั้ง รุ่นใหม่สำรองอัตโนมัติทุก 24 ชั่วโมง เก็บ 7 วัน/สูงสุด 32 ชุดโดยไม่ลบ manual backup ดู [การตั้งค่า](COMPANION_FEATURES.th.md)

## 10. อัปเดตและตรวจรับจริง

สำรองก่อนอัปเดต แล้ว `git pull --ff-only` ตามด้วย `docker compose up -d --build` อย่าใช้ `docker compose down -v` ถ้าต้องการเก็บความจำ

เมื่อใส่คีย์แล้ว ตรวจจริงตามนี้:

1. แท็กและตอบกลับบอทในห้องทั่วไป แล้วตรวจว่าไม่ตอบข้อความอื่นเอง
2. เปิดห้องบอทและคุยโดยไม่แท็ก
3. สั่งจำ รีสตาร์ตด้วย `docker compose restart bot` แล้วถามเรื่องเดิม
4. ใส่ข้อความทดสอบในห้องลับ ถามจากห้องทั่วไป ต้องไม่เห็นข้อมูลหรือแหล่งอ้างอิงจากห้องลับ
5. ทดสอบ `/privacy store_data:false` และนำเข้าประวัติอีกครั้ง ต้องไม่คืนข้อมูลเจ้าตัวในฐานข้อมูล
6. เปิด/ปิด interjection และช่วงเงียบ ตรวจ `/status`, log และ backup/restore

ผลตรวจอัตโนมัติและสิ่งที่ยังไม่ตรวจอยู่ใน [PROGRESS.md](../PROGRESS.md) ไม่ถือว่าผ่าน live acceptance จนกว่าจะทำรายการข้างต้นกับ Discord จริง

ผู้พัฒนาทดสอบโดยไม่ใช้คีย์ได้ด้วย `python -m unittest discover -v` หลังติดตั้ง `requirements.txt` สำหรับ smoke test ของ volume ใช้ `python3 scripts/docker_smoke.py` หลัง build image สคริปต์นี้ใช้คอนเทนเนอร์สองตัวและ volume ทดสอบใหม่ แล้วลบเฉพาะ volume ที่มันสร้าง ไม่แตะข้อมูลใช้งาน

ทดสอบ HTTP ไปยังเซิร์ฟเวอร์จำลองภายในคอนเทนเนอร์โดยไม่ต่อภายนอก:

```bash
docker run --rm --network none --read-only --tmpfs /tmp:size=32m --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" discord-ai-companion:local python -m unittest tests.http_integration -v
```
