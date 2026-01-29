from flask import Flask, render_template, request
from datetime import datetime

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        teacher = request.form.get('teacher', '')
        subject = request.form.get('subject', '')
        image = request.files.get('image')

        # مؤقتًا بدون OCR عشان نكسر الخطأ
        extracted_text = "سيتم استخراج النص لاحقًا"

        return render_template(
            'result.html',
            teacher=teacher,
            subject=subject,
            date=datetime.now().strftime('%Y-%m-%d'),
            text=extracted_text
        )

    return render_template('index.html')
