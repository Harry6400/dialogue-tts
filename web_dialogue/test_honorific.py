import unittest
from .honorific import direct_address

class HonorificTests(unittest.TestCase):
    def row(self, text, speaker='AGENT', muted=False):
        return {'speaker': speaker, 'text': text, 'muted': muted}
    def test_direct_address(self):
        self.assertEqual(direct_address([self.row('您好，女士，请问需要什么？')]), 'female')
        self.assertEqual(direct_address([self.row('先生，您好。')]), 'male')
        for text in ('张先生您好', '您好王先生', '请问您是王先生吗'):
            with self.subTest(text=text): self.assertEqual(direct_address([self.row(text)]), 'male')
        self.assertEqual(direct_address([self.row('李女士，您好')]), 'female')
    def test_ambiguous_does_not_switch(self):
        for text in ('那位先生来了。', '他说“先生，您好”。', '女士，您好。先生，您好。', '这位女士，您好。', '女士，您好。那位先生来了。', '女士，您好。他说“先生，您好”。', '女士，您好。请转告李先生。', "他说'您好，先生，请坐。'", '我刚才对客户说：您好，先生，请坐。', '他说“您好，先生，请坐。', '这位张先生您好', '我叫李先生', '您找王先生吗', '张先生您好，李女士您好'):
            with self.subTest(text=text): self.assertEqual(direct_address([self.row(text)]), 'undetermined')
    def test_user_and_muted_are_ignored(self):
        self.assertEqual(direct_address([self.row('先生，您好。', 'USER'), self.row('女士，您好。', muted=True)]), 'undetermined')
