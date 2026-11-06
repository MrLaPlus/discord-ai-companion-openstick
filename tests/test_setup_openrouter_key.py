import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class SetupOpenRouterTests(unittest.TestCase):
    def test_atomic_setup_keeps_existing_and_hides_key(self):
        with tempfile.TemporaryDirectory() as root:
            cfg=Path(root)/'service.env'
            cfg.write_text('GROQ_API_KEY=existing\nTELEGRAM_ALLOWED_USER_IDS=123\n')
            cfg.chmod(0o600)
            script=Path('scripts/setup_openrouter_key.py').read_text().replace("Path('/etc/discord-companion-usb.env')", f'Path({str(cfg)!r})')
            output=io.StringIO()
            key='sk-or-v1-'+'a'*40
            with patch('os.geteuid',return_value=0), patch('getpass.getpass',return_value=key), patch('builtins.input',return_value=''), patch('subprocess.run') as run, contextlib.redirect_stdout(output):
                exec(compile(script,'setup_openrouter_key.py','exec'),{})
            text=cfg.read_text()
            self.assertIn('GROQ_API_KEY=existing',text)
            self.assertIn('OPENROUTER_API_KEY='+key,text)
            self.assertIn('TELEGRAM_USAGE_ADMIN_IDS=123',text)
            self.assertNotIn(key,output.getvalue())
            self.assertEqual(cfg.stat().st_mode & 0o777,0o600)
            run.assert_called_once()
    def test_invalid_key_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            cfg=Path(root)/'service.env';cfg.write_text('GROQ_API_KEY=existing\n')
            script=Path('scripts/setup_openrouter_key.py').read_text().replace("Path('/etc/discord-companion-usb.env')", f'Path({str(cfg)!r})')
            with patch('os.geteuid',return_value=0),patch('getpass.getpass',return_value='bad'):
                with self.assertRaises(SystemExit): exec(compile(script,'setup_openrouter_key.py','exec'),{})
            self.assertEqual(cfg.read_text(),'GROQ_API_KEY=existing\n')
