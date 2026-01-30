import os
import io
import time
import base64
import requests
import openai

from PIL import Image
from flask import Flask, render_template, request

app = Flask(__name__)

# مفاتيح البيئة من Render
OCR_API_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
openai.api_key = OPENAI_API_KEY


# -----------------------------
# Helpers
# -----------------------------
def compress_image(file_storage, max_w=1600, quality=75):
    """
    يقلل حجم الصورة قبل إرسالها لـ OCR.space عشان السرعة ويقلل مشاكل timeout.
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
    OCR.space
    ملاحظة: لا ترسل language عشان ما يطلع E201.
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render"

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

            text = (parsed[0].get("ParsedText", "") or "").strip()
            return text, ""

        except requests.exceptions.Timeout:
            last_err = "Timeout: OCR.space تأخر بالرد"
        except Exception as e:
            last_err = f"خطأ OCR: {e}"

        time.sleep(0.8 * (attempt + 1))

    return "", (last_err or "فشل OCR لسبب غير معروف")


def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def gpt_make_program_name_and_desc(ocr_text: str):
    """
    GPT يحول النص المستخرج لوصف تربوي رسمي + يقترح اسم نشاط.
    لو ما فيه مفتاح GPT يرجع fallback.
    """
    ocr_text = clean_text(ocr_text)
    if not ocr_text:
        return "", ""

    if not OPENAI_API_KEY:
        # fallback بدون GPT
        name = "نشاط/برنامج تعليمي"
        desc = "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."
        return name, desc

    prompt = f"""
أنت مختص توثيق شواهد تربوية.
أمامك نص مستخرج من شاهد (OCR).
أخرج لي:
1) اسم برنامج/نشاط مناسب (سطر واحد فقط).
2) وصف برنامج رسمي مختصر (سطرين إلى ثلاثة) بصياغة تربوية.

النص:
{ocr_text}

مهم:
- لا تذكر أي معلومات غير موجودة بالنص.
- لا تخترع أسماء أشخاص.
- ركّز على أنه شاهد تربوي مرتبط بالتعليم/التعلم.
أخرج النتيجة بهذا الشكل بالضبط:
NAME: ...
DESC: ...
"""

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "أنت مساعد يكتب توثيق تربوي رسمي مختصر ودقيق."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = resp["choices"][0]["message"]["content"].strip()

        name = ""
        desc = ""
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("NAME:"):
                name = line.replace("NAME:", "").strip()
            elif line.startswith("DESC:"):
                desc = line.replace("DESC:", "").strip()

        # لو GPT ما التزم بالشكل
        if not name:
            name = "نشاط/برنامج تعليمي"
        if not desc:
            desc = "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."

        return name, desc

    except Exception as e:
        # fallback إذا GPT فشل
        name = "نشاط/برنامج تعليمي"
        desc = f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (تعذر GPT: {e})"
        return name, desc


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

    program_name = (request.form.get("program_name") or "").strip()
    program_desc = (request.form.get("program_desc") or "").strip()

    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    img1_url = None
    img2_url = None

    ocr1_text, ocr1_err = "", ""
    ocr2_text, ocr2_err = "", ""

    # OCR + عرض الصور داخل الصفحة
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

    # إذا المستخدم ما كتب اسم/وصف → خذ من GPT بناءً على OCR
    if (not program_name) or (not program_desc):
        gpt_name, gpt_desc = gpt_make_program_name_and_desc(combined_ocr)
        if not program_name:
            program_name = gpt_name
        if not program_desc:
            program_desc = gpt_desc

    # أخفاء النص (OCR) في البطاقة: نخليه قسم مطوي (details) بالـ HTML
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
