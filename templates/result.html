import os
from datetime import datetime
from PIL import Image
import io

app = Flask(__name__)

# =========================
# الإعدادات الثابتة
# =========================
SCHOOL_NAME = "ثانوية الظهران"
PRINCIPAL_NAME = "أحمد الشمراني"
DEFAULT_TEACHER = "علي عبدالله علي عسيري"

OCR_API_KEY = os.getenv("OCR_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# =========================
# OCR
# =========================
def extract_text_from_image(image_bytes):
    response = requests.post(
        "https://api.ocr.space/parse/image",
        files={"file": ("image.png", image_bytes)},
        data={
            "apikey": OCR_API_KEY,
            "language": "ara",
        },
        timeout=25
    )
    result = response.json()
    try:
        return result["ParsedResults"][0]["ParsedText"]
    except:
        return ""

# =========================
# GPT (مرة وحدة فقط)
# =========================
def analyze_with_gpt(all_text):
    prompt = f"""
أنت خبير تربوي محترف.
حلّل الشواهد التالية (حتى لو كانت غير واضحة أو ناقصة)،
ولا تترك أي عنصر فارغ.

استنتج بشكل ذكي ومهني:
- وصف برنامج تعليمي متكامل
- الهدف
- آلية التنفيذ
- التقنيات المستخدمة
- أسلوب التقويم
- الأثر التعليمي

اكتب بلغة عربية واضحة، قوية، رسمية، ومفهومة للمشرف.

النص المستخرج:
{all_text}
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "أنت خبير تقويم تربوي."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30
    )

    data = response.json()
    return data["choices"][0]["message"]["content"]

# =========================
# Routes
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    images = request.files.getlist("images")

    all_text = ""
    for img in images:
        img_bytes = img.read()
        text = extract_text_from_image(img_bytes)
        all_text += "\n" + text

    if not all_text.strip():
        all_text = "نشاط تعليمي داخل الصف لدعم تعلم الطلاب باستخدام أدوات متنوعة."

    gpt_result = analyze_with_gpt(all_text)

    teacher_name = request.form.get("teacher_name") or DEFAULT_TEACHER

    return render_template(
        "result.html",
        date=datetime.now().strftime("%Y/%m/%d"),
        school=SCHOOL_NAME,
        principal=PRINCIPAL_NAME,
        teacher=teacher_name,
        result=gpt_result
    )

# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
