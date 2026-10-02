from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import applications, collect, jobs, main, profile
    app.register_blueprint(main.bp)
    app.register_blueprint(jobs.bp)
    app.register_blueprint(collect.bp)
    app.register_blueprint(profile.bp)
    app.register_blueprint(applications.bp)
