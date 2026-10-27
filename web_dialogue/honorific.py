"""Conservative direct-address detection for fictional or user-supplied dialogue.

No inference of real-world gender: only a clear form of address can select a
voice presentation. Ambiguous or quoted uses preserve the caller's choice.
"""
import re

_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"]*\"|‘[^’]*’')
_DIRECT = re.compile(r'(?:^|[，,。！？!?；;])\s*(?:您好|你好|请问)?\s*(女士|先生)\s*(?:[，,。！？!?：:]|您好|你好|请问)')
_SURNAME = r'[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东殴殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公]'
_NAMED = re.compile(r'(?:^|[，,。！？!?；;])\s*(?:您好|你好)?\s*' + _SURNAME + r'(女士|先生)\s*(?:[，,。！？!?：:]|您好|你好|请问|$)')
_IDENTIFY = re.compile(r'(?:^|[，,。！？!?；;])\s*请问您是' + _SURNAME + r'(女士|先生)吗[？?]?$')
_OTHER = re.compile(r'(?:这位|那位|一位|某位|该|他是|她是|称为|叫做|称呼为)\s*(?:女士|先生)')

def direct_address(rows):
    """Return female/male/undetermined, ignoring muted/non-AGENT content."""
    found = set()
    for row in rows:
        if str(row.get('speaker', '')).strip().upper() != 'AGENT' or row.get('muted'):
            continue
        text = str(row.get('text', ''))
        if any(ch in text for ch in ('\"', "'", '“', '”', '‘', '’', '「', '」', '『', '』')) and re.search(r'女士|先生', text):
            return 'undetermined'
        if re.search(r'(?:说|称呼|称作|转告|告诉|提到|询问)[^。！？!?；;]*(?:女士|先生)', text):
            return 'undetermined'
        quoted = _QUOTED.findall(text)
        if any('女士' in quote or '先生' in quote for quote in quoted) or _OTHER.search(text):
            return 'undetermined'
        text = _QUOTED.sub('', text)
        direct = _DIRECT.findall(text) + _NAMED.findall(text) + _IDENTIFY.findall(text)
        if len(direct) != len(re.findall(r'女士|先生', text)):
            return 'undetermined'
        found.update(direct)
    if found == {'女士'}: return 'female'
    if found == {'先生'}: return 'male'
    return 'undetermined'
