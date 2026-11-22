"""ตัวอ่านโจทย์แบบ rule-based (ไม่ใช้ AI/อินเทอร์เน็ต) ใช้เป็นทั้ง fallback และ baseline ในการวัดผล

มีเวอร์ชัน JavaScript คู่ขนานใน assets/planner.js — แก้ที่นี่ต้องแก้ที่นั่นด้วย (tests/compare_js ตรวจให้)
"""
import re

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

EVENT_KW = {
    "wedding_banquet": ["แต่งงาน", "มงคลสมรส", "โต๊ะจีน", "เลี้ยงโต๊ะ", "งานเลี้ยง", "wedding", "banquet", "gala"],
    "product_launch": ["เปิดตัว", "launch"],
    "exhibition": ["นิทรรศการ", "แสดงสินค้า", "exhibition", "expo", "trade show"],
    "networking": ["เน็ตเวิร์ก", "networking", "สังสรรค์", "ปาร์ตี้", "party", "mixer"],
    "workshop": ["เวิร์กชอป", "เวิร์คชอป", "เวิร์กช็อป", "เวิร์คช็อป", "workshop", "อบรม", "training"],
    "conference": ["คอนเฟอเรนซ์", "ประชุมวิชาการ", "conference", "convention"],
    "seminar": ["สัมมนา", "seminar", "ประชุม", "meeting"],
}
LAYOUT_KW = {
    "theater": ["เธียเตอร์", "theater", "theatre", "แถวเก้าอี้"],
    "classroom": ["ห้องเรียน", "classroom", "โต๊ะยาว"],
    "banquet": ["โต๊ะจีน", "โต๊ะกลม", "banquet", "round table"],
    "cabaret": ["คาบาเรต์", "คาบาเร่ต์", "cabaret"],
    "cocktail": ["ค็อกเทล", "cocktail", "ยืนสังสรรค์", "standing"],
    "u_shape": ["ตัวยู", "u-shape", "u shape", "ushape"],
}
COMPONENT_KW = {
    "stage": ["เวที", "stage"],
    "buffet": ["บุฟเฟ", "อาหาร", "buffet", "catering", "เครื่องดื่ม"],
    "registration": ["ลงทะเบียน", "registration", "เช็คอิน", "check-in"],
    "av_booth": ["av booth", "ห้องควบคุม", "ควบคุมเสียง", "ห้องมิกซ์"],
    "photo_corner": ["ถ่ายรูป", "ถ่ายภาพ", "photo", "โฟโต้"],
    "booths": ["บูธ", "booth"],
    "lounge": ["vip", "รับรอง", "เลานจ์", "lounge"],
    "dance_floor": ["เต้น", "dance"],
}
TIER_KW = {
    "basic": ["ประหยัด", "basic", "ราคาถูก", "cheap"],
    "standard": ["มาตรฐาน", "standard", "ปานกลาง"],
    "premium": ["พรีเมียม", "premium", "หรูหรา", "luxury", "ระดับหรู"],
}
_MULT = {"พัน": 1_000, "หมื่น": 10_000, "แสน": 100_000, "ล้าน": 1_000_000, "k": 1_000, "m": 1_000_000}
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
# k/m ต้องไม่ตามด้วยตัวอักษร (กัน "50 more" -> 50 ล้าน, "40 kids" -> 40 พัน)
_UNIT = r"(พัน|หมื่น|แสน|ล้าน|k(?![a-z])|m(?![a-z]))?"

PEOPLE_RE = re.compile(_NUM + r"\s*(?:คน|ท่าน|ที่นั่ง|people|persons|person|pax|guests|attendees|seats)", re.I)
BUDGET_RES = [
    re.compile(r"(?:งบประมาณ|งบ|budget)[^\d]{0,12}" + _NUM + r"\s*" + _UNIT, re.I),
    re.compile(_NUM + r"\s*" + _UNIT + r"\s*(?:บาท|baht|thb|฿)", re.I),
    re.compile(r"฿\s*" + _NUM + r"\s*" + _UNIT, re.I),
]


def _first_by_position(text, table):
    """คืน id ของคำที่ปรากฏก่อนสุดในข้อความ (ถ้าเริ่มตำแหน่งเดียวกัน เลือกคำที่ยาวกว่า)"""
    best = None
    for key, words in table.items():
        for w in words:
            pos = text.find(w)
            if pos >= 0:
                cand = (pos, -len(w), key)
                if best is None or cand < best:
                    best = cand
    return best[2] if best else None


def _to_number(num, unit):
    v = float(num.replace(",", ""))
    if unit:
        v *= _MULT[unit.lower()]
    return int(round(v))


def parse_request(text):
    """อ่านข้อความอิสระ -> dict ของข้อกำหนด (ค่าที่ไม่พบเป็น None / [])"""
    t = (text or "").translate(_THAI_DIGITS).lower()
    out = {"event_type": None, "people": None, "tier": None, "layout": None,
           "components": [], "budget_thb": None}

    m = PEOPLE_RE.search(t)
    if m:
        out["people"] = int(float(m.group(1).replace(",", "")))

    for rx in BUDGET_RES:
        m = rx.search(t)
        if m:
            val = _to_number(m.group(1), m.group(2))
            if val > 0:
                out["budget_thb"] = val
                break

    out["event_type"] = _first_by_position(t, EVENT_KW)
    out["layout"] = _first_by_position(t, LAYOUT_KW)
    out["tier"] = _first_by_position(t, TIER_KW)

    t_comp = t.replace("av booth", " ")
    for cid, words in COMPONENT_KW.items():
        hay = t if cid == "av_booth" else t_comp
        if any(w in hay for w in words):
            out["components"].append(cid)
    return out
