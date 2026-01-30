import os
import io
import time
import base64
import datetime
import json
import requests
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-please-123")  # Render env أفضل

OCR_API_KEY = (os.environ.get("OCR_SPACE_KEY") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

# Defaults (ثابتة لو تركتها فاضية)
DEFAULT_TEACHER = "علي عسيري"
DEFAULT_SCHOOL = "ثانوية الظهران"
DEFAULT_PRINCIPAL = "أحمد الشمراني"


# -----------------------------
# Helpers
# -----------------------------
def compress_image(file_storage, max_w=1400, quality=70):
    """
    يقلل حجم الصورة قبل إرسالها لـ OCR.space
    يرجع (filename, bytes, mimetype)
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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=45):
    """
    لا نرسل language لتجنب أخطاء OCR.space
    """
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
    # تنظيف بسيط
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def is_gibberish_ar(snippet: str) -> bool:
    """
    إذا OCR طلع كلام غريب/مكسّر — نكتشفه
    """
    if not snippet:
        return True
    # نسبة أحرف عربية بسيطة
    arabic = sum(1 for ch in snippet if "\u0600" <= ch <= "\u06FF")
    ratio = arabic / max(len(snippet), 1)
    # لو نسبة العربي ضعيفة جدًا يعتبر غير مفهوم
    return ratio < 0.12


def smart_program_desc(ocr_text: str) -> str:
    """
    وصف برنامج عربي مفهوم + متنوع
    يعتمد على OCR إذا كان واضح، وإذا لا يعطي وصف رسمي عام ممتاز.
    """
    t = clean_text(ocr_text)
    if not t:
        return "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف، بهدف رفع التفاعل وتحسين نواتج التعلم وفق خطة الدرس."

    first = " ".join(t.splitlines()[:3])[:250]
    if is_gibberish_ar(first):
        return "تم تنفيذ نشاط/برنامج تعليمي داخل الصف لدعم تعلم الطلاب، تضمن تفاعلًا منظمًا ومهامًا صفية وإشرافًا مباشرًا من المعلم، مع توثيق خطوات التنفيذ ونتائج المتعلمين."
    else:
        return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {first})"


def gpt_extract_fields(ocr_text: str, program_name: str, program_desc: str, subject: str) -> dict:
    """
    يرجع dict:
    goal, procedure, tech_tool, assessment, impact
    - هنا نسمح بتعبئة احترافية حتى لو الدليل ضعيف (بصياغة احتمالية)
    """
    base = {
        "goal": "",
        "procedure": "",
        "tech_tool": "",
        "assessment": "",
        "impact": "",
        "_gpt_error": ""
    }

    if not OPENAI_API_KEY:
        # fallback قوي
        base["goal"] = "تعزيز تعلم الطلاب ورفع التفاعل داخل الصف وتحسين نواتج التعلم."
        base["procedure"] = "تنفيذ نشاط/مهمة صفية منظمة مع إرشادات واضحة ومتابعة تقدم الطلاب وتوثيق الإنجاز."
        base["tech_tool"] = "استخدام وسائل عرض/أدوات صفية مساندة (مثل عرض مرئي أو ورقة عمل) بحسب المتاح."
        base["assessment"] = "تقويم بنائي أثناء التنفيذ عبر ملاحظة الأداء وأسئلة قصيرة/تحقق من المنتج النهائي."
        base["impact"] = "تحسن الفهم والتفاعل وارتفاع مستوى الإنجاز لدى الطلاب بصورة ملحوظة."
        base["_gpt_error"] = "OPENAI_API_KEY غير موجود"
        return base

    ocr_short = (ocr_text or "").strip()
    if len(ocr_short) > 4500:
        ocr_short = ocr_short[:4500] + "..."

    sys = (
        "أنت خبير تقويم تربوي سعودي. لديك نص OCR قد يكون ناقصًا أو غير واضح. "
        "المطلوب تعبئة بطاقة شاهد رسمية باحتراف. "
        "إذا الدليل واضح: استخرج منه مباشرة. "
        "إذا الدليل ضعيف: اكتب صياغة (احتمالية/مستنتجة) قريبة جدًا مما يظهر في الشاهد دون مبالغة، "
        "واستخدم عبارات مثل: (يُفهم/يُرجح/يبدو/يحتمل) لتجنب اختلاق جازم. "
        "ممنوع كتابة كلمات غير مفهومة أو لغة غريبة. "
        "أعد JSON فقط دون أي شرح."
    )

    user = f"""
التخصص: {subject or "غير محدد"}
اسم البرنامج/النشاط: {program_name or "نشاط/برنامج تعليمي"}
وصف البرنامج: {program_desc or ""}

نص الشاهد (OCR):
{ocr_short}

أرجع JSON بهذه المفاتيح فقط:
goal, procedure, tech_tool, assessment, impact

قواعد:
- كل قيمة سطر واحد فقط (15-28 كلمة تقريباً)
- عربي رسمي واضح
- إذا ما فيه دليل كافي: استخدم (يُفهم/يُرجح/يبدو/يحتمل) + صياغة مهنية قريبة مما يظهر
- ممنوع إضافة مفاتيح أخرى
"""

    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.25,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }

        r = requests.post(url, headers=headers, json=payload, timeout=55)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]

        j = json.loads(content)

        def safe_get(k, fallback):
            v = (j.get(k) or "").strip()
            return v if v else fallback

        # Fallbacks مهنية إذا رجّع فاضي
        base["goal"] = safe_get("goal", "تعزيز تعلم الطلاب ورفع التفاعل داخل الصف وتحسين نواتج التعلم وفق أهداف الدرس.")
        base["procedure"] = safe_get("procedure", "تنفيذ نشاط صفّي منظم بخطوات واضحة ومهام محددة، مع متابعة المعلم لمشاركة الطلاب وتوثيق النتائج.")
        base["tech_tool"] = safe_get("tech_tool", "يُرجح استخدام وسائل تعليمية مساندة (عرض مرئي/ورقة عمل/منصة رقمية) بحسب ما يظهر في الشاهد.")
        base["assessment"] = safe_get("assessment", "تقويم بنائي أثناء التنفيذ عبر الملاحظة وأسئلة سريعة/تحقق من المنتج النهائي، مع رصد مستوى إتقان الطلاب.")
        base["impact"] = safe_get("impact", "يُفهم وجود أثر تعليمي إيجابي مثل تحسن الفهم وارتفاع المشاركة والانضباط، وانعكاس ذلك على أداء الطلاب.")

        return base

    except Exception as e:
        # لا نطيّح الصفحة، نخليها تشتغل
        base["goal"] = "تعذر تحليل GPT حالياً، وتم توليد صياغة احتياطية مهنية."
        base["procedure"] = "تنفيذ نشاط/مهمة صفية منظمة مع متابعة مشاركة الطلاب وتوثيق نواتج التعلم."
        base["tech_tool"] = "يُرجح استخدام أدوات تعليمية مساندة (سبورة/عرض/ورقة عمل/منصة) حسب ما يظهر في الشاهد."
        base["assessment"] = "تقويم بنائي خلال النشاط بالملاحظة والأسئلة القصيرة والتحقق من إنجاز المهمة."
        base["impact"] = "أثر تعليمي محتمل يتمثل في رفع التفاعل وتحسن الفهم والأداء."
        base["_gpt_error"] = str(e)
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

    # تاريخ تلقائي
    date_str = datetime.date.today().strftime("%Y/%m/%d")

    program_name = (request.form.get("program_name") or "").strip() or "نشاط/برنامج تعليمي"
    program_desc = (request.form.get("program_desc") or "").strip()

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = None
    img2_url = None

    ocr1_text, ocr1_err = "", ""
    ocr2_text, ocr2_err = "", ""

    # 1) صورة 1
    if img1 and img1.filename:
        fn, bts, mt = compress_image(img1)
        img1_url = f"data:{mt};base64," + base64.b64encode(bts).decode("utf-8")
        ocr1_text, ocr1_err = ocr_space(bts, filename=fn)

    # 2) صورة 2
    if img2 and img2.filename:
        fn, bts, mt = compress_image(img2)
        img2_url = f"data:{mt};base64," + base64.b64encode(bts).decode("utf-8")
        ocr2_text, ocr2_err = ocr_space(bts, filename=fn)

    ocr1_text = clean_text(ocr1_text)
    ocr2_text = clean_text(ocr2_text)

    combined_ocr = "\n".join([t for t in [ocr1_text, ocr2_text] if t]).strip()

    # وصف البرنامج ذكي ومفهوم
    if not program_desc:
        program_desc = smart_program_desc(combined_ocr)

    # GPT Extract
    gpt_data = gpt_extract_fields(
        ocr_text=combined_ocr,
        program_name=program_name,
        program_desc=program_desc,
        subject=subject
    )

    # --- حل 405: نخزن النتيجة ونحوّل لصفحة GET ---
    session["result"] = {
        "teacher": teacher,
        "subject": subject,
        "school": school,
        "principal": principal,
        "date_str": date_str,
        "program_name": program_name,
        "program_desc": program_desc,
        "img1_url": img1_url,
        "img2_url": img2_url,
        "ocr1_err": ocr1_err,
        "ocr2_err": ocr2_err,
        "gpt": gpt_data,
        "note_footer": "ملاحظة: تم توليد الملخص بالذكاء الاصطناعي بناءً على نص OCR وما يظهر من قرائن، بصياغة مهنية محافظة."
    }

    return redirect(url_for("result"))


@app.route("/result", methods=["GET"])
def result():
    data = session.get("result")
    if not data:
        return redirect(url_for("index"))
    return render_template("result.html", **data)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
