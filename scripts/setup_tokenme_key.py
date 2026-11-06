#!/usr/bin/env python3
"""Set TokenMe key without shell history or revealing other service credentials."""
import getpass, os, re, stat, subprocess, tempfile
from pathlib import Path
path=Path('/etc/discord-companion-usb.env')
if os.geteuid()!=0 or not path.is_file() or path.is_symlink():
 raise SystemExit('Run with sudo on the board and verify service configuration.')
key=getpass.getpass('TokenMe API key (input hidden): ').strip()
if not re.fullmatch(r'[A-Za-z0-9._~+/-]{12,512}',key):
 raise SystemExit('Key format invalid; no changes made.')
original=path.read_text(encoding='utf-8')
lines=[line for line in original.splitlines() if not re.match(r'^\s*TOKENME_API_KEY\s*=',line)]
text='\n'.join(lines)+'\nTOKENME_API_KEY='+key+'\n'
fd,name=tempfile.mkstemp(prefix='.companion-',dir=path.parent)
try:
 os.fchmod(fd,stat.S_IMODE(path.stat().st_mode)&0o600)
 with os.fdopen(fd,'w',encoding='utf-8') as f:
  f.write(text);f.flush();os.fsync(f.fileno())
 os.replace(name,path)
finally:
 if os.path.exists(name):os.unlink(name)
print('Configuration saved; restarting bot.')
subprocess.run(['systemctl','restart','discord-usb-bot.service'],check=True)
print('Restart requested.')
