import contextlib, io, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

class SetupTests(unittest.TestCase):
 def test_hidden_key_and_preserve_config(self):
  with tempfile.TemporaryDirectory() as root:
   cfg=Path(root)/'service.env';cfg.write_text('GROQ_API_KEY=secret\nDISABLED_OPENROUTER_API_KEY=other\n');cfg.chmod(0o600)
   script=Path('scripts/setup_tokenme_key.py').read_text().replace("Path('/etc/discord-companion-usb.env')",f'Path({str(cfg)!r})')
   key='token-test-'+('x'*30);out=io.StringIO()
   with patch('os.geteuid',return_value=0),patch('getpass.getpass',return_value=key),patch('subprocess.run') as run,contextlib.redirect_stdout(out):
    exec(compile(script,'setup_tokenme_key.py','exec'),{})
   self.assertIn('TOKENME_API_KEY='+key,cfg.read_text());self.assertIn('GROQ_API_KEY=secret',cfg.read_text())
   self.assertIn('DISABLED_OPENROUTER_API_KEY=other',cfg.read_text());self.assertNotIn(key,out.getvalue());run.assert_called_once()
   self.assertEqual(cfg.stat().st_mode&0o777,0o600)
 def test_invalid_key_no_changes(self):
  with tempfile.TemporaryDirectory() as root:
   cfg=Path(root)/'service.env';cfg.write_text('GROQ_API_KEY=secret\n')
   script=Path('scripts/setup_tokenme_key.py').read_text().replace("Path('/etc/discord-companion-usb.env')",f'Path({str(cfg)!r})')
   with patch('os.geteuid',return_value=0),patch('getpass.getpass',return_value='bad'):
    with self.assertRaises(SystemExit):exec(compile(script,'setup_tokenme_key.py','exec'),{})
   self.assertEqual(cfg.read_text(),'GROQ_API_KEY=secret\n')
