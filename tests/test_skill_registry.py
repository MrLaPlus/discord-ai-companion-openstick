import unittest
from bot.skill_registry import invoke_skill, parse_skill_command


class SkillRegistryTests(unittest.TestCase):
    def test_explicit_allowlisted_skills_only(self):
        self.assertEqual(invoke_skill('ข่าว', 'Gemini'), 'ค้นเว็บ ข่าว Gemini')
        self.assertEqual(invoke_skill('factcheck', 'โลกกลม'), 'เช็กข่าว โลกกลม')
        self.assertEqual(invoke_skill('github', 'owner/repo'), '/github_release owner/repo')
        self.assertEqual(parse_skill_command('/skills'), ('list', ''))
        self.assertEqual(parse_skill_command('/skill ข่าว AI'), ('invoke', 'ค้นเว็บ ข่าว AI'))
        with self.assertRaises(ValueError):
            invoke_skill('shell', 'rm something')
        with self.assertRaises(ValueError):
            invoke_skill('ข่าว', 'a\nb')
