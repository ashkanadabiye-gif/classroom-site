import re
import unicodedata


def norm_name(s):
    """نام را برای مقایسه یکسان می‌کند: ي/ك عربی، نیم‌فاصله، اعراب، فاصله‌های اضافی و حروف بزرگ/کوچک."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    s = re.sub("[\u064B-\u065F\u0640]", "", s)
    return " ".join(s.split()).casefold()
