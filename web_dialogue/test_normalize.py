import copy
import unittest
from .normalize import EXAMPLE, InputError, normalize


class NormalizeTests(unittest.TestCase):
    def test_named_role_defaults_independent_of_order(self):
        for speakers in [('USER','AGENT'),('B','A')]:
            result=normalize({'data':[{'speaker':s,'text':'虚构测试'} for s in speakers]})
            self.assertEqual([r['voice'] for r in result['script']['lines']],
                             ['zh-CN-XiaoyiNeural','zh-CN-XiaoxiaoNeural'])

    def test_default_and_original_unchanged(self):
        original = copy.deepcopy(EXAMPLE)
        result = normalize({"data": original})
        self.assertEqual(original, EXAMPLE)
        self.assertEqual([x["speaker"] for x in result["script"]["lines"]], ["A", "B", "A", "B", "A", "B"])
        self.assertEqual(result["script"]["lines"][0]["voice"], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(result["script"]["lines"][1]["voice"], "zh-CN-XiaoyiNeural")

    def test_nested_mapping(self):
        result = normalize({"data": {"data": {"turns": [{"who": {"id": 0}, "body": "你好"}]}},
                            "array_path": "data.turns", "speaker_key": "who.id", "text_key": "body"})
        self.assertEqual(result["speakers"], ["0"])
        self.assertEqual(result["rows"][0]["text"], "你好")

    def test_mapping_voice(self):
        r = normalize({"data": EXAMPLE, "voices": {"A": "zh-CN-XiaoyiNeural"}})
        self.assertEqual(r["script"]["lines"][2]["voice"], "zh-CN-XiaoyiNeural")

    def test_errors(self):
        for p in ({"data": []}, {"data": {}}, {"data": [None]},
                  {"data": [{"speaker": [], "text": "你好"}]},
                  {"data": [{"speaker": "A", "text": " "}]},
                  {"data": EXAMPLE, "rate": float("nan")},
                  {"data": EXAMPLE, "pause_ms": True},
                  {"data": EXAMPLE, "voices": {"A": "not-real"}},
                  {"data": EXAMPLE, "speaker_key": ""}):
            with self.subTest(p=p), self.assertRaises(InputError):
                normalize(p)

    def test_order_not_sorted(self):
        r = normalize({"data": [{"speaker": "Z", "text": "先"}, {"speaker": "A", "text": "后"}]})
        self.assertEqual(r["speakers"], ["Z", "A"])
        self.assertEqual([x["text"] for x in r["rows"]], ["先", "后"])

    def test_text_preserved_exactly(self):
        text = "  文本\n不改写  "
        self.assertEqual(normalize({"data": [{"speaker": "A", "text": text}]})["rows"][0]["text"], text)

    def test_safety_limits(self):
        with self.assertRaises(InputError):
            normalize({"data": EXAMPLE * 17})
        with self.assertRaises(InputError):
            normalize({"data": [{"speaker": "A", "text": "中" * 5001}]})


if __name__ == "__main__":
    unittest.main()
