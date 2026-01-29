import os
import requests
from flask import Flask, request, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)
OCR_SPACE_KEY = os.getenv("OCR_SPACE_KEY", "")

HTML = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>منصة الشاهد الذكي</title>
</head>
<body style="font-family:system-ui">
<h2>رفع صورة الشاهد</h2>
<form method="post" enctype="multipart/form-data">
<input type="file" name="image" required>
<button type="submit">تحليل</button>
</form>
{% if text %}
<hr>
<h3>النص المستخرج:</h3>
<pre>{{ text }}</pre>
{% endif %}
</body>
</html>
"""

def ocr_space(image_bytes, filename):
    r = requests.post(
        "https://api.ocr.space/parse/image",
        files={"filename": (filename, image_bytes)},
        data={"apikey": OCR_SPACE_KEY, "language": "ara"},
        timeout=60
    )
    j = r.json()
    if j.get("ParsedResults"):
        return j["ParsedResults"][0].get("ParsedText", "")
    return "لم يتم استخراج نص"

@app.route("/", methods=["GET", "POST"])
def index():
    text = None
    if request.method == "POST":
        f = request.files["image"]
        text = ocr_space(f.read(), secure_filename(f.filename))
    return render_template_string(HTML, text=text)

if __name__ == "__main__":
    app.run()
