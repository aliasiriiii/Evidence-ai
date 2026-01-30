import os
import io
import time
import json
import base64
import requests
from PIL import Image
from flask import Flask, render_template, request

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

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


def ocr_space(image_bytes, filename="image.jpg", retries=2, timeout=30):
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


def evidence_prompt(ocr_text: str) -> str:
    return f"""
أنت خبير تقويم تربوي في وزارة التعليم.

استخرج من نص الشاهد التالي العناصر الخمسة التالية إن وُجدت، وإن لم تُذكر صراحة فاستنتجها استنتاجًا تربويًا منطقيًا دون مبالغة:

- الهدف
- الإجراء/التنفيذ
- الأداة/التقنية
- أسلوب التقويم
- الأثر على المتعلمين

أخرج النتيجة بصيغة JSON فقط، بدون شرح، وبدون أي نص إضافي:

{{
  "goal": "",
  "procedure": "",
  "tool": "",
  "assessment": "",
  "impact": ""
}}

نص الشاهد:
{ocr_text}
""".strip()


def call_openai_json(prompt: str, timeout=45):
    if not OPENAI_API_KEY:
        return "", "مفتاح OPENAI_API_KEY غير موجود في Render Environment Variables"

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "أنت مساعد تربوي متخصص في تقويم الشواهد."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        return text, ""
    except Exception as e:
        return "", f"فشل GPT: {e}"


def parse_gpt_fields(gpt_output: str):
    try:
        # أحيانًا يرجع داخل ```json ... ```
        gpt_output = gpt_output.strip()
        gpt_output = gpt_output.replace("```json", "").replace("```", "").strip()

        data = json.loads(gpt_output)
        return (
            (data.get("goal") or "").strip(),
            (data.get("procedure") or "").strip(),
            (data.get("tool") or "").strip(),
            (data.get("assessment") or "").strip(),
            (data.get("impact") or "").strip(),
        )
    except Exception:
        return "", "", "", "", ""


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

    # GPT استخراج عناصر الملخص
    goal = procedure = tool = assessment = impact = ""
    gpt_raw = ""
    gpt_err = ""

    if combined_ocr:
        gpt_raw, gpt_err = call_openai_json(evidence_prompt(combined_ocr))
        goal, procedure, tool, assessment, impact = parse_gpt_fields(gpt_raw)

    # fallback لو ما خرج شيء
    if not goal: goal = "غير مذكور في الشاهد"
    if not procedure: procedure = "غير مذكور في الشاهد"
    if not tool: tool = "غير مذكور في الشاهد"
    if not assessment: assessment = "غير مذكور في الشاهد"
    if not impact: impact = "غير مذكور في الشاهد"

    if not program_name:
        program_name = "نشاط/برنامج تعليمي"

    if not program_desc:
        program_desc = "تم تنفيذ نشاط/برنامج تعليمي داعم لعملية التعلم داخل الصف."

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
        goal=goal,
        procedure=procedure,
        tool=tool,
        assessment=assessment,
        impact=impact,
        gpt_err=gpt_err,
        gpt_raw=gpt_raw,
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
