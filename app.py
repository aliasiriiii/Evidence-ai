import os
import io
import time
import requests
from PIL import Image
from flask import Flask, render_template, request

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()

# -----------------------------
# Helpers
# -----------------------------
def compress_image(file_storage, max_w=1600, quality=75):
    """
    يقلل حجم الصورة قبل إرسالها لـ OCR.space عشان السرعة + يقلل تعليق الشبكة
    يرجع (filename, bytes, mimetype)
    """
    filename = file_storage.filename or "upload.jpg"
    img = Image.open(file_storage.stream)

    # تحويل إلى RGB لو PNG فيها ألفا
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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=30):
    """
    OCR.space لا يقبل ara كلغة (يطلع E201)
    الحل الأفضل: لا ترسل language أصلاً (يتعرف على العربي تلقائياً غالباً)
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا نرسل language نهائياً لتجنب E201
    }
    files = {
        "filename": (filename, image_bytes, "image/jpeg")
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, data=data, files=files, timeout=timeout)
            r.raise_for_status()
            j = r.json()

            if j.get("IsErroredOnProcessing"):
                msg = ""
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
            text = text.strip()
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
    # تنظيف بسيط: سطور فاضية كثيرة
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def auto_description_from_ocr(ocr_text: str) -> str:
    """
    يطلع وصف رسمي مختصر من النص المستخرج (بدون GPT)
    """
    t = clean_text(ocr_text)
    if not t:
        return ""
    # خذ أول 2-3 أسطر كفكرة
    lines = t.splitlines()[:3]
    snippet = " ".join(lines)
    return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {snippet})"


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    # بيانات النموذج
    teacher = (request.form.get("teacher") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    school = (request.form.get("school") or "").strip()
    principal = (request.form.get("principal") or "").strip()
    program_name = (request.form.get("program_name") or "").strip()
    program_desc = (request.form.get("program_desc") or "").strip()

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = None
    img2_url = None

    # نخزن الصور كـ base64 داخل الصفحة (بدون ملفات على السيرفر) عشان يكون سهل على Render
    import base64

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

    # لو وصف البرنامج فاضي، عبّه تلقائي من OCR
    if not program_desc:
        combined = "\n".join([t for t in [ocr1_text, ocr2_text] if t])
        program_desc = auto_description_from_ocr(combined)

    # لو اسم البرنامج فاضي، حاول تلميح بسيط
    if not program_name:
        program_name = "نشاط/برنامج تعليمي"

    return render_template(
        "result.html",
        teacher=teacher,
        subject=subject,
        school=school,
        principal=principal,
        program_name=program_name,
        program_desc=program_desc,
        img1_url=img1_url,
        img2_url=img2_url,
        ocr1_text=ocr1_text,
        ocr2_text=ocr2_text,
        ocr1_err=ocr1_err,
        ocr2_err=ocr2_err,
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
