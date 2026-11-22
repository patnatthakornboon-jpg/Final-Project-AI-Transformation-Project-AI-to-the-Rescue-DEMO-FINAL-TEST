"""ชั้น AI: ใช้ LLM (Gemini หรือ OpenAI) อ่านโจทย์ภาษาธรรมชาติ และเขียนคำแนะนำจากแผนที่คำนวณแล้ว

ออกแบบให้ใช้งานได้แม้ไม่มี API key: ถ้าเรียก LLM ไม่ได้ จะ fallback ไปตัวอ่านโจทย์แบบ rule-based
และคำแนะนำแบบแม่แบบโดยอัตโนมัติ (บอกผู้ใช้ชัดเจนว่าใช้แหล่งไหน)

ตัวแปรสภาพแวดล้อม:  GEMINI_API_KEY | GOOGLE_API_KEY | OPENAI_API_KEY
                    GEMINI_MODEL (ไม่ตั้ง = ลองโมเดลสำรองตามลำดับ GEMINI_MODELS) | OPENAI_MODEL (ค่าเริ่มต้น gpt-4o-mini)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from planner_engine import get_data
from planner_parser import parse_request

TIMEOUT_S = 12  # ต่อครั้ง ถ้านานกว่านี้ถือว่าล้มเหลวแล้วสลับโมเดลสำรอง
# ลำดับโมเดล Gemini ที่ลองเมื่อผู้ใช้ไม่ได้ระบุชื่อเอง (ตัวแรกที่ตอบได้จะถูกใช้) รุ่นที่ Google ระบุว่าใช้ฟรีอยู่ก่อน
GEMINI_MODELS = ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash")


# ---------- prompts ----------
def build_extract_prompt(data=None):
    d = data or get_data()
    ev = ", ".join(d["events"])
    ly = ", ".join(d["layouts"])
    cp = ", ".join(d["components"])
    sys_p = (d["prompts"]["extract_system"]
             .replace("{EVENT_TYPES}", ev).replace("{LAYOUTS}", ly).replace("{COMPONENTS}", cp))
    shots = "\n\n".join(
        f"Input: {x['input']}\nOutput: {json.dumps(x['output'], ensure_ascii=False)}"
        for x in d["prompts"]["extract_examples"])
    return sys_p, shots


def plan_summary_for_llm(plan):
    """ย่อแผนให้สั้นพอส่งให้ LLM (ไม่ส่งรายการอุปกรณ์ทั้งหมด)"""
    return {
        "inputs": plan["inputs"], "area": {k: plan["area"][k] for k in ("seating", "total", "circulation", "recommended_min_venue", "zones")},
        "total_cost": plan["total_cost"], "cost_per_person": plan["cost_per_person"],
        "tier_options": plan["tier_options"], "budget": plan["budget"], "safety": plan["safety"],
        "equipment_count": len(plan["equipment"]),
    }


# ---------- provider ----------
def available_provider(api_key=None, provider=None):
    """คืน (provider, key) ที่ใช้ได้ หรือ (None, None)"""
    if api_key:
        return (provider or ("openai" if api_key.startswith("sk-") else "gemini")), api_key
    g = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if g and provider in (None, "gemini"):
        return "gemini", g
    o = os.environ.get("OPENAI_API_KEY")
    if o and provider in (None, "openai"):
        return "openai", o
    return None, None


class AIError(RuntimeError):
    """เรียก AI ไม่สำเร็จ ข้อความมีเหตุผลจากผู้ให้บริการ (เช่น 'API key not valid') และไม่มี key
    status = รหัส HTTP (ถ้ามี) ใช้ตัดสินใจว่าจะลองโมเดลสำรองต่อหรือหยุด"""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _describe_http_error(e, secrets=()):
    """รหัส HTTP + สถานะ + ข้อความจริงจาก response body ของ Google/OpenAI (ตัด key ออก ไม่ยาวเกิน 300 ตัวอักษร)"""
    detail = ""
    try:
        err = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
        if isinstance(err, dict):
            detail = " ".join(x for x in (str(err.get("status") or ""), str(err.get("message") or "")) if x).strip()
    except Exception:
        pass
    text = f"HTTP {e.code}" + (f" {detail}" if detail else f" {e.reason}")
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text[:300]


def _request(method, url, headers, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    secrets = [v.replace("Bearer ", "") for v in headers.values()]
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AIError(_describe_http_error(e, secrets), e.code) from None
    except urllib.error.URLError as e:
        raise AIError(f"เชื่อมต่อไม่ได้: {e.reason}") from None
    except OSError as e:  # รวม TimeoutError (อ่านผลตอบกลับไม่ทันเวลา)
        raise AIError(f"เชื่อมต่อไม่ได้: {e or 'หมดเวลา'}") from None


RETRY_CODES = (429, 500, 502, 503, 504)  # เซิร์ฟเวอร์ยุ่งหรือโควตาชั่วคราว ลองใหม่ได้
RETRY_DELAYS = (1.0,)                    # วินาทีรอก่อนลองซ้ำกับโมเดลเดิม 1 ครั้ง


def _post(url, headers, payload):
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return _request("POST", url, headers, payload)
        except AIError as e:
            if e.status not in RETRY_CODES or attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])


def _get(url, headers):
    return _request("GET", url, headers)


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_SKIP = ("preview", "exp", "image", "tts", "live", "audio", "embedding", "aqa", "thinking", "vision", "robotics", "computer")


def list_gemini_models(key):
    """ชื่อโมเดล Gemini ที่ key นี้เรียก generateContent ได้ (ตัดคำนำหน้า 'models/')"""
    res = _get(f"{GEMINI_BASE}/models?pageSize=200", {"x-goog-api-key": key})
    return [m["name"].split("/", 1)[-1] for m in res.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def suggest_models(names, limit=5):
    """โมเดลแบบ flash ที่เสถียร (ไม่ใช่ preview/exp/ฯลฯ) เรียงจากใหม่ไปเก่า เพื่อแนะนำผู้ใช้"""
    flash = [n for n in names if n.startswith("gemini-") and "flash" in n and not any(s in n for s in _SKIP)]
    return sorted(flash, reverse=True)[:limit]


def test_connection(api_key=None, provider=None, model=None):
    """ตรวจว่า key ใช้ได้ไหม และโมเดลที่จะใช้เรียกได้จริงไหม คืน (ok, ข้อความอธิบายหลายบรรทัด)
    ไม่ระบุโมเดล = ตรวจโมเดลสำรองที่ระบบลองตามลำดับ (GEMINI_MODELS) ว่ามีตัวไหนใช้ได้กับบัญชีนี้"""
    prov, key = available_provider(api_key, provider)
    if not prov:
        return False, "ยังไม่ได้ใส่ API key"
    lines = []
    try:
        if prov == "gemini":
            model = model or os.environ.get("GEMINI_MODEL")
            names = list_gemini_models(key)
            lines.append(f"key ใช้ได้ พบโมเดลที่เรียกใช้ได้ {len(names)} ตัว")
            if model:
                usable = [model] if model in names else []
                if not usable:
                    sug = suggest_models(names)
                    lines.append(f"ไม่พบโมเดล '{model}' ในบัญชีนี้" + (f" — ลองใช้: {', '.join(sug)}" if sug else ""))
                    return False, "\n".join(lines)
            else:
                usable = [m for m in GEMINI_MODELS if m in names]
                if not usable:
                    sug = suggest_models(names)
                    lines.append("ไม่พบโมเดลสำรองที่ระบบใช้ในบัญชีนี้" + (f" — ลองใช้: {', '.join(sug)}" if sug else ""))
                    return False, "\n".join(lines)
                lines.append(f"โมเดลสำรองที่ใช้ได้กับบัญชีนี้: {', '.join(usable)}")
            model = usable[0]
        else:
            model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        reply = call_llm("Reply with the single word OK.", "ping", prov, key, model=model)
        lines.append(f"เรียกโมเดล '{model}' สำเร็จ (ตอบกลับ: {reply.strip()[:40]})")
        return True, "\n".join(lines)
    except Exception as e:
        lines.append(f"ล้มเหลว: {e}")
        return False, "\n".join(lines)


def call_llm(system, user, provider, key, json_mode=False, model=None):
    """เรียก LLM ครั้งเดียว คืนข้อความตอบกลับ (raise AIError เมื่อผิดพลาด)
    Gemini: ถ้าไม่ระบุโมเดล จะลอง GEMINI_MODELS ตามลำดับจนกว่าจะมีตัวที่ตอบได้ (รุ่นที่ Google ระบุว่าใช้ฟรีอยู่ก่อน)"""
    if provider == "gemini":
        chosen = model or os.environ.get("GEMINI_MODEL")
        candidates = [chosen] if chosen else list(GEMINI_MODELS)  # ผู้ใช้ระบุเอง = ใช้ตัวนั้นตัวเดียว
        gen = {"temperature": 0.2}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        first = last = None
        for m in candidates:
            try:
                res = _post(
                    f"{GEMINI_BASE}/models/{m}:generateContent",
                    {"x-goog-api-key": key},
                    {"systemInstruction": {"parts": [{"text": system}]},
                     "contents": [{"role": "user", "parts": [{"text": user}]}],
                     "generationConfig": gen})
                try:  # โมเดลที่คิดก่อนตอบอาจคืนหลายส่วน (ส่วน thought ไม่ใช่คำตอบ) จึงรวมทุกส่วนที่เป็นข้อความ
                    parts = res["candidates"][0]["content"]["parts"]
                    return "".join(p.get("text", "") for p in parts if not p.get("thought"))
                except (KeyError, IndexError, TypeError):
                    why = (res.get("promptFeedback") or (res.get("candidates") or [{}])[0].get("finishReason") or "ไม่ทราบสาเหตุ")
                    raise AIError(f"AI ตอบกลับโดยไม่มีข้อความ ({str(why)[:150]})") from None
            except AIError as e:
                if e.status in (400, 401):  # key ผิดรูปแบบ/ไม่ถูกต้อง ลองโมเดลอื่นก็ไม่ช่วย
                    raise
                last = e  # 403/404/429/5xx/timeout: อาจเป็นแค่โมเดลนี้ ลองตัวถัดไป
                if first is None and e.status != 404:
                    first = e  # เก็บเหตุผลแรกที่ไม่ใช่ "ไม่พบโมเดล" ไว้แสดง กันถูก 404 ของตัวท้ายบังเหตุจริง
        raise first or last
    if provider == "openai":
        model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        body = {"model": model, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        res = _post("https://api.openai.com/v1/chat/completions",
                    {"Authorization": f"Bearer {key}"}, body)
        return res["choices"][0]["message"]["content"]
    raise ValueError(f"ไม่รู้จัก provider '{provider}'")


# ---------- โจทย์ -> ข้อกำหนด ----------
def sanitize_requirements(raw, data=None):
    """ตรวจผลจาก LLM: ทิ้งค่าที่ไม่อยู่ใน data (กัน AI แต่ง id หรือค่าเพี้ยน)"""
    d = data or get_data()
    out = {"event_type": None, "people": None, "tier": None, "layout": None,
           "components": [], "budget_thb": None}
    if not isinstance(raw, dict):
        return out
    if raw.get("event_type") in d["events"]:
        out["event_type"] = raw["event_type"]
    if raw.get("layout") in d["layouts"]:
        out["layout"] = raw["layout"]
    if raw.get("tier") in d["tiers"]:
        out["tier"] = raw["tier"]
    p = raw.get("people")
    if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0:
        out["people"] = int(p)
    b = raw.get("budget_thb")
    if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
        out["budget_thb"] = int(b)
    comps = raw.get("components")
    if isinstance(comps, list):
        out["components"] = [c for c in dict.fromkeys(comps) if c in d["components"]]
    return out


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(m.group(0) if m else text)


def extract_requirements(text, api_key=None, provider=None, model=None, data=None, llm=call_llm):
    """คืน (ข้อกำหนด, ข้อมูลแหล่งที่มา)  source ∈ {"llm", "rules"} และ error ถ้า LLM ล้มเหลว"""
    prov, key = available_provider(api_key, provider)
    if prov:
        try:
            system, shots = build_extract_prompt(data)
            reply = llm(system, f"{shots}\n\nInput: {text}\nOutput:", prov, key, json_mode=True, model=model)
            req = sanitize_requirements(_extract_json(reply), data)
            return req, {"source": "llm", "provider": prov, "error": None}
        except Exception as e:  # เครือข่าย/โควตา/รูปแบบตอบกลับเพี้ยน -> ใช้ rule-based แทน
            return parse_request(text), {"source": "rules", "provider": prov, "error": f"{type(e).__name__}: {e}"}
    return parse_request(text), {"source": "rules", "provider": None, "error": None}


# ---------- คำแนะนำ ----------
def rule_based_advice(plan):
    """คำแนะนำแบบแม่แบบจากตัวเลขจริงของแผน (ใช้เมื่อไม่มี/เรียก LLM ไม่ได้)"""
    i, a, s = plan["inputs"], plan["area"], plan["safety"]
    tips = [
        f"- แผน{i['event_name_th']} {i['people']:,} คน แบบ{i['layout_name_th']} ใช้พื้นที่รวม {a['total']:g} ตร.ม. "
        f"(ที่นั่ง {a['seating']:g} + ส่วนประกอบอื่น {a['subtotal'] - a['seating']:.1f} + ทางสัญจร {a['circulation']:g}) "
        f"ควรหาสถานที่ไม่ต่ำกว่า {a['recommended_min_venue']:,} ตร.ม.",
        f"- ความปลอดภัย: ควรมีทางออกอย่างน้อย {s['exits']} จุด ความกว้างรวมไม่ต่ำกว่า {s['exit_width_total_m']:g} ม. "
        f"ห้องน้ำประมาณ {s['toilets']} ที่ และเพดานสูงไม่ต่ำกว่า {s['ceiling_height_min_m']:g} ม.",
    ]
    if "buffet" in i["components"]:
        tips.append("- โซนอาหาร: ตั้งจุดบริการแยกจากทางเข้างานและทางเดินหลักเพื่อลดคิวติดขัด")
    b = plan["budget"]
    opts = {o["tier"]: o for o in plan["tier_options"]}
    if b:
        if b["within_budget"]:
            tips.append(f"- งบที่ตั้งไว้ {b['budget_thb']:,.0f} บาท เพียงพอ เหลือประมาณ {b['difference']:,} บาท สำหรับสำรองหน้างาน")
        elif b["recommended_tier"]:
            o = opts[b["recommended_tier"]]
            tips.append(f"- งบเกินประมาณ {-b['difference']:,} บาท ลองลดเป็นระดับ '{o['name_th']}' "
                        f"(ประมาณ {o['total_cost']:,} บาท) จะอยู่ในงบ")
        else:
            lo = opts["basic"]
            tips.append(f"- แม้ระดับประหยัดก็ยังเกินงบ (ประมาณ {lo['total_cost']:,} บาท) ลองลดจำนวนคนหรือตัดส่วนประกอบที่ไม่จำเป็น")
    else:
        tips.append("- ยังไม่ได้ระบุงบ: เทียบตารางระดับงบด้านล่างเพื่อเลือกที่เหมาะกับเป้าหมายงาน")
    return "\n".join(tips)


def advise(plan, text="", api_key=None, provider=None, model=None, llm=call_llm):
    """คืน (ข้อความคำแนะนำ, ข้อมูลแหล่งที่มา)"""
    d = get_data()
    prov, key = available_provider(api_key, provider)
    if prov:
        try:
            user = (f"โจทย์ของผู้ใช้: {text or '-'}\n\nข้อมูลแผน (JSON):\n"
                    f"{json.dumps(plan_summary_for_llm(plan), ensure_ascii=False)}")
            reply = llm(d["prompts"]["advise_system"], user, prov, key, json_mode=False, model=model)
            if reply and reply.strip():
                return reply.strip(), {"source": "llm", "provider": prov, "error": None}
            raise ValueError("LLM ตอบกลับว่างเปล่า")
        except Exception as e:
            return rule_based_advice(plan), {"source": "rules", "provider": prov, "error": f"{type(e).__name__}: {e}"}
    return rule_based_advice(plan), {"source": "rules", "provider": None, "error": None}
