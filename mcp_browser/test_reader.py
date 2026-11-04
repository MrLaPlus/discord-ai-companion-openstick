import unittest

from mcp_browser.server import allowed_url


class BrowserPolicyTests(unittest.TestCase):
    def test_read_only_input_policy(self):
        self.assertTrue(allowed_url("https://example.com/page"))
        for url in [
            "http://example.com",
            "file:///etc/passwd",
            "https://example.com:bad",
            "https://a:b@example.com",
            "https://example.com/?token=private",
            "https://discord.com/channels/1/2/3",
            "https://example.com/a\nb",
        ]:
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url))
