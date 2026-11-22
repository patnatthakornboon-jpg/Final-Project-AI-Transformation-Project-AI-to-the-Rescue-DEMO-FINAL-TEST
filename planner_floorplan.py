"""สร้างผังพื้นที่แบบ schematic (SVG) จากผลของ engine — ขนาดสัดส่วนตามพื้นที่จริง

SVG มีพื้นหลังขาวในตัว จึงอ่านได้เหมือนกันทั้งธีมสว่าง/มืด และเมื่อใช้เป็นรูปใน Streamlit
มีเวอร์ชัน JavaScript คู่ขนานใน assets/planner.js (tests/compare_js ตรวจให้ว่าผังและป้ายชื่อตรงกัน)
"""
import math
from xml.sax.saxutils import escape

BANDS = (("stage", "av_booth", "lounge"), ("seating", "dance_floor"),
         ("registration", "buffet", "photo_corner", "booths"))
COLORS = {
    "seating": "#cfe3f7", "stage": "#f5c26b", "av_booth": "#d9c2f0", "lounge": "#f3b8c8",
    "dance_floor": "#b9e6d3", "registration": "#ffe08a", "buffet": "#f7b88b",
    "photo_corner": "#c8d8f5", "booths": "#d4e8a8",
}
SHORT = {"seating": "ที่นั่ง", "stage": "เวที", "av_booth": "AV", "lounge": "VIP", "dance_floor": "ฟลอร์เต้น",
         "registration": "ลงทะเบียน", "buffet": "อาหาร", "photo_corner": "ถ่ายภาพ", "booths": "บูธ"}
ASPECT = 1.5      # กว้าง:ลึก
CHAR_W = 0.6      # ความกว้างตัวอักษรโดยประมาณ (เท่าของขนาดฟอนต์) ใช้ตัดสินว่าชื่อพอดีกล่องหรือไม่
MIN_LABEL_H = 30      # สูงกว่านี้ (px): ป้าย 2 บรรทัด
MIN_ONE_LINE_H = 16   # สูงถึงนี้: ป้าย 1 บรรทัด / เตี้ยกว่านี้ไม่เขียนป้าย (ยังมี tooltip)


def short_name(name):
    return name.split("(")[0].strip()


def zone_label(zone_id, name, width_px, sqm=None):
    """เลือกป้ายที่พอดีกล่อง คืน (ข้อความ, ขนาดฟอนต์) หรือ (None, None) ถ้าไม่พอดีเลย
    sqm=None -> โหมด 2 บรรทัด (ชื่อ + พื้นที่แยกบรรทัด) ลองชื่อเต็ม 13px -> 11px -> ชื่อย่อ 11px -> 9px
    sqm=ตัวเลข -> โหมด 1 บรรทัด (กล่องเตี้ย) ชื่อและพื้นที่อยู่บรรทัดเดียวกัน"""
    short = SHORT.get(zone_id, name)
    if sqm is None:
        options = ((name, 13), (name, 11), (short, 11), (short, 9))
    else:
        options = ((f"{name} {sqm:g} ตร.ม.", 11), (f"{name} {sqm:g}", 11), (f"{short} {sqm:g}", 11),
                   (f"{short} {sqm:g}", 9), (short, 9))
    for text, fs in options:
        if len(text) * CHAR_W * fs <= width_px - 6:
            return text, fs
    return None, None


def layout_zones(zones, total_area, subtotal):
    """จัดโซนเป็นแถบ (หน้างาน -> กลาง -> บริการ) คืน (กว้าง, ลึก, สี่เหลี่ยมของแต่ละโซน) หน่วยเมตร"""
    width = math.sqrt(total_area * ASPECT)
    depth = total_area / width
    scale = math.sqrt(subtotal / total_area)
    iw, idp = width * scale, depth * scale
    ox, oy = (width - iw) / 2, (depth - idp) / 2
    by_id = {z["id"]: z for z in zones}
    rects, y = [], oy
    for band in BANDS:
        members = [by_id[i] for i in band if i in by_id]
        if not members:
            continue
        band_area = sum(z["sqm"] for z in members)
        h = idp * band_area / subtotal
        x = ox
        for z in members:
            w = iw * z["sqm"] / band_area
            rects.append({"id": z["id"], "name": short_name(z["name_th"]), "sqm": z["sqm"],
                          "x": x, "y": y, "w": w, "h": h})
            x += w
        y += h
    return width, depth, rects


def scene(plan, px_width=680):
    """คำนวณสิ่งที่จะวาด (หน่วยพิกเซล) — แยกออกมาเพื่อให้ทดสอบเทียบกับ JavaScript ได้"""
    a = plan["area"]
    sub = sum(z["sqm"] for z in a["zones"])
    width, depth, rects = layout_zones(a["zones"], a["total"], sub)
    k = px_width / width
    boxes = []
    for r in rects:
        w_px, h_px = r["w"] * k, r["h"] * k
        if h_px > MIN_LABEL_H:
            lines, (text, fs) = 2, zone_label(r["id"], r["name"], w_px)
        elif h_px >= MIN_ONE_LINE_H:
            lines, (text, fs) = 1, zone_label(r["id"], r["name"], w_px, r["sqm"])
        else:
            lines, text, fs = 0, None, None
        boxes.append({"id": r["id"], "name": r["name"], "sqm": r["sqm"], "x": 20 + r["x"] * k,
                      "y": 20 + r["y"] * k, "w": w_px, "h": h_px, "label": text, "fs": fs, "lines": lines})
    return width, depth, k, boxes


def floorplan_svg(plan, px_width=680, fixed_size=False):
    """fixed_size=True: ใส่ width/height เป็นพิกเซล (จำเป็นเมื่อนำ SVG ไปใช้เป็นรูป เช่น st.image)"""
    a = plan["area"]
    width, depth, k, boxes = scene(plan, px_width)
    h_px = depth * k
    vb_w, vb_h = px_width + 40, f"{h_px + 70:.0f}"
    size = f'width="{vb_w}" height="{vb_h}"' if fixed_size else 'width="100%"'
    font = "Sarabun,'Noto Sans Thai','Segoe UI',Tahoma,sans-serif"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" {size} font-family="{font}" '
        f'role="img" aria-label="ผังพื้นที่จัดงาน">',
        f'<rect width="{vb_w}" height="{vb_h}" fill="#ffffff"/>',
        f'<rect x="20" y="20" width="{px_width}" height="{h_px:.1f}" rx="6" fill="#fafafa" '
        f'stroke="#555" stroke-width="2" stroke-dasharray="6 4"/>',
    ]
    for b in boxes:
        tip = escape(f'{b["name"]} {b["sqm"]:g} ตร.ม.')
        parts.append(f'<rect x="{b["x"]:.1f}" y="{b["y"]:.1f}" width="{b["w"]:.1f}" height="{b["h"]:.1f}" '
                     f'fill="{COLORS.get(b["id"], "#ddd")}" stroke="#7a7a7a" stroke-width="1"><title>{tip}</title></rect>')
        if b["label"]:
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            if b["lines"] == 2:
                parts.append(f'<text x="{cx:.1f}" y="{cy - 2:.1f}" text-anchor="middle" font-size="{b["fs"]}" '
                             f'fill="#222">{escape(b["label"])}</text>')
                parts.append(f'<text x="{cx:.1f}" y="{cy + 14:.1f}" text-anchor="middle" font-size="{min(12, b["fs"])}" '
                             f'fill="#444">{b["sqm"]:g} ตร.ม.</text>')
            else:
                parts.append(f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" font-size="{b["fs"]}" '
                             f'fill="#222">{escape(b["label"])}</text>')
    parts.append(f'<text x="{20 + px_width / 2:.1f}" y="{h_px + 40:.1f}" text-anchor="middle" '
                 f'font-size="13" fill="#333">▲ ทางเข้า · พื้นที่รวม {a["total"]:g} ตร.ม. '
                 f'(≈ {width:.1f} × {depth:.1f} ม., รวมทางสัญจร {a["circulation"]:g} ตร.ม.)</text>')
    parts.append("</svg>")
    return "".join(parts)
