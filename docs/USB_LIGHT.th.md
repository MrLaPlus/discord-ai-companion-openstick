# OpenStick บน Linux

รุ่นนี้ใช้ Python และ systemd ไม่ต้องใช้ Docker ตัวอย่าง unit อยู่ใน `deploy/systemd/` และใช้ไฟล์คีย์แยกสำหรับบอทกับบริการค้นหา ตรวจสอบ path และสิทธิ์ของไฟล์ก่อนใช้งาน

1. ติดตั้ง Python 3.13 และสร้าง virtual environment ที่ `/opt/discord-companion-usb/venv`
2. ติดตั้ง dependencies จาก `requirements-usb.txt` ลงใน virtual environment
3. จัดวางซอร์สไว้ที่ `/opt/discord-companion-usb` และสร้าง environment file ตามที่ unit กำหนด โดยจำกัดสิทธิ์การอ่าน คัดลอก `.env.example` เพื่อดูชื่อตัวแปรของบอท และกำหนด `TAVILY_API_KEY` สำหรับบริการค้นหาเมื่อใช้ Tavily
4. ตรวจสอบ unit และตั้งค่า Discord/Groq ก่อนเปิดใช้งาน จากนั้นใช้ systemd เปิดบริการทั้งสองตามสภาพแวดล้อมจริง

บอทเก็บข้อมูลส่วนตัวในฐานข้อมูลและ privacy ledger แยกจากซอร์ส ก่อนย้ายหรือนำข้อมูลสำรองกลับมาต้องรักษาทั้งสองส่วนให้สอดคล้องกัน ห้ามใส่คีย์หรือฐานข้อมูลใน repository

บริการเว็บเปิดเครื่องมือค้นหาและอ่านหน้า HTTPS สาธารณะ ไม่รับประกันเว็บไซต์ที่ต้องล็อกอิน ใช้ JavaScript หรือเป็นวิดีโอ หน้าสถานะผูกกับ loopback และต้องตั้ง `OPS_TOKEN` การอ่านจากเครื่องอื่นต้องใช้วิธีเชื่อมต่อที่ปลอดภัย เช่น SSH tunnel
