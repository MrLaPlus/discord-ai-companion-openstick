import unittest
from bot.image_search import image_query
from bot.web_tools import ToolError


class ImageSearchIntentTests(unittest.TestCase):
    def test_requested_example_and_normal_text(self):
        self.assertEqual(image_query('ค้นหารูป ริมุรุสิเอามาสักรูป'), 'ริมุรุ')
        self.assertEqual(image_query('หารูปแมวหน่อย'), 'แมว')
        self.assertIsNone(image_query('บอกหน่อยว่าในรูปมีอะไร'))

    def test_private_or_ambiguous_query_not_sent(self):
        with self.assertRaises(ToolError):
            image_query('หารูปคนที่คุยในห้องเมื่อกี้')
