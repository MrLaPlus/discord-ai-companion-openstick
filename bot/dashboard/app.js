'use strict';
const $ = id => document.getElementById(id);
const put = (id, value) => { $(id).textContent = value; };
const mib = n => typeof n === 'number' ? `${Math.round(n / 1048576)} MiB` : 'ไม่มีข้อมูล';
const percent = (a, b) => typeof a === 'number' && b > 0 ? Math.round(a / b * 100) : null;
const age = t => {
  if (typeof t !== 'number' || !Number.isFinite(t)) return 'ยังไม่มีข้อมูล';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (sec < 60) return `${sec} วินาทีที่แล้ว`;
  if (sec < 3600) return `${Math.floor(sec / 60)} นาทีที่แล้ว`;
  return `${Math.floor(sec / 3600)} ชั่วโมงที่แล้ว`;
};
const old = t => typeof t !== 'number' || Date.now() / 1000 - t > 300;
function pill(id, text, state) { const el = $(id); el.textContent = text; el.className = `pill ${state}`; }
function chart(id, values, color, maxValue) {
  const canvas = $(id), ctx = canvas.getContext('2d');
  const width = Math.max(1, Math.round(canvas.clientWidth)), height = 50;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  ctx.scale(dpr, dpr); ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = '#26394c'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, height - 1); ctx.lineTo(width, height - 1); ctx.stroke();
  const points = values.filter(v => typeof v === 'number' && Number.isFinite(v));
  if (points.length < 2) return;
  const max = Math.max(maxValue || 0, ...points, 1);
  ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
  points.forEach((v, i) => { const x = i * (width / Math.max(points.length - 1, 1)); const y = height - 4 - Math.min(v / max, 1) * (height - 10); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
  ctx.stroke();
}
function render(data) {
  const b = data.board || {}, bot = data.bot || {}, history = data.history || [];
  $('connection').className = 'dot good'; put('updated', `อัปเดต ${new Date(data.at * 1000).toLocaleTimeString('th-TH')}`);
  $('hero-dot').className = `dot ${bot.ready ? 'good' : 'bad'}`;
  put('hero-status', bot.ready ? 'บอทพร้อมทำงาน' : 'บอทยังไม่พร้อม');
  put('hero-description', bot.ready ? 'เชื่อมต่อ Discord แล้ว · ข้อมูลล่าสุดจากบอร์ด' : 'รอการเชื่อมต่อ Discord หรือ worker');
  const up = b.uptime_seconds;
  put('uptime', typeof up === 'number' ? `${Math.floor(up / 86400)} วัน ${Math.floor(up % 86400 / 3600)} ชม. ${Math.floor(up % 3600 / 60)} นาที` : 'ไม่มีข้อมูล');
  put('board-clock', 'อัปเดตทุก 2 วินาที');
  put('cpu', typeof b.cpu_percent === 'number' ? `${b.cpu_percent}%` : '—');
  put('cpu-sub', `CPU0 ${b.cpu0_mhz ? b.cpu0_mhz + ' MHz' : 'ไม่มีข้อมูล'} · Load ${b.load_average ? b.load_average[0] : '—'}`);
  const ram = b.ram || {}, ramPercent = percent(ram.used, ram.total);
  put('ram', ramPercent === null ? '—' : `${ramPercent}%`); put('ram-sub', `${mib(ram.used)} / ${mib(ram.total)} · พร้อมใช้ ${mib(ram.available)}`);
  const cpus = Object.entries(b.temperatures_c || {}).filter(([name]) => name.startsWith('cpu'));
  const currentTemp = cpus.length ? Math.max(...cpus.map(([,v]) => v)) : null;
  put('temp', currentTemp === null ? '—' : `${currentTemp}°C`); put('temp-sub', cpus.length ? cpus.map(([k,v]) => `${k.replace('-thermal','')} ${v}°`).join(' · ') : 'เซนเซอร์ไม่พร้อมใช้งาน');
  const disk = b.disk || {}, diskPercent = percent(disk.used, disk.total);
  put('disk', diskPercent === null ? '—' : `${diskPercent}%`); put('disk-sub', `${mib(disk.used)} / ${mib(disk.total)} · เหลือ ${mib(disk.available)}`); $('disk-meter').style.width = `${Math.max(0, Math.min(diskPercent || 0, 100))}%`;
  put('swap', `Swap ${mib((b.swap || {}).used)} / ${mib((b.swap || {}).total)}`);
  chart('cpu-chart', history.map(x => x.cpu), '#6cdbb4', 100);
  chart('ram-chart', history.map(x => x.ram), '#63baf8', ram.total);
  chart('temp-chart', history.map(x => x.temp), '#f0bb70', 100);
  pill('discord-status', bot.ready ? 'เชื่อมต่อ' : 'ไม่พร้อม', bot.ready ? 'good' : 'bad');
  const g = data.groq || {}, gs = String(g.last_status || '');
  pill('groq-status', old(g.checked) ? 'ข้อมูลเก่า / ไม่ทราบ' : gs === '200' ? 'ล่าสุดสำเร็จ' : 'ล่าสุดขัดข้อง', old(g.checked) ? 'neutral' : gs === '200' ? 'good' : 'warn');
  put('groq-detail', `คำขอ AI ล่าสุด · ${age(g.checked)}`);
  const s = data.search || {}, reasons = {missing_key:'ไม่มีคีย์ Tavily',quota:'Tavily ถึงเพดาน',invalid_key:'คีย์ Tavily ใช้ไม่ได้',rate_limit:'Tavily จำกัดความถี่',temporary_error:'Tavily ขัดข้องชั่วคราว'};
  pill('search-status', !s.provider ? 'ยังไม่เคยค้น' : old(s.checked) ? 'ข้อมูลเก่า' : s.provider === 'tavily' ? 'Tavily' : 'DuckDuckGo', !s.provider || old(s.checked) ? 'neutral' : s.provider === 'tavily' ? 'good' : 'warn');
  put('search-detail', `${s.reason && reasons[s.reason] ? reasons[s.reason] + ' · ' : ''}ค้นล่าสุด ${age(s.checked)} · เครดิต Tavily: ไม่มีข้อมูล`);
  const m = data.mcp || {};
  pill('mcp-status', old(m.checked) ? 'ข้อมูลเก่า / ไม่ทราบ' : ({reachable:'ตรวจพบ',unverified:'รอยืนยัน',unavailable:'ไม่พร้อม',disabled:'ปิดอยู่'}[m.status] || 'ไม่ทราบ'), old(m.checked) ? 'neutral' : m.status === 'reachable' ? 'good' : m.status === 'unavailable' ? 'bad' : 'warn');
  put('mcp-detail', `ตรวจเครื่องมือค้นหา · ${age(m.checked)}`);
  put('queue', `${bot.queue ?? '—'} งาน`); put('workers', `${bot.workers_alive ?? '—'} / ${bot.workers_total ?? '—'}`); put('guilds', bot.guilds_allowed ?? 'ไม่จำกัด');
  const backup = data.backup || {}; put('backup', backup.error ? 'มีข้อผิดพลาด' : age(backup.created));
  put('recovery', backup.recovery_ok === true ? `ตรวจการกู้คืนผ่าน · ${age(backup.recovery_checked)}` : backup.recovery_ok === false ? 'ตรวจการกู้คืนไม่ผ่าน' : 'ยังไม่ทราบผลตรวจการกู้คืน');
  const q = g.quota || {}, fmt = row => typeof row?.remaining === 'number' && typeof row?.limit === 'number' ? `${row.remaining.toLocaleString()} / ${row.limit.toLocaleString()}` : 'ไม่มีข้อมูล';
  put('quota-requests', fmt(q.requests_day)); put('quota-tokens', fmt(q.tokens_minute)); put('quota-time', `อัปเดต ${age(g.quota_checked)}`);
}
let pending = false;
async function refresh() {
  if (pending || document.hidden) return;
  pending = true;
  try {
    const response = await fetch('/api/snapshot', {cache:'no-store'});
    if (response.status === 401) { window.location.reload(); return; }
    if (!response.ok) throw new Error('unavailable');
    render(await response.json());
  } catch (_) {
    $('connection').className = 'dot bad'; put('updated', 'เชื่อมต่อไม่ได้ · กำลังลองใหม่');
    $('hero-dot').className = 'dot wait'; put('hero-status', 'ไม่ทราบสถานะปัจจุบัน');
    put('hero-description', 'ตรวจ SSH tunnel และสถานะบอท · ค่าด้านล่างเป็นข้อมูลเก่า');
    for (const id of ['discord-status', 'groq-status', 'search-status', 'mcp-status']) pill(id, 'ข้อมูลเก่า', 'neutral');
  }
  finally { pending = false; }
}
refresh(); setInterval(refresh, 2000); document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
