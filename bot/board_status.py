"""Small, on-demand Linux host metrics; unavailable readings remain unknown."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _cpu(path: Path) -> tuple[int, int] | None:
    try:
        values = _read(path).splitlines()[0].split()
        if values[0] != "cpu" or len(values) < 5:
            return None
        # Guest counters are already included in user/nice and must not be added twice.
        times = [int(value) for value in values[1:9]]
        if len(times) < 4 or any(value < 0 for value in times):
            return None
        return sum(times), times[3] + (times[4] if len(times) > 4 else 0)
    except (AttributeError, ValueError, IndexError):
        return None


def _cpu_percent(before, after) -> float | None:
    if before is None or after is None:
        return None
    total = after[0] - before[0]
    idle = after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100 * (total - idle) / total, 1)


def _memory(path: Path) -> dict:
    raw = _read(path)
    if raw is None:
        return {}
    values = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "kB":
            try:
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
            except ValueError:
                continue
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    swap, swap_free = values.get("SwapTotal"), values.get("SwapFree")
    result = {}
    if total is not None and available is not None and 0 <= available <= total:
        result["ram"] = {"total": total, "used": total - available, "available": available}
    if swap is not None and swap_free is not None and 0 <= swap_free <= swap:
        result["swap"] = {"total": swap, "used": swap - swap_free}
    return result


def _temperatures(root: Path) -> dict:
    result = {}
    try:
        paths = sorted(root.glob("thermal_zone*"))[:32]
        for path in paths:
            kind, raw = _read(path / "type"), _read(path / "temp")
            if not kind or not re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", kind) or raw is None:
                continue
            try:
                value = int(raw) / 1000
            except ValueError:
                continue
            if -40 <= value <= 150:
                # Retain all sensors even if two report the same type.
                label = kind if kind not in result else f"{kind} ({path.name})"
                result[label] = round(value, 1)
    except OSError:
        pass
    return result


def _snapshot(proc: Path = Path("/proc"), sys: Path = Path("/sys"), disk: Path = Path("/")) -> dict:
    data = _memory(proc / "meminfo")
    try:
        usage = shutil.disk_usage(disk)
        data["disk"] = {"total": usage.total, "used": usage.used, "available": usage.free}
    except OSError:
        pass
    try:
        uptime = float((_read(proc / "uptime") or "").split()[0])
        if uptime >= 0:
            data["uptime_seconds"] = int(uptime)
    except (IndexError, ValueError, OverflowError):
        pass
    try:
        data["load_average"] = [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        pass
    try:
        khz = int(_read(sys / "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") or "")
        if khz > 0:
            data["cpu0_mhz"] = round(khz / 1000)
    except ValueError:
        pass
    data["temperatures_c"] = _temperatures(sys / "class/thermal")
    return data


async def board_status() -> dict:
    proc = Path("/proc")
    before = await asyncio.to_thread(_cpu, proc / "stat")
    await asyncio.sleep(0.25)
    after, data = await asyncio.gather(asyncio.to_thread(_cpu, proc / "stat"), asyncio.to_thread(_snapshot))
    data["cpu_percent"] = _cpu_percent(before, after)
    return data


def board_summary(data: dict) -> str:
    def size(value):
        return f"{value / 1024**2:.0f} MiB"

    cpu = data.get("cpu_percent")
    frequency = data.get("cpu0_mhz")
    lines = ["สถานะบอร์ด USB", f"CPU: {str(cpu) + '%' if cpu is not None else 'ไม่พร้อมใช้งาน'}"]
    if frequency is not None:
        lines[-1] += f" · CPU0 {frequency} MHz"
    load = data.get("load_average")
    if load:
        lines.append("Load 1/5/15 นาที: " + " / ".join(str(value) for value in load))
    for key, label in (("ram", "RAM"), ("swap", "Swap"), ("disk", "พื้นที่ /")):
        metric = data.get(key)
        if metric:
            text = f"{label}: {size(metric['used'])} / {size(metric['total'])}"
            if "available" in metric:
                text += f" · พร้อมใช้ {size(metric['available'])}"
            lines.append(text)
        else:
            lines.append(f"{label}: ไม่พร้อมใช้งาน")
    temps = data.get("temperatures_c", {})
    lines.append("อุณหภูมิ: " + (", ".join(f"{k} {v:g}°C" for k, v in temps.items()) or "ไม่พร้อมใช้งาน"))
    uptime = data.get("uptime_seconds")
    if uptime is not None:
        lines.append(
            f"เปิดมา: {uptime // 86400} วัน {(uptime % 86400) // 3600} ชั่วโมง {(uptime % 3600) // 60} นาที"
        )
    else:
        lines.append("เวลาเปิดเครื่อง: ไม่พร้อมใช้งาน")
    return "\n".join(lines)


def is_board_question(text: str) -> bool:
    return bool(re.fullmatch(r'\s*(?:บอร์ดเป็น(?:ยัง)?ไง(?:บ้าง)?|สถานะบอร์ด|บอร์ดเป็นอย่างไร)(?:ครับ|ค่ะ|คะ)?[\s?!?.]*', text))


async def _service_state(unit: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec('systemctl', 'is-active', unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 'ไม่ทราบ'
        state = output.decode('ascii', 'replace').strip()
        if state == 'active':
            return 'ทำงาน'
        if state in {'inactive', 'failed'}:
            return 'ไม่ทำงาน'
    except OSError:
        pass
    # DynamicUser may lack system-bus access; cgroup membership only proves
    # that a process exists, not the systemd health or readiness of a service.
    procs = _read(Path('/sys/fs/cgroup/system.slice') / unit / 'cgroup.procs')
    return 'มีโปรเซส (ยังไม่ยืนยันความพร้อม)' if procs and any(line.isdigit() for line in procs.splitlines()) else 'ไม่ทราบ'


async def board_assessment() -> str:
    data, bot, search = await asyncio.gather(board_status(), _service_state('discord-usb-bot.service'), _service_state('discord-usb-search.service'))
    warnings = []
    disk = data.get('disk')
    if disk and disk['total'] and disk['used'] / disk['total'] >= .85:
        warnings.append('พื้นที่เก็บข้อมูลใช้ไปอย่างน้อย 85%')
    ram = data.get('ram')
    if ram and ram['available'] < 64 * 1024**2:
        warnings.append('RAM พร้อมใช้เหลือน้อยกว่า 64 MiB')
    if any(value >= 80 for value in data.get('temperatures_c', {}).values()):
        warnings.append('มีเซนเซอร์อุณหภูมิอย่างน้อย 80°C')
    if bot == 'ไม่ทำงาน' or search == 'ไม่ทำงาน':
        warnings.append('มีบริการที่ไม่ทำงาน')
    assessment = 'ข้อสังเกต: ' + ('; '.join(warnings) if warnings else 'ไม่พบค่าเกินเกณฑ์ที่ตรวจในครั้งนี้')
    return board_summary(data) + f'\nบริการบอท: {bot} · ค้นเว็บ: {search}\n' + assessment
