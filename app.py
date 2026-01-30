import os
import io
import time
import base64
import datetime
import json
import re
import requests
from PIL import Image
from flask import Flask, render_template, request

app = Flask(__name__)

OCR_API_KEY = (os.environ.get("OCR_SPACE_KEY") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

# Defaults (ثابتة لو تركتها فاضية)
DEFAULT_TEACHER = "علي عسيري"
DEFAULT_SCHOOL = "ثانوية الظهران"
DEFAULT_PRINCIPAL = "أحمد الشمراني"


# -----------------------------
# Helpers
# -----------------------------
def compress_image(file_storage, max_w=1600, quality=75):
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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=45):
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
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

            text = (parsed[0].get("ParsedText") or "").strip()
            return text, ""

        except requests.exceptions.Timeout:
            last_err = "انتهت مهلة الاتصال مع OCR.space (Timeout)"
        except Exception as e:
            last_err = f"خطأ اتصال/تحليل OCR: {e}"

        time.sleep(0.8 * (attempt + 1))

    return "", last_err or "فشل OCR لسبب غير معروف"


def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def auto_program_desc_from_ocr(ocr_text: str) -> str:
    t = clean_text(ocr_text)
    if not t:
        return ""
    lines = t.splitlines()[:3]
    snippet = " ".join(lines)
    return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {snippet})"


def _extract_json_safely(text: str) -> dict:
    """
    يحاول يطلع JSON حتى لو GPT رجّع كلام حوله.
    """
    if not text:
        return {}

    # أول محاولة: json.loads مباشر
    try:
        return json.loads(text)
    except Exception:
        pass

    # محاولة ثانية: استخراج أول كتلة { ... }
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}

    return {}


def gpt_extract_fields(ocr_text: str, program_name: str, program_desc: str, subject: str) -> dict:
    """
    يرجع dict:
    goal, procedure, tech_tool, assessment, impact
    * ما يخلي شيء فاضي
    * إذا ما فيه دليل قوي -> يكتب (استنتاج محتمل) بصيغة رسمية
    """
    base = {
        "goal": "استنتاج محتمل: دعم عملية التعلم وتعزيز تحقق نواتج التعلم وفق محتوى الشاهد.",
        "procedure": "استنتاج محتمل: تنفيذ نشاط/إجراء تعليمي موثق في الشاهد داخل الصف أو ضمن خطة الدرس.",
        "tech_tool": "استنتاج محتمل: استخدام ورقة عمل/نموذج/مستند توثيقي، وقد تُستخدم وسيلة عرض عند الحاجة.",
        "assessment": "استنتاج محتمل: متابعة أداء الطلاب عبر التحقق من الإجابات/التصحيح/رصد الدرجات حسب الشاهد.",
        "impact": "استنتاج محتمل: تحسين انضباط التعلم ووضوح المطلوب للطلاب ورفع مستوى الإنجاز.",
    }

    # لو ما فيه مفتاح GPT نخلي الاستنتاجات الافتراضية (بدون سقوط)
    if not OPENAI_API_KEY:
        base["goal"] = "استنتاج محتمل: الشاهد يوثق إجراء تعليمي؛ لم يتم تفعيل GPT (OPENAI_API_KEY غير موجود)."
        return base

    ocr_short = (ocr_text or "").strip()
    if len(ocr_short) > 5000:
        ocr_short = ocr_short[:5000] + "..."

    # ✅ هذا هو “البرومبت” اللي تعدله لو تبغى
    sys = (
        "أنت خبير تقويم تربوي. لديك نص مستخرج OCR من شاهد تعليمي (قد يكون غير واضح). "
        "مهمتك تعبئة 5 حقول بشكل احترافي ورسمي.\n\n"
        "قواعد إلزامية:\n"
        "1) ممنوع ترك أي قيمة فارغة.\n"
        "2) إذا وجدت دليل صريح في النص: اكتب صياغة مؤكدة.\n"
        "3) إذا الدليل غير صريح لكن يوجد تلميح/قرينة: اكتب (استنتاج محتمل: ...).\n"
        "4) إذا لا توجد قرائن كافية إطلاقاً: اكتب (تقدير مهني عام: ...).\n"
        "5) لا تذكر أنك نموذج ذكاء اصطناعي ولا تكتب أي شرح خارج JSON.\n"
        "6) أعد JSON فقط بهذه المفاتيح: goal, procedure, tech_tool, assessment, impact."
    )

    user = f"""
التخصص: {subject or "غير محدد"}
اسم البرنامج/النشاط (إن وجد): {program_name or "غير محدد"}
وصف البرنامج (إن وجد): {program_desc or "غير محدد"}

نص الشاهد (OCR):
{ocr_short}

أعد JSON بالمفاتيح فقط:
goal, procedure, tech_tool, assessment, impact

شروط الصياغة:
- كل قيمة سطر واحد رسمي (10-25 كلمة).
- استخدم (استنتاج محتمل:) عند غياب الدليل الصريح مع وجود قرائن.
- استخدم (تقدير مهني عام:) عند انعدام القرائن.
"""

    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }

        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        content = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        j = _extract_json_safely(content)

        # تعبئة آمنة بدون فراغات
        for k in list(base.keys()):
            v = (j.get(k) or "").strip()
            if v:
                base[k] = v

        return base

    except Exception:
        # لا نطيّح الصفحة — نرجع الاستنتاجات الافتراضية
        return base


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET"])
def index():
    today = datetime.date.today().strftime("%Y/%m/%d")
    return render_template(
        "index.html",
        default_teacher=DEFAULT_TEACHER,
        default_school=DEFAULT_SCHOOL,
        default_principal=DEFAULT_PRINCIPAL,
        today=today
    )


@app.route("/generate", methods=["POST"])
def generate():
    teacher = (request.form.get("teacher") or "").strip() or DEFAULT_TEACHER
    subject = (request.form.get("subject") or "").strip()
    school = (request.form.get("school") or "").strip() or DEFAULT_SCHOOL
    principal = (request.form.get("principal") or "").strip() or DEFAULT_PRINCIPAL

    date_str = datetime.date.today().strftime("%Y/%m/%d")

    program_name = (request.form.get("program_name") or "").strip()
    program_desc = (request.form.get("program_desc") or "").strip()

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = None
    img2_url = None

    ocr1_text, ocr1_err = "", ""
    ocr2_text, ocr2_err = "", ""

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

    if not program_desc:
        program_desc = auto_program_desc_from_ocr(combined_ocr) or "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."

    if not program_name:
        program_name = "نشاط/برنامج تعليمي"

    gpt_data = gpt_extract_fields(
        ocr_text=combined_ocr,
        program_name=program_name,
        program_desc=program_desc,
        subject=subject
    )

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
        ocr1_err=ocr1_err,
        ocr2_err=ocr2_err,
        gpt=gpt_data,
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
