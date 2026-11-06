#!/usr/bin/env python3
"""Set the OpenRouter key interactively without echoing it or printing existing config."""
import getpass
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

path = Path('/etc/discord-companion-usb.env')
if os.geteuid() != 0:
    raise SystemExit('Run with sudo on the OpenStick board.')
if not path.is_file() or path.is_symlink():
    raise SystemExit('Configuration not found or is a symlink; no changes made.')
key = getpass.getpass('OpenRouter API key (input hidden): ').strip()
if not re.fullmatch(r'sk-or-v1-[A-Za-z0-9_-]{20,200}', key):
    raise SystemExit('Key format is invalid; no changes made.')
original = path.read_text(encoding='utf-8')
admin = input('Telegram administrator numeric user ID (Enter to use sole allowed user): ').strip()
if not admin:
    matches = re.findall(r'^\s*TELEGRAM_ALLOWED_USER_IDS\s*=\s*[\"\']?([0-9]+)[\"\']?\s*$', original, re.M)
    if len(matches) == 1:
        admin = matches[0]
if not re.fullmatch(r'[0-9]{1,20}', admin) or int(admin) <= 0:
    raise SystemExit('Administrator ID is invalid; no changes made.')
new_lines = [line for line in original.splitlines() if not re.match(r'^\s*(OPENROUTER_API_KEY|TELEGRAM_USAGE_ADMIN_IDS)\s*=', line)]
new = '\n'.join(new_lines) + '\nOPENROUTER_API_KEY=' + key + '\nTELEGRAM_USAGE_ADMIN_IDS=' + admin + '\n'
fd, name = tempfile.mkstemp(prefix='.discord-companion-', dir=path.parent)
try:
    os.fchmod(fd, stat.S_IMODE(path.stat().st_mode) & 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(new); f.flush(); os.fsync(f.fileno())
    os.replace(name, path)
finally:
    if os.path.exists(name): os.unlink(name)
print('Configuration saved. Restarting bot...')
subprocess.run(['systemctl', 'restart', 'discord-usb-bot.service'], check=True)
print('Restart requested; check service status before use.')
