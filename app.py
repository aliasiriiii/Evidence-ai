import os
import requests
from flask import Flask, render_template, request

app = Flask(__name__)

OCR_API_KEY = os.getenv("OCR_SPACE_KEY", "").strip()

def ocr_space_image(file_storage):
    """
    Sends an image to OCR.space and returns extracted text.
    """
    if not OCR_API_KEY:
        return "خطأ: لم يتم ضبط OCR_SPACE_KEY في Render."

    # OCR.space expects multipart 'file'
    url = "https://api.ocr.space/parse/image"
    payload = {
        "apikey": OCR_API_KEY,
        "language": "ara",          # Arabic
        "isOverlayRequired": False,
        "OCREngine": 2,             # غالباً أدق
        "scale": True,
    }

    files = {
        "file": (file_storage.filename, file_storage.stream, file_storage.mimetype)
    }

    try:
        r = requests.post(url, data=payload, files=files, timeout=60)
        r.raise_for_status()
        data = r.json()

        if data.get("IsErroredOnProcessing"):
            err = data.get("ErrorMessage") or data.get("ErrorDetails") or "خطأ غير معروف من OCR.space"
            return f"فشل OCR: {err}"

        parsed = data.get("ParsedResults", [])
        if not parsed:
            return "لم يتم استخراج نص (ParsedResults فاضي)."

        text = parsed[0].get("ParsedText", "").strip()
        return text if text else "تمت المعالجة لكن النص المستخرج فارغ."
    except Exception as e:
        return f"خطأ اتصال OCR: {str(e)}"


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/generate")
def generate():
    # بيانات البطاقة
    teacher_name = request.form.get("teacher_name", "").strip()
    subject_name = request.form.get("subject_name", "").strip()
    school_name  = request.form.get("school_name", "").strip()
    leader_name  = request.form.get("leader_name", "").strip()
    program_name = request.form.get("program_name", "").strip()

    # صور (اختياري)
    img1 = request.files.get("image1")
    img2 = request.files.get("image2")

    extracted_1 = ""
    extracted_2 = ""

    # شغّل OCR فقط إذا فيه ملف فعلاً
    if img1 and img1.filename:
        extracted_1 = ocr_space_image(img1)

    if img2 and img2.filename:
        extracted_2 = ocr_space_image(img2)

    # وصف البرنامج (ذكي): إذا المستخدم ما كتب وصف، خله من OCR
    program_desc = request.form.get("program_desc", "").strip()
    if not program_desc:
        # نجمع النصوص المستخرجة ونقصها لو طويلة
        combined = "\n\n".join([t for t in [extracted_1, extracted_2] if t]).strip()
        if combined:
            # اختصار بسيط عشان ما يصير طويل جدًا في البطاقة
            program_desc = combined[:900]
        else:
            program_desc = "تم تنفيذ نشاط تعليمي داعم لعملية التعلم داخل الصف."

    return render_template(
        "result.html",
        teacher_name=teacher_name,
        subject_name=subject_name,
        school_name=school_name,
        leader_name=leader_name,
        program_name=program_name,
        program_desc=program_desc,
        extracted_1=extracted_1,
        extracted_2=extracted_2,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
