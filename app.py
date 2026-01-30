import os
import io
import time
import base64
import datetime
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
    لا نرسل language لتجنب E201
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا نرسل language
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


def gpt_extract_fields(ocr_text: str, program_name: str, program_desc: str, subject: str) -> dict:
    """
    يرجع dict:
    goal, procedure, tech_tool, assessment, impact
    مع fallback لو المفتاح غير موجود أو فشل الاتصال
    """
    base = {
        "goal": "غير مذكور في الشاهد",
        "procedure": "غير مذكور في الشاهد",
        "tech_tool": "غير مذكور في الشاهد",
        "assessment": "غير مذكور في الشاهد",
        "impact": "غير مذكور في الشاهد",
    }

    if not OPENAI_API_KEY:
        base["goal"] = "لم يتم تفعيل GPT (OPENAI_API_KEY غير موجود)"
        return base

    # قص النص عشان ما يطول ويكسر الطباعة
    ocr_short = (ocr_text or "").strip()
    if len(ocr_short) > 4000:
        ocr_short = ocr_short[:4000] + "..."

    sys = (
        "أنت مساعد تربوي. استخرج معلومات من الشاهد (نص OCR) واكتبها بصيغة رسمية قصيرة. "
        "لا تخترع معلومات غير موجودة. إذا ما لقيت دليل، اكتب: غير مذكور في الشاهد. "
        "أرجع JSON فقط بدون أي شرح."
    )

    user = f"""
التخصص: {subject or "غير محدد"}
اسم البرنامج/النشاط (إن وجد): {program_name or "غير محدد"}
وصف البرنامج (إن وجد): {program_desc or "غير محدد"}

نص الشاهد (OCR):
{ocr_short}

المطلوب JSON بهذه المفاتيح فقط:
goal, procedure, tech_tool, assessment, impact
قواعد:
- كل قيمة سطر واحد قصير (10-20 كلمة تقريباً)
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
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }

        r = requests.post(url, headers=headers, json=payload, timeout=45)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON
        import json
        j = json.loads(content)

        for k in base.keys():
            v = (j.get(k) or "").strip()
            base[k] = v if v else "غير مذكور في الشاهد"

        return base

    except Exception as e:
        # لا نطيّح الصفحة، نخليها تشتغل
        base["goal"] = f"تعذر تحليل GPT حالياً"
        base["procedure"] = "غير مذكور في الشاهد"
        base["tech_tool"] = "غير مذكور في الشاهد"
        base["assessment"] = "غير مذكور في الشاهد"
        base["impact"] = "غير مذكور في الشاهد"
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

    # تاريخ تلقائي (حتى لو ما ارسلت من الفورم)
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

    # GPT Extract
    gpt_data = gpt_extract_fields(
        ocr_text=combined_ocr,
        program_name=program_name,
        program_desc=program_desc,
        subject=subject
    )

    # اخفاء نص OCR (ما نطبعه)
    # نرسله فقط للعرض عند الحاجة (تقدر تخليه مخفي تماماً من الواجهة)
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
