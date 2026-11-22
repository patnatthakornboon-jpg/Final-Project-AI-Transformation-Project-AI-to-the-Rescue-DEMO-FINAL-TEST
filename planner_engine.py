"""Rule engine: คำนวณพื้นที่ รายการอุปกรณ์ และงบประมาณจากไฟล์ data/*.json

หลักการ: LLM อ่านโจทย์/อธิบายผล ส่วนตัวเลขทั้งหมดคำนวณที่นี่เพื่อความถูกต้องและตรวจสอบย้อนกลับได้
มีเวอร์ชัน JavaScript คู่ขนานใน assets/planner.js (ทดสอบให้ผลตรงกันใน tests/)
"""
import json
import math
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent  # โครงสร้างแบบแบน: ไฟล์ .json อยู่ข้างไฟล์นี้
TIER_ORDER = ("basic", "standard", "premium")
TIER_RANK = {t: i + 1 for i, t in enumerate(TIER_ORDER)}


def _ceil(x):
    """ceil ที่กันความคลาดเคลื่อนทศนิยม (เช่น 100*1.05 = 105.00000000000001 ต้องได้ 105 ไม่ใช่ 106)"""
    return math.ceil(round(x, 6))


def _r1(x):
    return round(x + 1e-9, 1)


@lru_cache(maxsize=4)
def _load(data_dir):
    def rd(name):
        with open(Path(data_dir) / name, encoding="utf-8") as f:
            return json.load(f)

    rules = rd("venue_rules.json")
    equip = rd("equipment.json")
    events = rd("event_types.json")
    return {
        "layouts": {x["id"]: x for x in rd("layouts.json")["layouts"]},
        "rules": rules,
        "components": {x["id"]: x for x in rules["components"]},
        "categories": {x["id"]: x["name_th"] for x in equip["categories"]},
        "category_order": [x["id"] for x in equip["categories"]],
        "items": equip["items"],
        "events": {x["id"]: x for x in events["event_types"]},
        "tiers": {x["id"]: x for x in events["budget_tiers"]},
        "prompts": rd("prompts.json"),
    }


def get_data(data_dir=DATA_DIR):
    return _load(str(data_dir))


def component_area(rule, people):
    t = rule["type"]
    if t == "fixed":
        return float(rule["sqm"])
    if t == "per_n_people":
        units = max(rule["min_units"], _ceil(people / rule["n"]))
        return float(units * rule["sqm_per_unit"])
    for tier in rule["tiers"]:
        if people <= tier["max_people"]:
            return float(tier["sqm"])
    raise ValueError("tiered rule ไม่ครอบคลุมจำนวนคนนี้")


def _item_qty(item, people, layout, total_area, comp_areas):
    r = item["rule"]
    t = r["type"]
    if t == "fixed":
        return r["qty"]
    if t == "per_n_people":
        q = max(r.get("min_qty", 1), _ceil(people / r["n"] * (1 + r.get("spare_ratio", 0))))
        if r.get("round_even") and q % 2:
            q += 1
        return q
    if t == "per_layout_unit":
        return _ceil(people / layout["seats_per_unit"])
    if t == "per_total_area":
        return max(r.get("min_qty", 1), _ceil(total_area / r["sqm_per_unit"]))
    if t == "per_component_area":
        return max(1, _ceil(comp_areas[r["component"]] / r["sqm_per_unit"]))
    raise ValueError(f"rule type ไม่รู้จัก: {t}")


def _calc(d, ev, people, tier, layout_id, comp_ids):
    layout = d["layouts"][layout_id]
    dens = d["tiers"][tier]["layout_density"]
    rules = d["rules"]

    seating = people * layout["sqm_per_person"][dens]
    comp_areas = {c: component_area(d["components"][c]["rule"], people) for c in comp_ids}
    subtotal = seating + sum(comp_areas.values())
    circ_ratio = rules["circulation_ratio"][dens]
    circulation = subtotal * circ_ratio
    total = subtotal + circulation

    equipment, cost = [], 0
    for it in d["items"]:
        if TIER_RANK[it["tier_min"]] > TIER_RANK[tier]:
            continue
        if "requires_layouts" in it and layout_id not in it["requires_layouts"]:
            continue
        if any(c not in comp_areas for c in it.get("requires_components", [])):
            continue
        qty = _item_qty(it, people, layout, total, comp_areas)
        line = qty * it["price_thb"]
        cost += line
        equipment.append({
            "id": it["id"], "name_th": it["name_th"], "category": it["category"],
            "category_th": d["categories"][it["category"]], "qty": qty, "unit": it["unit"],
            "unit_price": it["price_thb"], "total_price": line,
        })
    return {
        "layout": layout, "density": dens, "seating": seating, "comp_areas": comp_areas,
        "subtotal": subtotal, "circ_ratio": circ_ratio, "circulation": circulation,
        "total": total, "equipment": equipment, "cost": cost,
    }


def plan(event_type, people, tier="standard", layout=None, components=None,
         budget_thb=None, data=None):
    """สร้างแผนจัดงาน คืน dict ที่ UI นำไปแสดงผลได้ทันที

    ผิดเงื่อนไข (จำนวนคน/layout/ส่วนประกอบไม่ถูกต้อง) -> ValueError พร้อมข้อความภาษาไทย
    """
    d = data or get_data()
    ev = d["events"].get(event_type)
    if ev is None:
        raise ValueError(f"ไม่รู้จักประเภทงาน '{event_type}'")
    if isinstance(people, bool) or not isinstance(people, int):
        raise ValueError("จำนวนคนต้องเป็นจำนวนเต็ม")
    if not (ev["min_people"] <= people <= ev["max_people"]):
        raise ValueError(
            f"{ev['name_th']} รองรับ {ev['min_people']:,}-{ev['max_people']:,} คน (ได้ {people:,})")
    if tier not in d["tiers"]:
        raise ValueError(f"ไม่รู้จักระดับงบ '{tier}'")
    layout_id = layout or ev["default_layout"]
    if layout_id not in ev["allowed_layouts"]:
        raise ValueError(f"{ev['name_th']} ไม่เหมาะกับ layout '{layout_id}' (ใช้ได้: {', '.join(ev['allowed_layouts'])})")
    comp_ids = list(dict.fromkeys(ev["default_components"] if components is None else components))
    for c in comp_ids:
        if c not in d["components"]:
            raise ValueError(f"ไม่รู้จักส่วนประกอบ '{c}'")
    if budget_thb is not None and (not isinstance(budget_thb, (int, float)) or budget_thb <= 0):
        raise ValueError("งบประมาณต้องเป็นตัวเลขมากกว่า 0")

    main = _calc(d, ev, people, tier, layout_id, comp_ids)
    rules = d["rules"]

    # ตัวเลือกทุกระดับงบ (ใช้เทียบและแนะนำเมื่องบไม่พอ)
    tier_options = []
    for t in TIER_ORDER:
        c = main if t == tier else _calc(d, ev, people, t, layout_id, comp_ids)
        tier_options.append({
            "tier": t, "name_th": d["tiers"][t]["name_th"], "total_area": _r1(c["total"]),
            "total_cost": c["cost"], "cost_per_person": round(c["cost"] / people),
            "items": len(c["equipment"]),
        })

    budget = None
    if budget_thb is not None:
        fits = [o["tier"] for o in tier_options if o["total_cost"] <= budget_thb]
        budget = {
            "budget_thb": budget_thb,
            "within_budget": main["cost"] <= budget_thb,
            "difference": round(budget_thb - main["cost"]),
            "affordable_tiers": fits,
            "recommended_tier": fits[-1] if fits else None,
        }

    zones = [{"id": "seating", "name_th": "โซนที่นั่ง", "sqm": _r1(main["seating"])}]
    zones += [{"id": c, "name_th": d["components"][c]["name_th"], "sqm": _r1(a)}
              for c, a in main["comp_areas"].items()]

    groups = []
    for cat in d["category_order"]:
        rows = [e for e in main["equipment"] if e["category"] == cat]
        if rows:
            groups.append({"category": cat, "name_th": d["categories"][cat], "items": rows,
                           "subtotal": sum(r["total_price"] for r in rows)})

    exits = max(rules["min_exits"], _ceil(people / 250))
    layout = main["layout"]
    return {
        "inputs": {"event_type": event_type, "event_name_th": ev["name_th"], "people": people,
                   "tier": tier, "tier_name_th": d["tiers"][tier]["name_th"],
                   "layout": layout_id, "layout_name_th": layout["name_th"],
                   "components": comp_ids, "budget_thb": budget_thb},
        "area": {
            "sqm_per_person": layout["sqm_per_person"][main["density"]],
            "seating": _r1(main["seating"]), "zones": zones,
            "subtotal": _r1(main["subtotal"]), "circulation_ratio": main["circ_ratio"],
            "circulation": _r1(main["circulation"]), "total": _r1(main["total"]),
            "recommended_min_venue": _ceil(main["total"]),
        },
        "equipment": main["equipment"],
        "equipment_groups": groups,
        "total_cost": main["cost"],
        "cost_per_person": round(main["cost"] / people),
        "tier_options": tier_options,
        "budget": budget,
        "safety": {
            "ceiling_height_min_m": rules["ceiling_height_min_m"],
            "exits": exits,
            "exit_width_total_m": _r1(people / 100 * rules["exit_width_m_per_100_people"]),
            "toilets": _ceil(people / rules["restroom_rule"]["people_per_toilet"]),
            "seat_units": _ceil(people / layout["seats_per_unit"]),
            "seat_unit_name": layout["unit_name"],
        },
    }
