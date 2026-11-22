"""AI Event & Venue Space Planner — Streamlit app

รัน:  streamlit run app.py
ใส่ API key (ไม่บังคับ) ที่แถบด้านข้าง หรือตั้ง GEMINI_API_KEY / OPENAI_API_KEY ก่อนรัน
ถ้าไม่มี key ระบบยังทำงานครบด้วย rule engine + ตัวอ่านโจทย์แบบ rule-based
"""
import csv
import io

import streamlit as st

import planner_ai as ai
from planner_engine import TIER_ORDER, get_data, plan
from planner_floorplan import floorplan_svg

D = get_data()
EVENTS, LAYOUTS, COMPS, TIERS = D["events"], D["layouts"], D["components"], D["tiers"]
EXAMPLES = [
    "งานสัมมนา 100 คน มีเวทีและซุ้มอาหาร งบ 5 หมื่นบาท",
    "งานแต่งงาน 250 คน โต๊ะจีน ระดับพรีเมียม มีฟลอร์เต้น",
    "Networking cocktail party for 80 people with buffet, budget 40k",
]

st.set_page_config(page_title="AI Event & Venue Space Planner", page_icon="🗺️", layout="wide")


# ---------- state ----------
def _secret(name):
    try:
        return st.secrets.get(name)
    except Exception:  # ไม่มีไฟล์ secrets
        return None


def _reset_for_event():
    ev = EVENTS[st.session_state.event_type]
    st.session_state.layout = ev["default_layout"]
    st.session_state.components = list(ev["default_components"])
    st.session_state.people = min(max(st.session_state.people, ev["min_people"]), ev["max_people"])


def _init():
    if "event_type" not in st.session_state:
        st.session_state.event_type = "seminar"
        st.session_state.people = 100
        st.session_state.tier = "standard"
        st.session_state.budget = 0
        st.session_state.intake_text = EXAMPLES[0]
        st.session_state.intake_info = None
        st.session_state.advice = None
        _reset_for_event()


def _apply_requirements(req):
    notices = []
    if req["event_type"]:
        st.session_state.event_type = req["event_type"]
    ev = EVENTS[st.session_state.event_type]
    st.session_state.layout = req["layout"] if req["layout"] in ev["allowed_layouts"] else ev["default_layout"]
    if req["layout"] and req["layout"] not in ev["allowed_layouts"]:
        notices.append(f"{ev['name_th']} ไม่รองรับ layout ที่ระบุ จึงใช้ค่าเริ่มต้นแทน")
    st.session_state.components = list(dict.fromkeys(ev["default_components"] + req["components"]))
    if req["people"]:
        n = min(max(req["people"], ev["min_people"]), ev["max_people"])
        if n != req["people"]:
            notices.append(f"{ev['name_th']} รองรับ {ev['min_people']:,}-{ev['max_people']:,} คน จึงปรับจาก {req['people']:,} เป็น {n:,}")
        st.session_state.people = n
    else:
        st.session_state.people = min(max(st.session_state.people, ev["min_people"]), ev["max_people"])
    st.session_state.tier = req["tier"] or "standard"  # โจทย์ใหม่ที่ไม่ระบุระดับงบ ต้องไม่ค้างค่าเก่า
    st.session_state.budget = req["budget_thb"] or 0
    return notices


def _on_read_text():
    key, prov = _ai_settings()
    req, info = ai.extract_requirements(st.session_state.intake_text, api_key=key, provider=prov,
                                        model=st.session_state.get("model") or None)
    info["notices"] = _apply_requirements(req)
    info["found"] = {k: v for k, v in req.items() if v not in (None, [])}
    st.session_state.intake_info = info
    st.session_state.advice = None


def _ai_settings():
    key = (st.session_state.get("api_key") or _secret("GEMINI_API_KEY") or _secret("OPENAI_API_KEY") or "").strip() or None
    prov = st.session_state.get("provider", "auto")
    return key, (None if prov == "auto" else prov)


def _csv_bytes(p):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["หมวด", "รายการ", "จำนวน", "หน่วย", "ราคาต่อหน่วย (บาท)", "รวม (บาท)"])
    for e in p["equipment"]:
        w.writerow([e["category_th"], e["name_th"], e["qty"], e["unit"], e["unit_price"], e["total_price"]])
    w.writerow(["", "รวมทั้งหมด", "", "", "", p["total_cost"]])
    return buf.getvalue().encode("utf-8-sig")  # BOM เพื่อให้ Excel อ่านภาษาไทยถูก


_init()

# ---------- sidebar ----------
with st.sidebar:
    st.header("ตั้งค่า AI")
    st.caption("ไม่ใส่ key ก็ใช้งานได้ครบ — ระบบจะอ่านโจทย์และเขียนคำแนะนำด้วยกฎที่เตรียมไว้")
    st.text_input("API key (Gemini หรือ OpenAI)", type="password", key="api_key",
                  help="ใช้เฉพาะในเซสชันนี้ ไม่ถูกบันทึกลงไฟล์")
    st.selectbox("ผู้ให้บริการ", ["auto", "gemini", "openai"], key="provider",
                 help="auto: key ขึ้นต้นด้วย sk- = OpenAI ที่เหลือ = Gemini")
    st.text_input("ชื่อโมเดล (เว้นว่างเพื่อใช้ค่าเริ่มต้น)", key="model")
    _k, _p = _ai_settings()
    _prov, _ = ai.available_provider(_k, _p)
    if _prov:
        st.success(f"โหมด AI: {_prov}")
        if st.button("ทดสอบการเชื่อมต่อ AI"):
            with st.spinner("กำลังตรวจ key และโมเดล..."):
                _ok, _msg = ai.test_connection(_k, _p, st.session_state.get("model") or None)
            if _ok:
                st.success(_msg)
            else:
                st.error(_msg)
    else:
        st.info("โหมด AI: ปิด (ใช้ rule-based)")
    st.divider()
    st.caption("ตัวเลขพื้นที่ จำนวนอุปกรณ์ และราคา คำนวณจากไฟล์ data/ ด้วย rule engine "
               "ไม่ได้ให้ AI เดา เพื่อให้ตรวจสอบย้อนกลับได้ ราคาเป็นค่าประมาณเพื่อการวางแผน")

# ---------- header + intake ----------
st.title("AI Event & Venue Space Planner")
st.write("บอกโจทย์ของงาน แล้วได้ผังพื้นที่ รายการอุปกรณ์ และงบประมาณโดยประมาณทันที")

st.text_area("เล่าโจทย์ของงานด้วยภาษาของคุณ", key="intake_text", height=80,
             help="เช่น ประเภทงาน จำนวนคน สิ่งที่ต้องมี และงบประมาณ")
c1, c2 = st.columns([1, 3])
c1.button("อ่านโจทย์แล้วกรอกฟอร์ม", on_click=_on_read_text, type="primary")
c2.caption("ตัวอย่าง: " + " | ".join(EXAMPLES))

info = st.session_state.intake_info
if info:
    how = "AI" if info["source"] == "llm" else "ตัวอ่านโจทย์แบบ rule-based"
    st.info(f"อ่านโดย{how}: พบ {len(info['found'])} ข้อมูล — ตรวจและแก้ในฟอร์มด้านล่างได้")
    if info.get("error"):
        st.warning(f"เรียก AI ไม่สำเร็จ ({info['error']}) จึงใช้ตัวอ่านโจทย์แบบ rule-based แทน")
    for n in info.get("notices", []):
        st.warning(n)

# ---------- form ----------
ev = EVENTS[st.session_state.event_type]
st.session_state.people = min(max(st.session_state.people, ev["min_people"]), ev["max_people"])
if st.session_state.layout not in ev["allowed_layouts"]:
    st.session_state.layout = ev["default_layout"]

f1, f2, f3 = st.columns(3)
f1.selectbox("ประเภทงาน", list(EVENTS), format_func=lambda i: EVENTS[i]["name_th"],
             key="event_type", on_change=_reset_for_event)
f2.number_input("จำนวนผู้เข้าร่วม (คน)", min_value=ev["min_people"], max_value=ev["max_people"],
                step=10, key="people")
f3.radio("ระดับงบ", TIER_ORDER, format_func=lambda t: TIERS[t]["name_th"], key="tier", horizontal=True)
g1, g2, g3 = st.columns([1, 2, 1])
g1.selectbox("รูปแบบการจัดที่นั่ง", ev["allowed_layouts"], format_func=lambda i: LAYOUTS[i]["name_th"], key="layout")
g2.multiselect("ส่วนประกอบของงาน", list(COMPS), format_func=lambda i: COMPS[i]["name_th"], key="components")
g3.number_input("งบประมาณ (บาท) — 0 = ไม่ระบุ", min_value=0, step=5000, key="budget")

# ---------- plan ----------
try:
    p = plan(st.session_state.event_type, int(st.session_state.people), st.session_state.tier,
             st.session_state.layout, list(st.session_state.components),
             st.session_state.budget or None)
except ValueError as e:
    st.error(str(e))
    st.stop()

a, b = p["area"], p["budget"]
m = st.columns(5)
m[0].metric("พื้นที่รวม (ตร.ม.)", f"{a['total']:g}")
m[1].metric("สถานที่ขั้นต่ำ (ตร.ม.)", f"{a['recommended_min_venue']:,}")
m[2].metric("งบโดยประมาณ (บาท)", f"{p['total_cost']:,}")
m[3].metric("ต่อคน (บาท)", f"{p['cost_per_person']:,}")
m[4].metric("อุปกรณ์ (รายการ)", f"{len(p['equipment'])}")
if b:
    if b["within_budget"]:
        st.success(f"อยู่ในงบ {b['budget_thb']:,.0f} บาท เหลือประมาณ {b['difference']:,} บาท")
    elif b["recommended_tier"]:
        name = TIERS[b["recommended_tier"]]["name_th"]
        st.warning(f"เกินงบประมาณ {-b['difference']:,} บาท — ระดับ '{name}' จะอยู่ในงบ")
    else:
        st.error(f"เกินงบประมาณ {-b['difference']:,} บาท และแม้ระดับประหยัดก็ยังเกิน ลองลดจำนวนคนหรือส่วนประกอบ")

tab_plan, tab_list, tab_tier, tab_ai = st.tabs(["ผังพื้นที่", "รายการอุปกรณ์", "เทียบระดับงบ", "คำแนะนำ"])

with tab_plan:
    st.image(floorplan_svg(p, fixed_size=True))
    st.caption("ผังแบบ schematic ขนาดสัดส่วนตามพื้นที่คำนวณ ไม่ใช่แบบก่อสร้าง ใช้ประกอบการเลือกสถานที่")
    st.table([{"โซน": z["name_th"], "พื้นที่ (ตร.ม.)": z["sqm"]} for z in a["zones"]]
             + [{"โซน": f"ทางสัญจร ({a['circulation_ratio'] * 100:.0f}%)", "พื้นที่ (ตร.ม.)": a["circulation"]},
                {"โซน": "รวม", "พื้นที่ (ตร.ม.)": a["total"]}])
    s = p["safety"]
    st.write(f"**ความปลอดภัยเบื้องต้น:** ทางออกอย่างน้อย {s['exits']} จุด กว้างรวมไม่ต่ำกว่า {s['exit_width_total_m']:g} ม. · "
             f"ห้องน้ำประมาณ {s['toilets']} ที่ · เพดานสูงไม่ต่ำกว่า {s['ceiling_height_min_m']:g} ม. · "
             f"{s['seat_unit_name']} {s['seat_units']:,} ชุด")

with tab_list:
    for grp in p["equipment_groups"]:
        st.subheader(f"{grp['name_th']} — {grp['subtotal']:,} บาท")
        st.table([{"รายการ": e["name_th"], "จำนวน": e["qty"], "หน่วย": e["unit"],
                   "ราคา/หน่วย": f"{e['unit_price']:,}", "รวม (บาท)": f"{e['total_price']:,}"} for e in grp["items"]])
    st.markdown(f"### รวมทั้งหมด {p['total_cost']:,} บาท")
    st.download_button("ดาวน์โหลด Checklist (CSV)", _csv_bytes(p), file_name="event_checklist.csv", mime="text/csv")

with tab_tier:
    st.table([{"ระดับงบ": o["name_th"] + (" (ที่เลือก)" if o["tier"] == p["inputs"]["tier"] else ""),
               "พื้นที่รวม (ตร.ม.)": o["total_area"], "งบรวม (บาท)": f"{o['total_cost']:,}",
               "ต่อคน (บาท)": f"{o['cost_per_person']:,}", "รายการอุปกรณ์": o["items"]} for o in p["tier_options"]])
    st.caption("ระดับสูงขึ้น = พื้นที่ต่อคนโปร่งขึ้น และมีอุปกรณ์เพิ่ม (เช่น ไฟเวที ถ่ายทอดสด ดอกไม้)")

with tab_ai:
    _k, _p = _ai_settings()
    _prov, _ = ai.available_provider(_k, _p)
    sig = (p["inputs"]["event_type"], p["inputs"]["people"], p["inputs"]["tier"], p["inputs"]["layout"],
           tuple(p["inputs"]["components"]), p["inputs"]["budget_thb"])
    if not _prov:
        text, src = ai.advise(p)
        st.markdown(text)
        st.caption("คำแนะนำจากแม่แบบ (ไม่ได้ใช้ AI) — ใส่ API key ที่แถบด้านข้างเพื่อให้ AI เขียนคำแนะนำเฉพาะงานนี้")
    else:
        if st.button("ให้ AI เขียนคำแนะนำสำหรับแผนนี้", type="primary"):
            with st.spinner("กำลังให้ AI วิเคราะห์..."):
                text, src = ai.advise(p, st.session_state.intake_text, api_key=_k, provider=_p,
                                      model=st.session_state.get("model") or None)
            st.session_state.advice = {"sig": sig, "text": text, "src": src}
        adv = st.session_state.advice
        if adv and adv["sig"] == sig:
            st.markdown(adv["text"])
            if adv["src"]["error"]:
                st.warning(f"เรียก AI ไม่สำเร็จ ({adv['src']['error']}) จึงแสดงคำแนะนำแบบแม่แบบแทน")
            else:
                st.caption(f"เขียนโดย AI ({adv['src']['provider']}) จากตัวเลขที่คำนวณแล้วเท่านั้น")
        else:
            st.write("กดปุ่มเพื่อให้ AI วิเคราะห์แผนปัจจุบัน")
