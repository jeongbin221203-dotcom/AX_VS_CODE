from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import main, quiz, speaking, toefl, vocab
    app.register_blueprint(main.bp)
    app.register_blueprint(toefl.bp)
    app.register_blueprint(speaking.bp)
    app.register_blueprint(quiz.bp)
    app.register_blueprint(vocab.bp)
    app.register_blueprint(vocab.bp, url_prefix="/toefl", name="tvocab")   # 토플 학술 어휘 (같은 화면)
