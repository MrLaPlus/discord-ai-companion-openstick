# ค้นเว็บด้วย MCP — คู่มือรุ่นใช้งานจริง

> รุ่นค้นเว็บเดิมด้านล่าง; ส่วนเพิ่ม RSS/YouTube/Playwright โหมด4 และห้องใหม่ ดู [EXTENSIONS.th.md](EXTENSIONS.th.md)

2026-10-28 · ดูสถานะการเปิดใช้ล่าสุดใน PROGRESS.md

ใช้ `mcp==2.3.0` ต่อ `duckduckgo-mcp-server==0.7.0` ที่แยกอยู่ใน Docker service `mcp-web` ไม่ต้องมีคีย์ค้นเว็บเพิ่ม โมเดลคุยยังเป็นตัวเดิม ไม่ติดตั้ง server ทั้งสารบัญ awesome-mcp-servers อัตโนมัติ

## ใช้งาน

- ตั้ง `MCP_ENABLED=true` ใน `.env` แล้ว `docker compose up -d --build` ทั้งสอง service รันโดยไม่มี GUI และไม่เปิดพอร์ตสู่ host
- แอดมินใช้ `/mcp channel mode:hybrid` ในห้องที่เปิดให้บอทตอบอยู่แล้ว แต่ละห้องเริ่มเป็น `off` ใช้ `channel:` เลือกห้องอื่นได้ ต้องมี Manage Server
- `hybrid` ค้นข้อมูลล่าสุดเมื่อคำถามชัดเจนและมีปุ่มค้นเพิ่ม; `auto` ค้นเอง; `button` ให้ตรวจคำค้นผ่านปุ่มก่อน; `off` ปิด คำถามที่อิงข้อมูลคน/แชทยังต้องยืนยันคำค้นแม้เป็น auto
- เช่น `@ผู้ช่วย สกอร์บอลแมนยูล่าสุด`, `@ผู้ช่วย ข่าวล่าสุดของ [หัวข้อ]`, `@ผู้ช่วย อ่าน https://example.com` หรือ `/ask question:...` ซึ่งตอบส่วนตัวเหมือนเดิม
- ปุ่ม “ค้นเว็บเพิ่ม 🌐” เปิดช่องให้ตรวจ/แก้ **คำค้นที่จะส่งออกจริง** ทุกครั้ง ใช้ได้เฉพาะคนถาม ในห้องเดิม ภายใน 5 นาที กดซ้ำไม่สร้างงานซ้ำ ไม่เอาคำตอบหรือประวัติแชทมาสร้างคำค้นเอง
- คำถามกำกวม เช่น “เรื่องที่คุยในห้องเมื่อกี้” ให้กรอกคำค้นสาธารณะเอง อย่าใส่ข้อมูลส่วนตัวหรือคีย์ API บอทบล็อกรูปแบบคีย์/รหัสที่รู้จักเพิ่มอีกชั้น แต่ตัวตรวจไม่ใช่ตัวจำแนกข้อมูลส่วนตัวที่สมบูรณ์
- ไม่แสดงที่มาเอง ถาม “เอามาจากไหน” ภายใน 30 นาที หรือ reply คำตอบเดิมเพื่อขอลิงก์ที่ใช้ ไม่ค้นใหม่/เรียก AI เพื่อบอกที่มา คำตอบยาวยังแบ่งส่ง
- `/mcp status` แสดงสถานะ operation ครั้งล่าสุดกับเพดานในเครื่อง ไม่ใช่การรับประกันสถานะทุกเว็บตลอดเวลา คำถามเรื่องความสามารถตอบจากการตั้งค่าจริง

## ขอบเขตที่ตรวจได้

คำถามสาธารณะชัดเจน/URL ใช้เส้นทางตรง คำถามอื่นที่น่าต้องค้นใช้ planner คืน JSON แบบจำกัด (`search`, `fetch`, `none`) planner เห็นเฉพาะข้อความปัจจุบันกับวันที่ **ไม่เห็นประวัติ ความจำ หรือคำตอบเก่า** แล้ว host ตรวจคำสั่งอีกครั้ง รุ่นนี้ยังไม่ส่ง native `tool_calls` ของ Groq: การเรียก MCP เป็นงานของ host ส่วน final synthesis ไม่มีเครื่องมือให้เรียกต่อ

เมื่อต้องค้นเว็บ บริบทคำตอบมาจากผลค้น/หน้าเว็บที่เลือก ไม่ดึงแชทเก่าที่ไม่เกี่ยวมาปน ผลเว็บเป็นข้อมูลไม่เชื่อถือ ไม่ใช่คำสั่ง เปิดเครื่องมือเพิ่ม/รัน shell/อ่านไฟล์/ส่งข้อความแทนคนไม่ได้ คุยเล่นกับค้นประวัติยังใช้ทางเดิม และไม่ค้นเว็บระหว่างการแทรกคุยเอง

ที่มาเว็บ `W1` แยกจาก Discord `S1` ไม่ส่ง URL เว็บเข้า parser หมายเลขข้อความ Discord เก็บที่มาเว็บและคำตอบต่อเรื่องใน RAM ตามขอบเขต guild/channel/user/private ไม่เกิน 30 นาที/200 scope และล้างเมื่อ privacy/สิทธิ์/ข้อมูลเปลี่ยนหรือรีสตาร์ต ผู้ opt-out ไม่เก็บประวัติที่มา/คำตอบเพิ่ม

เก็บเพียง schema fingerprint และตัวนับโควตาใน SQLite schema 2 ตัวนับ MCP ไม่มีข้อความค้น/URL/raw user ID ใช้ HMAC ระบุโควตารายคน เก็บไม่เกิน 48 ชั่วโมงเมื่อมีคำขอถัดไป และจำกัดย้อนหลัง 24 ชั่วโมง การ opt-out ไม่รีเซ็ตเพดานป้องกันการใช้เกินโควตา ไม่เก็บ raw tool text ในฐานข้อมูลหรือ backup

## เพดานและความล้มเหลว

ค่าเริ่มต้น 6 MCP calls/นาที, 100/24 ชั่วโมง/instance, 10/24 ชั่วโมง/คน แต่ละคำถามไม่เกิน 2 calls (ค้นแล้วลองอ่านผลแรก) และไม่เกิน 2 AI calls ใช้โควตา AI เดิมร่วมกัน ไม่เพิ่มเพดานเงียบ ๆ คิดเป็นจำนวนเรียกเครื่องมือ ไม่ใช่จำนวนคำถาม งานที่เริ่มทำแล้วจำกัด 90 วินาที ข้อความที่ค้างคิวเกิน 5 นาทีหมดอายุ

เครื่องมือล้มเหลว 3 ครั้งพัก 60 วินาที และลงรายการปัญหาสำหรับระบบแจ้งแอดมินเดิม แยก “ค้นล้มเหลว” ออกจาก “ค้นสำเร็จแต่ไม่เจอผล” ถ้าอ่านหน้าแรกไม่ได้ยังใช้ snippet ที่ค้นได้ แต่ไม่อ้างว่าอ่านหน้าเต็มแล้ว

**DuckDuckGo ไม่ใช่ feed ผลบอลสด** การค้นสำเร็จอาจได้เพียงหน้ารวม/โปรแกรมแข่ง หรือข้อมูลเก่า บอทต้องแยกวันแข่ง วันค้น และผลจบเกม ถ้ายังยืนยันสกอร์ล่าสุดไม่ได้ต้องบอกตามนั้น บางเว็บต้องใช้ JavaScript/ล็อกอินหรือบล็อกบอท ไม่มีการแก้ CAPTCHA และไม่มี fallback ไปบริการเสียเงินอัตโนมัติ

## การแยกบริการ

Sidecar ไม่มีคีย์ Discord/Groq, volume ฐานข้อมูล, Docker socket, host port หรือ host network มีเครือข่ายควบคุมภายในกับบอท และ egress สำหรับเว็บ UID 10002, root read-only, cap drop, memory384MB/CPU0.75/pids96 ช่วยจำกัดผลกระทบ แต่ไม่ใช่ sandbox สำหรับ server ใดก็ได้ที่ดาวน์โหลดมา

เปิดเพียง `search` กับ `fetch_content` ตรวจชื่อ/schema และ pin fingerprint; schema เปลี่ยนต้องให้ operator ตรวจ ไม่รับ resources/prompts/sampling/elicitation หรือเครื่องมืออื่น ส่วน fetch เดิมของ upstream ถูกแทนด้วยตัวอ่าน HTTPS ที่ตรวจ DNS ทุก redirect แล้วเชื่อม **IP ที่ตรวจแล้วจริง** โดยเก็บ Host/TLS SNI เป็นชื่อเดิม ปฏิเสธ private/metadata/IPv6 transition address, credentials และ URL ประวัติ Discord

รับหน้า text ไม่เกิน 2MiB, ตัดข้อความที่ใช้ไม่เกิน4096 bytes/page, ผล MCP ไม่เกิน128KiB, ไม่รับหน้า compressed ที่ไม่ทำตาม Accept-Encoding: identity เพื่อหลีกเลี่ยง decompression bomb ผลค้น upstream ตรวจขนาด HTML หลังรับและมี deadline/resource cap; อย่านำคำกล่าวนี้ไปตีความว่าทั้ง upstream network stack เป็น streaming sandbox ทุก byte

## ตรวจสอบและย้อนกลับ

- `python -m unittest tests.test_web -v` ทดสอบการเลือกเส้นทาง/โควตา/privacy/ปุ่ม/schema/ขนาดผล ร่วมกับ regression เดิม
- `scripts/mcp_probe.py` เรียก MCP จริงด้วยคำค้นสาธารณะ ไม่มี Discord login/secret; ใช้ใน container ที่อยู่ network เดียวกับ sidecar
- `scripts/live_web_probe.py` ใช้คีย์ Groq และโควตาถาวรที่ตั้งไว้ ทดสอบ public planner → MCP → final โดยไม่อ่านข้อความหรือส่งข้อความ Discord รันเมื่อ operator ตั้งใจตรวจจริงเท่านั้น
- Sidecar `mcp_web/test_safe.py` ทดสอบ DNS/TLS pinning, redirect, private IPv4/IPv6, oversize/compression และ SDK จริงด้วย fixture
- ก่อนเปิดรุ่นนี้สร้าง backup `pre-mcp-2026-10-28.sqlite3` ตรวจ integrity และเก็บ image `discord-ai-companion:before-mcp-aecce68` สร้างคืนจาก source hotfix เดิม ไม่ copy `.env` เข้า image ย้อนกลับโดยปิด MCP, ใช้ image เดิมและไม่ลบ volume; schema ยังเป็น 2

รองรับ service ที่ตรวจแล้วหนึ่งตัว ไม่มี UI ติดตั้ง MCP server ทั่วไปหรือ role allowlist รายบริการใน MVP ถ้าจะเพิ่ม private integration, write tools, provider หรือ endpoint ใหม่ต้องออกแบบสิทธิ์แยกก่อน

2026-10-28: local per-user tool cap increased from10 to30 calls/rolling24h after a real question was rejected at admission. Tool calls are not questions: search+read uses2. Instance100/day and6/min, real provider limits and room pauses unchanged. / เพิ่มเพดานเครื่องมือรายคนจาก10เป็น30ครั้งย้อนหลัง24ชั่วโมง ไม่ใช่โควตา AI และไม่ได้เปิดห้องที่พักไว้


2026-10-28 OWNER OVERRIDE: per-person daily tool cap REMOVED, superseding10→30 entries above. MCP_USER_REQUESTS_PER_DAY=0 disables this optional cap (positive values remain supported for existing operator configs). Current env/default/example use0. Global100/day and6/min and real provider limits remain. Owner explicitly approved resuming คุยเกียว-project-arcania; resume response only, no history/interjection expansion or old-question replay. / เอาเพดานค้นเว็บรายคนออกตามคำสั่งล่าสุด และอนุมัติเปิดห้องกลับแล้ว
