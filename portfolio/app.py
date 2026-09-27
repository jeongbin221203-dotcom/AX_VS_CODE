import os
from flask import Flask, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

@app.route('/')
def index():
    kakao_rest_key = os.getenv('KAKAO_REST_API_KEY', '')
    return render_template('index.html', kakao_rest_key=kakao_rest_key)

@app.route('/sub1')
def sub1():
    return render_template('sub1.html')

# 이 부분이 반드시 포함되어 있어야 합니다!
@app.route('/sub1/detail/<category>')
def sub1_detail(category):
    return render_template('sub1_detail.html', category=category)

@app.route('/sub2')
def sub2():
    return render_template('sub2.html')

@app.route('/sub3')
def sub3():
    return render_template('sub3.html')

@app.route('/sub4')
def sub4():
    return render_template('sub4.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)