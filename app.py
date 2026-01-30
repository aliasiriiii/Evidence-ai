import os
import io
import time
import base64
import json
import logging
from datetime import datetime

import requests
from PIL import Image
from flask import Flask, render_template, request

# OpenAI SDK (v1+)
from openai import OpenAI

# -----------------------------
# App + Logging
# -----------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("evidence-ai")

OCR_API_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# -----------------------------
# Constants: MoE rubric (11 items)
# (تصنيف محافظ: نطلع عنصرين فقط إذا في دليل واضح)
# -----------------------------
RUBRIC_11 = [
    "أداء الواجبات الوظيفية",
    "التفاعل مع المجتمع المحلي",
    "التفاعل مع أولياء الأمور",
    "تنويع استراتيجيات التدريس",
    "تحسين نواتج المتعلمين",
    "إعداد وتنفيذ خطة الدرس",
    "توظيف التقنيات والوسائل التعليمية",
    "تهيئة البيئة التعليمية",
    "ضبط سلوك الطلاب",
    "تحليل نتائج المتعلمين وتشخيص مستواهم",
    "تنوع أساليب التقويم",
]

# -----------------------------
# Helpers
# -----------------------------
def get_openai_client():
    if not OPENAI_API_KEY:
        return None
    # بدون أي arguments إضافية عشان ما يطلع خطأ Client.__init__()
    return OpenAI(api_key=OPENAI_API_KEY)

def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()

def compress_image(file_storage, max_w=1600, quality=78):
    """
    يقلل حجم الصورة قبل OCR.space
    يرجع: (filename, bytes, mimetype)
    """
    filename = file_storage.filename or "upload.jpg"
    img = Image.open(file_storage.stream)

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    if w > max_w:
        new_h = int(h * (max_w / w))
        img = img.resize((max_w, new_h))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return filename, buf.getvalue(), "image/jpeg"

def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=35):
    """
    حل مشكلة E201: لا نرسل language نهائياً
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا نرسل language لتجنب E201
    }
    files = {"filename": (filename, image_bytes, "image/jpeg")}

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, data=data, files=files, timeout=timeout)
            r.raise_for_status()
            j = r.json()

            if j.get("IsErroredOnProcessing"):
                errs = j.get("ErrorMessage")
                if isinstance(errs, list):
                    msg = " | ".join(errs)
                else:
                    msg = str(errs) if errs else "خطأ غير معروف من OCR.space"
                return "", f"فشل OCR: {msg}"

            parsed = j.get("ParsedResults", [])
            if not parsed:
                return "", "OCR رجّع نتيجة فاضية"

            text = parsed[0].get("ParsedText", "") or ""
            return text.strip(), ""

        except requests.exceptions.Timeout:
            last_err = "انتهت مهلة الاتصال مع OCR.space (Timeout)"
        except Exception as e:
            last_err = f"خطأ اتصال/تحليل OCR: {e}"

        time.sleep(0.8 * (attempt + 1))

    return "", last_err or "فشل OCR لسبب غير معروف"

def auto_description_from_ocr(ocr_text: str) -> str:
    """
    fallback بدون GPT
    """
    t = clean_text(ocr_text)
    if not t:
        return "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."
    lines = t.splitlines()[:3]
    snippet = " ".join(lines)
    return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {snippet})"

# -----------------------------
# GPT: Evidence + Blocks + Rubric mapping (محافظ)
# -----------------------------
def gpt_make_evidence(program_hint: str, ocr_text: str):
    """
    GPT يصيغ:
    - program_name
    - program_desc (سطر/سطرين مناسب للبطاقة)
    - blocks: goal/implementation/tools/assessment/impact
    - rubric_top2: [{name, reason}] أو "غير محدد"
    """
    if not OPENAI_API_KEY:
        return "", "", {}, [], "OPENAI_API_KEY غير موجود في Render Environment"

    client = get_openai_client()
    if client is None:
        return "", "", {}, [], "OPENAI_API_KEY غير موجود"

    ocr_text = clean_text(ocr_text)

    # fallback إذا OCR فاضي
    if not ocr_text:
        blocks = {
            "goal": "غير مذكور في الشاهد",
            "implementation": "غير مذكور في الشاهد",
            "tools": "غير مذكور في الشاهد",
            "assessment": "غير مذكور في الشاهد",
            "impact": "غير مذكور في الشاهد",
        }
        rubric_top2 = []
        program_name = program_hint.strip() or "نشاط/برنامج تعليمي"
        program_desc = "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."
        return program_name, program_desc, blocks, rubric_top2, ""

    rubric_list = "\n- " + "\n- ".join(RUBRIC_11)

    prompt = f"""
أنت مساعد تربوي سعودي تكتب بطاقة توثيق (شاهد تربوي) رسمي ودقيق.

قواعد صارمة:
1) ممنوع اختلاق أي معلومة غير موجودة في نص الشاهد.
2) إذا لم تتوفر معلومة: اكتب حرفيًا "غير مذكور في الشاهد".
3) اكتب بالعربية الرسمية المختصرة (أسلوب وزارة التعليم).
4) المطلوب إخراج JSON فقط (بدون أي كلام خارج JSON).
5) تصنيف عناصر الأداء الوظيفي: اختر "حد أقصى عنصرين فقط" من القائمة، بشرط وجود دليل واضح داخل النص. إذا ما فيه دليل كافي اترك القائمة فارغة [].

قائمة عناصر الأداء (اختر منها فقط):
{rubric_list}

ملاحظة اسم النشاط المقترح (اختياري): {program_hint}

نص الشاهد (OCR):
\"\"\"{ocr_text[:3500]}\"\"\"

أخرج JSON بالمفاتيح التالية فقط:
{{
  "program_name": "...",
  "program_desc": "...",
  "goal": "...",
  "implementation": "...",
  "tools": "...",
  "assessment": "...",
  "impact": "...",
  "rubric_top2": [
    {{"name": "اسم عنصر من القائمة", "reason": "سبب مختصر من النص"}},
    {{"name": "اسم عنصر من القائمة", "reason": "سبب مختصر من النص"}}
  ]
}}
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": "التزم بإخراج JSON فقط دون أي شرح."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)

        program_name = (data.get("program_name") or "").strip()
        program_desc = (data.get("program_desc") or "").strip()

        blocks = {
            "goal": (data.get("goal") or "").strip(),
            "implementation": (data.get("implementation") or "").strip(),
            "tools": (data.get("tools") or "").strip(),
            "assessment": (data.get("assessment") or "").strip(),
            "impact": (data.get("impact") or "").strip(),
        }

        rubric_top2 = data.get("rubric_top2") or []
        if not isinstance(rubric_top2, list):
            rubric_top2 = []

        # حمايات
        if not program_name:
            program_name = program_hint.strip() or "نشاط/برنامج تعليمي"
        if not program_desc:
            program_desc = "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."

        for k in list(blocks.keys()):
            if not blocks[k]:
                blocks[k] = "غير مذكور في الشاهد"

        # فلترة rubric: لازم يكون من القائمة فقط
        filtered = []
        for item in rubric_top2[:2]:
            if isinstance(item, dict):
                nm = (item.get("name") or "").strip()
                rs = (item.get("reason") or "").strip()
                if nm in RUBRIC_11:
                    filtered.append({"name": nm, "reason": rs or "غير مذكور في الشاهد"})
        rubric_top2 = filtered

        return program_name, program_desc, blocks, rubric_top2, ""

    except Exception as e:
        log.exception("GPT error")
        program_name = program_hint.strip() or "نشاط/برنامج تعليمي"
        program_desc = auto_description_from_ocr(ocr_text)
        blocks = {
            "goal": "غير مذكور في الشاهد",
            "implementation": "غير مذكور في الشاهد",
            "tools": "غير مذكور في الشاهد",
            "assessment": "غير مذكور في الشاهد",
            "impact": "غير مذكور في الشاهد",
        }
        return program_name, program_desc, blocks, [], f"تعذر توليد GPT بسبب خطأ: {e}"

# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    teacher = (request.form.get("teacher") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    school = (request.form.get("school") or "").strip()
    principal = (request.form.get("principal") or "").strip()
    program_hint = (request.form.get("program_hint") or "").strip()
    date_str = (request.form.get("date") or "").strip()

    if not date_str:
        date_str = datetime.now().strftime("%Y/%m/%d")

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = None
    img2_url = None

    ocr1_text, ocr1_err = "", ""
    ocr2_text, ocr2_err = "", ""

    # OCR + embed images as base64
    if img1 and img1.filename:
        fn, bts, mt = compress_image(img1)
        img1_url = f"data:{mt};base64," + base64.b64encode(bts).decode("utf-8")
        ocr1_text, ocr1_err = ocr_space(bts, filename=fn)

    if img2 and img2.filename:
        fn, bts, mt = compress_image(img2)
        img2_url = f"data:{mt};base64," + base64.b64encode(bts).decode("utf-8")
        ocr2_text, ocr2_err = ocr_space(bts, filename=fn)

    ocr1_text = clean_text(ocr1_text)
    ocr2_text = clean_text(ocr2_text)

    combined_ocr = "\n".join([t for t in [ocr1_text, ocr2_text] if t]).strip()

    # GPT: اسم النشاط + وصف + عناصر + rubric
    program_name, program_desc, gpt_blocks, rubric_top2, gpt_err = gpt_make_evidence(
        program_hint, combined_ocr
    )

    # إذا GPT فشل وطلع desc فاضي لأي سبب
    if not program_desc:
        program_desc = auto_description_from_ocr(combined_ocr)

    return render_template(
        "result.html",
        teacher=teacher,
        subject=subject,
        school=school,
        principal=principal,
        date_str=date_str,
        program_name=program_name,
        program_desc=program_desc,
        img1_url=img1_url,
        img2_url=img2_url,
        ocr1_text=ocr1_text,
        ocr2_text=ocr2_text,
        ocr1_err=ocr1_err,
        ocr2_err=ocr2_err,
        gpt_blocks=gpt_blocks,
        rubric_top2=rubric_top2,
        gpt_err=gpt_err,
    )

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
