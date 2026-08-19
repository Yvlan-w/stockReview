"""账户名与初始密码自动生成。

客户登录账号的用户名规则：名字拼音 + '#' + 随机序号；随机序号 + 唯一性校验
避免与 user 表已有 username 冲突，防止插入失败。初始密码为随机字符串。

拼音转换优先使用 pypinyin（覆盖全量汉字、正确处理多音字）；
若运行环境未安装则降级到内置 _PINYIN 字典（覆盖常见姓氏 + 常用名用字），
保证项目在任意干净 Python 环境下都能启动。
"""
import re
import secrets

try:
    from pypinyin import lazy_pinyin, Style as _PinyinStyle
    _PYPINYIN_AVAILABLE = True
except ImportError:
    _PYPINYIN_AVAILABLE = False

# —— 降级字典：仅当 pypinyin 不可用时使用 ——
_PINYIN = {
    "赵": "zhao", "钱": "qian", "孙": "sun", "李": "li", "周": "zhou", "吴": "wu",
    "郑": "zheng", "王": "wang", "冯": "feng", "陈": "chen", "褚": "chu", "卫": "wei",
    "蒋": "jiang", "沈": "shen", "韩": "han", "杨": "yang", "朱": "zhu", "秦": "qin",
    "尤": "you", "许": "xu", "何": "he", "吕": "lv", "施": "shi", "张": "zhang",
    "孔": "kong", "曹": "cao", "严": "yan", "华": "hua", "金": "jin", "魏": "wei",
    "陶": "tao", "姜": "jiang", "戚": "qi", "谢": "xie", "邹": "zou", "喻": "yu",
    "柏": "bai", "窦": "dou", "章": "zhang", "云": "yun", "苏": "su", "潘": "pan",
    "葛": "ge", "奚": "xi", "范": "fan", "彭": "peng", "郎": "lang", "鲁": "lu",
    "韦": "wei", "昌": "chang", "马": "ma", "苗": "miao", "凤": "feng", "花": "hua",
    "方": "fang", "俞": "yu", "任": "ren", "袁": "yuan", "柳": "liu", "鲍": "bao",
    "史": "shi", "唐": "tang", "费": "fei", "廉": "lian", "岑": "cen", "薛": "xue",
    "雷": "lei", "贺": "he", "倪": "ni", "汤": "tang", "滕": "teng", "殷": "yin",
    "罗": "luo", "毕": "bi", "郝": "hao", "安": "an", "常": "chang", "乐": "le",
    "于": "yu", "时": "shi", "傅": "fu", "皮": "pi", "卞": "bian", "齐": "qi",
    "康": "kang", "伍": "wu", "余": "yu", "元": "yuan", "卜": "bu", "顾": "gu",
    "孟": "meng", "平": "ping", "黄": "huang", "和": "he", "穆": "mu", "萧": "xiao",
    "尹": "yin", "姚": "yao", "邵": "shao", "汪": "wang", "祁": "qi", "毛": "mao",
    "禹": "yu", "狄": "di", "米": "mi", "贝": "bei", "明": "ming", "臧": "zang",
    "计": "ji", "伏": "fu", "成": "cheng", "戴": "dai", "谈": "tan", "宋": "song",
    "茅": "mao", "庞": "pang", "熊": "xiong", "纪": "ji", "舒": "shu", "屈": "qu",
    "项": "xiang", "祝": "zhu", "董": "dong", "梁": "liang", "杜": "du", "阮": "ruan",
    "蓝": "lan", "闵": "min", "席": "xi", "季": "ji", "麻": "ma", "强": "qiang",
    "贾": "jia", "路": "lu", "娄": "lou", "危": "wei", "江": "jiang", "童": "tong",
    "颜": "yan", "郭": "guo", "梅": "mei", "盛": "sheng", "林": "lin", "刁": "diao",
    "钟": "zhong", "徐": "xu", "邱": "qiu", "骆": "luo", "高": "gao", "夏": "xia",
    "蔡": "cai", "田": "tian", "樊": "fan", "胡": "hu", "凌": "ling", "霍": "huo",
    "虞": "yu", "万": "wan", "支": "zhi", "柯": "ke", "管": "guan", "卢": "lu",
    "莫": "mo", "经": "jing", "房": "fang", "裘": "qiu", "缪": "miao", "干": "gan",
    "解": "xie", "应": "ying", "宗": "zong", "丁": "ding", "宣": "xuan", "邓": "deng",
    "郁": "yu", "单": "shan", "杭": "hang", "洪": "hong", "包": "bao", "诸": "zhu",
    "左": "zuo", "石": "shi", "崔": "cui", "吉": "ji", "钮": "niu", "龚": "gong",
    "程": "cheng", "嵇": "ji", "邢": "xing", "滑": "hua", "裴": "pei", "陆": "lu",
    "荣": "rong", "翁": "weng", "荀": "xun", "羊": "yang", "惠": "hui", "甄": "zhen",
    "曲": "qu", "家": "jia", "封": "feng", "芮": "rui", "羿": "yi", "储": "chu",
    "靳": "jin", "汲": "ji", "邴": "bing", "糜": "mi", "松": "song", "井": "jing",
    "段": "duan", "富": "fu", "巫": "wu", "乌": "wu", "焦": "jiao", "巴": "ba",
    "弓": "gong", "牧": "mu", "山": "shan", "谷": "gu", "车": "che", "侯": "hou",
    "宓": "mi", "蓬": "peng", "全": "quan", "郗": "xi", "班": "ban", "仰": "yang",
    "秋": "qiu", "仲": "zhong", "伊": "yi", "宫": "gong", "宁": "ning", "仇": "qiu",
    "栾": "luan", "暴": "bao", "甘": "gan", "厉": "li", "戎": "rong", "祖": "zu",
    "武": "wu", "符": "fu", "刘": "liu", "景": "jing", "詹": "zhan", "束": "shu",
    "龙": "long", "叶": "ye", "幸": "xing", "司": "si", "韶": "shao", "郜": "gao",
    "黎": "li", "蓟": "ji", "薄": "bo", "印": "yin", "宿": "su", "白": "bai",
    "怀": "huai", "蒲": "pu", "邰": "tai", "从": "cong", "鄂": "e", "索": "suo",
    "咸": "xian", "籍": "ji", "赖": "lai", "卓": "zhuo", "蔺": "lin", "屠": "tu",
    "蒙": "meng", "池": "chi", "乔": "qiao", "阴": "yin", "胥": "xu", "能": "neng",
    "苍": "cang", "双": "shuang", "闻": "wen", "莘": "shen", "党": "dang", "翟": "zhai",
    "谭": "tan", "贡": "gong", "劳": "lao", "逄": "pang", "姬": "ji", "申": "shen",
    "扶": "fu", "堵": "du", "冉": "ran", "宰": "zai", "郦": "li", "雍": "yong",
    "却": "que", "璩": "qu", "桑": "sang", "桂": "gui", "濮": "pu", "牛": "niu",
    "寿": "shou", "通": "tong", "边": "bian", "扈": "hu", "燕": "yan", "冀": "ji",
    "郏": "jia", "浦": "pu", "尚": "shang", "农": "nong", "温": "wen", "别": "bie",
    "庄": "zhuang", "晏": "yan", "柴": "chai", "瞿": "qu", "阎": "yan", "充": "chong",
    "慕": "mu", "连": "lian", "茹": "ru", "习": "xi", "宦": "huan", "艾": "ai",
    "鱼": "yu", "容": "rong", "向": "xiang", "古": "gu", "易": "yi", "慎": "shen",
    "戈": "ge", "廖": "liao", "庾": "yu", "终": "zhong", "暨": "ji", "居": "ju",
    "衡": "heng", "步": "bu", "都": "du", "耿": "geng", "满": "man", "弘": "hong",
    "匡": "kuang", "国": "guo", "文": "wen", "寇": "kou", "广": "guang", "禄": "lu",
    "阙": "que", "东": "dong", "欧": "ou", "沃": "wo", "利": "li", "蔚": "wei",
    "越": "yue", "隆": "long", "师": "shi", "巩": "gong", "聂": "nie", "晁": "chao",
    "勾": "gou", "敖": "ao", "融": "rong", "冷": "leng", "辛": "xin", "阚": "kan",
    "那": "na", "简": "jian", "饶": "rao", "空": "kong", "曾": "zeng", "沙": "sha",
    "养": "yang", "鞠": "ju", "须": "xu", "丰": "feng", "巢": "chao", "关": "guan",
    "蒯": "kuai", "相": "xiang", "查": "zha", "后": "hou", "荆": "jing", "红": "hong",
    "游": "you", "竺": "zhu", "权": "quan", "逯": "lu", "盖": "ge", "益": "yi",
    "桓": "huan", "公": "gong",
    "伟": "wei", "芳": "fang", "娜": "na", "敏": "min", "静": "jing", "磊": "lei",
    "军": "jun", "洋": "yang", "勇": "yong", "艳": "yan", "杰": "jie", "娟": "juan",
    "涛": "tao", "超": "chao", "秀": "xiu", "英": "ying", "霞": "xia", "刚": "gang",
    "辉": "hui", "力": "li", "建": "jian", "玉": "yu", "兰": "lan", "海": "hai",
    "志": "zhi", "丽": "li", "斌": "bin", "庆": "qing", "雪": "xue", "婷": "ting",
    "浩": "hao", "宇": "yu", "子": "zi", "涵": "han", "雨": "yu", "欣": "xin",
    "然": "ran", "思": "si", "远": "yuan", "嘉": "jia", "怡": "yi", "梓": "zi",
    "萱": "xuan", "一": "yi", "诺": "nuo", "俊": "jun", "睿": "rui", "泽": "ze",
    "晨": "chen", "可": "ke", "若": "ruo", "曦": "xi", "沐": "mu", "宸": "chen",
    "诗": "shi", "轩": "xuan", "雅": "ya", "梦": "meng", "琪": "qi", "源": "yuan",
    "立": "li", "诚": "cheng", "美": "mei", "琳": "lin", "豪": "hao", "妍": "yan",
    "鹏": "peng", "博": "bo", "语": "yu", "嫣": "yan", "淑": "shu", "佳": "jia",
    "永": "yong", "瑞": "rui", "晓": "xiao", "峰": "feng", "珍": "zhen", "天": "tian",
    "翊": "yi", "辰": "chen", "桐": "tong", "鑫": "xin", "航": "hang", "昊": "hao",
    "铭": "ming", "彤": "tong", "晴": "qing", "菲": "fei", "慧": "hui",
    "丹": "dan", "楠": "nan", "晶": "jing", "萌": "meng", "帆": "fan",
}


def to_pinyin(name: str) -> str:
    """将中文姓名转为拼音串（ASCII 字母数字保留小写；pypinyin 不可用时走降级字典）。"""
    s = str(name or "")
    if _PYPINYIN_AVAILABLE:
        out = []
        for part in lazy_pinyin(s, style=_PinyinStyle.NORMAL):
            cleaned = re.sub(r"[^a-z0-9]", "", part.lower())
            if cleaned:
                out.append(cleaned)
    else:
        out = []
        for ch in s:
            if ch in _PINYIN:
                out.append(_PINYIN[ch])
            elif ch.isascii() and ch.isalnum():
                out.append(ch.lower())
    return "".join(out)


def generate_username(name: str, exists) -> str:
    """生成 拼音#序号 形式的唯一用户名；exists(candidate)->bool 用于唯一性校验。"""
    base = to_pinyin(name).strip() or "client"
    for _ in range(100):
        candidate = f"{base}#{secrets.randbelow(100000):05d}"
        if not exists(candidate):
            return candidate
    raise RuntimeError("无法生成唯一用户名")


# 去除易混淆字符（0/O、1/l/I），便于客服向客户口述/转发
_SAFE_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 12) -> str:
    """生成随机初始密码（字母+数字，去除易混淆字符）。"""
    return "".join(secrets.choice(_SAFE_CHARS) for _ in range(length))
