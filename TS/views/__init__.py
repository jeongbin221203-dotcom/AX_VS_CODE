from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import main, quiz, vocab
    app.register_blueprint(main.bp)
    app.register_blueprint(quiz.bp)
    app.register_blueprint(vocab.bp)
