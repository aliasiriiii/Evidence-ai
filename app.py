import os
import re
import base64
import requests
from datetime import date
from flask import Flask, request, render_template
from werkzeug.utils import secure_filename

app = Flask(__name__)

OCR_SPACE_KEY = os.getenv("OCR_SPACE_KEY", "").strip()

# ---------------------------
# OCR: استخراج النص من الصورة
# ---------------------------
def ocr_space_extract_text(image_bytes: bytes, filename: str) -> str:
    if not OCR_SPACE_KEY:
        raise RuntimeError("ناقص OCR_SPACE_KEY في Render Environment Variables")

    r = requests.post(
        "https://api.ocr.space/parse/image",
        files={"filename": (filename, image_bytes)},
        data={
            "apikey": OCR_SPACE_KEY,
            "language": "ara",
            "OCREngine": "2",
            "isOverlayRequired": "false",
        },
        timeout=60
    )
    j = r.json()

    if j.get("IsErroredOnProcessing"):
        msg = j.get("ErrorMessage") or j.get("ErrorDetails") or "OCR فشل"
        raise RuntimeError(str(msg))

    parsed = j.get("ParsedResults", [])
    text = "\n".join([p.get("ParsedText", "") for p in parsed]).strip()
    return text

def clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    t = text
    t = re.sub(r"\b(AM|PM)\s*\d{1,2}:\d{2}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    t = re.sub(r"(?m)^\s*\d+\s*$", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t

def infer_card_fields(text: str, meta: dict) -> dict:
    subject = meta.get("subject", "فيزياء")
    stage = meta.get("stage", "ثانوي")
    teacher = meta.get("teacher", "علي عبدالله عسيري")
    school = meta.get("school", "ثانوية الظهران الحكومية")
    field = meta.get("field", "صفي / عملي")
    job = meta.get("job", "معلم")
    beneficiaries = meta.get("beneficiaries", "الطلاب")
    count = meta.get("count", "—")
    exec_date = meta.get("exec_date") or date.today().isoformat()

    low = (text or "").lower()

    if any(k in low for k in ["محاكاة", "simulation", "phet", "افتراضي", "مختبر افتراضي"]):
        program_name = "التعلم بالمحاكاة وتطبيق التجارب الافتراضية باستخدام التقنيات الحديثة"
        goals = "التعرف على المختبرات الافتراضية والتقنيات الحديثة."
        desc = "أن يقوم الطلبة بالتجربة والتحليل وتسجيل النتائج باستخدام تقنيات حديثة."
    elif any(k in low for k in ["تجربة", "مختبر", "lab", "عملي"]):
        program_name = "تنفيذ تجربة عملية وتوثيقها تعليمياً"
        goals = "تنمية مهارات الاستقصاء العلمي والتجريب وتحليل النتائج."
        desc = "أن يقوم الطلبة بتنفيذ تجربة عملية وفق خطوات منظمة وتسجيل النتائج ومناقشتها."
    else:
        program_name = f"توظيف التقنية في تدريس {subject}"
        goals = f"تعزيز فهم مفاهيم {subject} عبر نشاط تطبيقي وتفاعلي."
        desc = "تنفيذ نشاط تعليمي موثق بالصور يرفع التفاعل ويحسن نواتج التعلم."

    lesson = (meta.get("lesson") or "").strip()
    if lesson:
        program_name += f" (درس: {lesson})"

    return {
        "program_name": program_name,
        "field": field,
        "teacher": teacher,
        "job": job,
        "goals": goals,
        "description": desc,
        "exec_date": exec_date,
        "beneficiaries": beneficiaries,
        "count": count,
        "school": school,
        "stage": stage,
        "subject": subject,
    }

# ---------------------------------------------------------
# ✅ صفحة واحدة "/" تستقبل GET + POST (تحل 405 مباشرة)
# ---------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def home():
    # GET: عرض صفحة الرفع
    if request.method == "GET":
        return render_template("index.html")

    # POST: معالجة رفع الصورة + استخراج النص + إخراج بطاقة
    try:
        if "image" not in request.files:
            return "لم يتم رفع صورة", 400

        f = request.files["image"]
        filename = secure_filename(f.filename or "evidence.jpg")
        img_bytes = f.read()

        meta = {
            "stage": request.form.get("stage", "ثانوي"),
            "subject": request.form.get("subject", "فيزياء"),
            "lesson": request.form.get("lesson", ""),
            "teacher": request.form.get("teacher", "علي عبدالله عسيري"),
            "school": request.form.get("school", "ثانوية الظهران الحكومية"),
            "field": request.form.get("field", "صفي / عملي"),
            "job": request.form.get("job", "معلم"),
            "beneficiaries": request.form.get("beneficiaries", "الطلاب"),
            "count": request.form.get("count", "—"),
            "exec_date": request.form.get("exec_date", ""),
        }

        ocr_text = ocr_space_extract_text(img_bytes, filename)
        cleaned = clean_ocr_text(ocr_text)
        card = infer_card_fields(cleaned, meta)

        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{img_b64}"

        return render_template(
            "result.html",
            card=card,
            ocr_text=cleaned,
            image_url=data_url
        )

    except Exception as e:
        # رسالة واضحة بدل 500
        return f"صار خطأ أثناء التحليل: {e}", 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
