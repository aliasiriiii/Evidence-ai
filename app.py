import os
import io
import time
import base64
import requests
from PIL import Image
from flask import Flask, render_template, request

# OpenAI (الإصدار الجديد)
from openai import OpenAI

# --------------------------------
# App
# --------------------------------
app = Flask(__name__)

# المفاتيح من Render Environment Variables
OCR_API_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()
# لا نقرأ المفتاح يدويًا، OpenAI يقرأه تلقائيًا
client = OpenAI()

# --------------------------------
# Helpers
# --------------------------------
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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=30):
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا نرسل language لتجنب E201
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
                errs = j.get("ErrorMessage")
                if isinstance(errs, list):
                    return "", " | ".join(errs)
                return "", str(errs)

            parsed = j.get("ParsedResults", [])
            if not parsed:
                return "", "OCR رجّع نتيجة فاضية"

            text = parsed[0].get("ParsedText", "") or ""
            return text.strip(), ""

        except requests.exceptions.Timeout:
            last_err = "Timeout مع OCR"
        except Exception as e:
            last_err = f"OCR Error: {e}"

        time.sleep(0.8 * (attempt + 1))

    return "", last_err or "فشل OCR"


def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    return "\n".join(lines)


def gpt_refine_educational_evidence(ocr_text: str) -> str:
    """
    GPT يحوّل النص المستخرج إلى وصف شاهد تربوي رسمي
    """
    if not ocr_text:
        return ""

    prompt = f"""
حوّل النص التالي إلى وصف شاهد تربوي رسمي مختصر
مناسب لبطاقة توثيق معلم، بدون أسماء طلاب،
وبلغة عربية رسمية واضحة:

النص:
{ocr_text}
"""

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "أنت مساعد تربوي متخصص في توثيق الشواهد التعليمية."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        return f"فشل GPT: {e}"


# --------------------------------
# Routes
# --------------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    teacher = request.form.get("teacher", "").strip()
    subject = request.form.get("subject", "").strip()
    school = request.form.get("school", "").strip()
    principal = request.form.get("principal", "").strip()
    program_name = request.form.get("program_name", "").strip()
    program_desc = request.form.get("program_desc", "").strip()

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = img2_url = None
    ocr_text_all = []

    if img1 and img1.filename:
        fn, bts, mt = compress_image(img1)
        img1_url = f"data:{mt};base64," + base64.b64encode(bts).decode()
        t, _ = ocr_space(bts, fn)
        if t:
            ocr_text_all.append(t)

    if img2 and img2.filename:
        fn, bts, mt = compress_image(img2)
        img2_url = f"data:{mt};base64," + base64.b64encode(bts).decode()
        t, _ = ocr_space(bts, fn)
        if t:
            ocr_text_all.append(t)

    ocr_text = clean_text("\n".join(ocr_text_all))

    # لو الوصف فاضي → GPT
    if not program_desc:
        program_desc = gpt_refine_educational_evidence(ocr_text)

    if not program_name:
        program_name = "نشاط / برنامج تعليمي"

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
        ocr_text=ocr_text,
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
