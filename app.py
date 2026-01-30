import os
import io
import time
import base64
import requests
from PIL import Image
from flask import Flask, render_template, request

# OpenAI SDK (v1.x)
from openai import OpenAI

app = Flask(__name__)

OCR_API_KEY = (os.environ.get("OCR_SPACE_KEY") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

client = OpenAI() if OPENAI_API_KEY else None


# -----------------------------
# Helpers
# -----------------------------
def compress_image(file_storage, max_w=1600, quality=78):
    """
    يقلل حجم الصورة قبل إرسالها لـ OCR.space لزيادة السرعة وتقليل التقطيع
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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=35):
    """
    ملاحظة: OCR.space ممكن يطلع E201 لو أرسلت language غير مقبول
    لذلك: لا نرسل language نهائياً (غالباً يتعرف)
    """
    if not OCR_API_KEY:
        return "", "مفتاح OCR_SPACE_KEY غير موجود في Render Environment Variables"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": OCR_API_KEY,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        # لا نرسل language نهائياً لتفادي E201
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

        time.sleep(0.9 * (attempt + 1))

    return "", (last_err or "فشل OCR لسبب غير معروف")


def clean_text(t: str) -> str:
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def make_educational_summary_with_gpt(ocr_text: str, program_name_hint: str = "") -> str:
    """
    يحوّل OCR إلى وصف تربوي رسمي مختصر (بدون اختراع مبالغ).
    """
    t = clean_text(ocr_text)
    if not t:
        return ""

    if not client:
        # fallback بدون GPT
        lines = t.splitlines()[:3]
        snippet = " ".join(lines)
        return f"تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف. (ملخص من الشاهد: {snippet})"

    try:
        prompt = f"""
أنت مساعد تربوي. عندك نص خام مستخرج من صورة (OCR) وقد يكون فيه تشويش.
مطلوب منك: كتابة "وصف البرنامج" بصياغة رسمية مختصرة (سطرين إلى أربعة أسطر) باللغة العربية الفصحى.
شروط مهمة:
- لا تخترع أسماء أو أرقام أو جهات غير موجودة.
- إذا كان النص غير واضح، اكتب وصفاً عاماً مناسباً للشواهد التعليمية دون مبالغة.
- ركّز على: الهدف التعليمي، ماذا تم داخل الصف، وأثره على تعلم الطلاب.
- لا تذكر كلمة OCR ولا تذكر أنك نموذج ذكاء اصطناعي.

تلميح اسم البرنامج (إن وجد): {program_name_hint}

نص الشاهد:
{t}
""".strip()

        # Responses API
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            temperature=0.2,
        )
        out = (resp.output_text or "").strip()
        return out

    except Exception as e:
        # fallback لو GPT تعطل
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
    teacher = (request.form.get("teacher") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    school = (request.form.get("school") or "").strip()
    principal = (request.form.get("principal") or "").strip()
    date = (request.form.get("date") or "").strip()

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

    combined_ocr = "\n".join([x for x in [ocr1_text, ocr2_text] if x]).strip()

    if not program_name:
        program_name = "نشاط/برنامج تعليمي"

    # الوصف: إذا المستخدم ما كتب وصف، خل GPT يبنيه من OCR
    if not program_desc:
        program_desc = make_educational_summary_with_gpt(combined_ocr, program_name_hint=program_name)

    return render_template(
        "result.html",
        teacher=teacher,
        subject=subject,
        school=school,
        principal=principal,
        date=date,
        program_name=program_name,
        program_desc=program_desc,
        img1_url=img1_url,
        img2_url=img2_url,
        ocr_text=combined_ocr,
        ocr1_err=ocr1_err,
        ocr2_err=ocr2_err,
        has_openai=bool(client),
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
