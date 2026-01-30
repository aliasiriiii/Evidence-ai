import os
import io
import time
import base64
import logging
from datetime import datetime

import requests
from flask import Flask, render_template, request
from PIL import Image, UnidentifiedImageError

app = Flask(__name__)

# ✅ حد أقصى لرفع الملفات (مثلاً 12MB)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

# Logging واضح في Render Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

OCR_API_KEY = (os.environ.get("OCR_SPACE_KEY") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()  # اختياري لو بتضيف GPT لاحقًا

ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png"}
ALLOWED_FILE_MIMES = {"image/jpeg", "image/png", "application/pdf"}


# -----------------------------
# Helpers
# -----------------------------
def is_allowed_mime(mime: str) -> bool:
    return (mime or "").lower() in ALLOWED_FILE_MIMES


def compress_image_bytes(file_storage, max_w=1600, quality=75):
    """
    يحاول يفتح الصورة بـ PIL ويضغطها.
    لو فشل (مثلاً HEIC) يرجع (None, error_message)
    """
    filename = file_storage.filename or "upload.jpg"
    mime = (file_storage.mimetype or "").lower()

    # اقرأ البايتس مرة واحدة
    raw = file_storage.read()
    file_storage.seek(0)

    if mime not in ALLOWED_IMAGE_MIMES:
        return None, None, f"صيغة الصورة غير مدعومة ({mime}). ارفع JPG أو PNG فقط."

    try:
        img = Image.open(io.BytesIO(raw))

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        w, h = img.size
        if w > max_w:
            new_h = int(h * (max_w / w))
            img = img.resize((max_w, new_h))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        buf.seek(0)

        return filename, buf.getvalue(), ""
    except UnidentifiedImageError:
        return None, None, "ما قدرت أقرأ الصورة. غالبًا صيغة HEIC. حوّلها إلى JPG/PNG ثم ارفعها."
    except Exception as e:
        return None, None, f"خطأ أثناء معالجة الصورة: {e}"


def ocr_space_bytes(file_bytes: bytes, filename="file.jpg", timeout=35, retries=2):
    """
    OCR.space
    مهم: لا نرسل language لتفادي E201
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا ترسل language
    }

    files = {"filename": (filename, file_bytes)}
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

        time.sleep(0.9 * (attempt + 1))

    return "", (last_err or "فشل OCR لسبب غير معروف")


def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def auto_description_from_ocr(ocr_text: str) -> str:
    t = clean_text(ocr_text)
    if not t:
        return ""
    lines = t.splitlines()[:3]
    snippet = " ".join(lines)
    return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {snippet})"


# -----------------------------
# Error Handlers (بدل 500)
# -----------------------------
@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", message="حجم الملف كبير جدًا. جرّب صورة أصغر من 12MB."), 413


@app.errorhandler(500)
def server_error(e):
    logging.exception("SERVER 500 ERROR")
    return render_template("error.html", message="صار خطأ داخلي أثناء المعالجة. افتح Render Logs وشوف السبب."), 500


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    try:
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

        ocr1_text, ocr1_err = "", ""
        ocr2_text, ocr2_err = "", ""

        # ---- Image 1
        if img1 and img1.filename:
            mime1 = (img1.mimetype or "").lower()
            if not is_allowed_mime(mime1):
                return render_template("error.html", message=f"ملف (صورة 1) غير مدعوم: {mime1}. ارفع JPG/PNG/PDF."), 400

            if mime1 in ALLOWED_IMAGE_MIMES:
                fn, bts, err = compress_image_bytes(img1)
                if err:
                    return render_template("error.html", message=f"صورة 1: {err}"), 400

                img1_url = "data:image/jpeg;base64," + base64.b64encode(bts).decode("utf-8")
                ocr1_text, ocr1_err = ocr_space_bytes(bts, filename=fn or "image1.jpg")
            else:
                # PDF: نرسله مباشرة لـ OCR (OCR.space يدعمه)
                raw = img1.read()
                img1.seek(0)
                ocr1_text, ocr1_err = ocr_space_bytes(raw, filename=img1.filename or "file1.pdf")

        # ---- Image 2
        if img2 and img2.filename:
            mime2 = (img2.mimetype or "").lower()
            if not is_allowed_mime(mime2):
                return render_template("error.html", message=f"ملف (صورة 2) غير مدعوم: {mime2}. ارفع JPG/PNG/PDF."), 400

            if mime2 in ALLOWED_IMAGE_MIMES:
                fn, bts, err = compress_image_bytes(img2)
                if err:
                    return render_template("error.html", message=f"صورة 2: {err}"), 400

                img2_url = "data:image/jpeg;base64," + base64.b64encode(bts).decode("utf-8")
                ocr2_text, ocr2_err = ocr_space_bytes(bts, filename=fn or "image2.jpg")
            else:
                raw = img2.read()
                img2.seek(0)
                ocr2_text, ocr2_err = ocr_space_bytes(raw, filename=img2.filename or "file2.pdf")

        ocr1_text = clean_text(ocr1_text)
        ocr2_text = clean_text(ocr2_text)

        if not program_desc:
            combined = "\n".join([t for t in [ocr1_text, ocr2_text] if t])
            program_desc = auto_description_from_ocr(combined)

        if not program_name:
            program_name = "نشاط/برنامج تعليمي"

        today = datetime.now().strftime("%Y/%m/%d")

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
            today=today
        )
    except Exception as e:
        logging.exception("Generate failed")
        return render_template("error.html", message=f"صار خطأ أثناء التوليد: {e}"), 500


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
