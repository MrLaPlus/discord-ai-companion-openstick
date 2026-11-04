# discord-ai-companion-openstick

บอท Discord AI Companion รุ่นแยกสำหรับ Linux บนบอร์ดทรัพยากรจำกัด ทำงานด้วย Python และ systemd โดยไม่ต้องใช้ Docker พัฒนาต่อยอดจาก [MrLaPlus/discord-ai-companion](https://github.com/MrLaPlus/discord-ai-companion)

- คุยผ่าน Discord และ Groq พร้อมจำกัดสิทธิ์ตามห้อง
- ค้นเว็บผ่าน Tavily และใช้ DuckDuckGo สำรอง อ่านหน้า HTTPS สาธารณะได้หนึ่งลิงก์ต่อครั้ง
- หน้าสถานะภายในเครื่องและตัวจับเวลาแต่ละขั้นแบบไม่บันทึกเนื้อหา
- service แยกสำหรับบอทและเครื่องมือเว็บ ตัวอย่างอยู่ใน `deploy/systemd/`

## เริ่มต้น

ต้องมี Linux, Python 3.13, Discord bot token และ Groq API key; Tavily API key เป็นทางเลือก สำเนาตัวอย่างการตั้งค่าอยู่ใน `.env.example` และ dependencies สำหรับ USB อยู่ใน `requirements-usb.txt` ตรวจสอบและปรับค่า path, สิทธิ์ และ environment file ใน systemd unit ก่อนติดตั้งบนเครื่องของตนเอง คู่มือสรุป: [docs/USB_LIGHT.th.md](docs/USB_LIGHT.th.md)

โปรเจกต์นี้ไม่เก็บคีย์ ฐานข้อมูล ประวัติแชต หรือไฟล์สำรองใน Git ส่วนเอกสารอื่นบางไฟล์และไฟล์ Docker ที่สืบทอดมาจากรุ่นก่อนเป็นข้อมูลเก่า ไม่ใช่วิธีติดตั้งสำหรับ OpenStick

## ทดสอบ

```sh
python -m unittest discover -s tests
python -m unittest mcp_web.test_safe mcp_web.test_tavily
```

ฟีเจอร์อ่านเว็บรองรับเฉพาะข้อความที่ดึงได้จากหน้า HTTPS สาธารณะ เว็บไซต์ที่ต้องล็อกอินหรือพึ่ง JavaScript อาจอ่านไม่ได้
