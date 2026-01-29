from flask import Flask, render_template, request
import requests
import os

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_SPACE_KEY")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html")

    if "image" not in request.files:
        return "لم يتم رفع صورة"

    image = request.files["image"]

    try:
        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": image},
            data={
                "apikey": OCR_API_KEY,
                "language": "ara"
            },
            timeout=15  # ⏱ يمنع التعليق
        )

        result = response.json()

        if result.get("IsErroredOnProcessing"):
            text = "تعذر قراءة النص من الصورة."
        else:
            text = result["ParsedResults"][0]["ParsedText"]

    except requests.exceptions.Timeout:
        text = "تم رفع الصورة بنجاح، لكن القراءة تأخرت. حاول بصورة أصغر."

    except Exception as e:
        text = "حدث خطأ أثناء المعالجة."

    return render_template(
        "result.html",
        text=text,
        date=request.form.get("date"),
        lesson=request.form.get("lesson"),
        students=request.form.get("students"),
        count=request.form.get("count")
    )

if __name__ == "__main__":
    app.run(debug=True)
