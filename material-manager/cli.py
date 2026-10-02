"""운영 명령 (Flask CLI). 앱과 같은 설정·DB로 돈다.

    flask --app app init-db                  # 테이블 만들기·마이그레이션 (앱 시작 때도 자동)
    flask --app app batch --loop             # 배치: 15초마다 주기가 된 작업 실행 (운영: 서비스로 등록, 여러 서버 가능)
    flask --app app batch                    # 주기가 된 작업을 한 번만
    flask --app app batch run sap_sync       # 특정 작업을 지금 실행
    flask --app app batch list               # 작업 목록과 마지막 실행 결과
    flask --app app erp status               # 연결 방식 · 대기열 건수
    flask --app app erp test                 # 연결 확인 (거래는 보내지 않음)
    flask --app app erp send                 # 대기열 지금 전송
    flask --app app erp master-sync          # 자재·원가센터 마스터 지금 받기
    flask --app app backup                   # DB 백업 지금 (SQLite, 검사 통과한 사본만 남김)
    flask --app app storage-flush            # S3 장애 동안 임시 보관한 파일 올리기
    flask --app app sso-outage --hours 4     # SSO 장애 모드 (비밀번호 계정 로그인 임시 허용, --hours 0 = 끄기)
    flask --app app doctor                   # 운영 점검: 실제 DB·저장소·ERP·SSO·백업·배치 연결 (실패 있으면 종료 코드 1)

Windows에서 `flask`가 경로에 없으면 `python -m flask --app app ...`.
"""
import time

import click
from flask import Flask

import config
from core import audit, backup, db, doctor, erp, jobs, master_sync, sap, sso, storage


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():
        """테이블 만들기·마이그레이션."""
        db.init_db()
        click.echo("DB 준비 완료")

    @app.cli.group("batch", invoke_without_command=True)
    @click.option("--loop", is_flag=True, help="계속 실행")
    @click.option("--tick", default=15, show_default=True, help="루프 간격(초)")
    @click.pass_context
    def batch(ctx, loop: bool, tick: int):
        """배치 실행기. 모든 서버에서 켜도 작업마다 한 서버만 실행한다(DB 임대 잠금)."""
        if ctx.invoked_subcommand:
            return
        while True:
            for name, msg in jobs.run_due().items():
                click.echo(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {name} {msg}")
            if not loop:
                break
            time.sleep(tick)

    @batch.command("run")
    @click.argument("job", type=click.Choice(list(jobs.JOBS)))
    def batch_run(job: str):
        """작업 하나를 지금 실행."""
        click.echo(jobs.run(job, force=True))

    @batch.command("list")
    def batch_list():
        """작업 목록과 마지막 실행 결과."""
        click.echo(jobs.status_df().to_string(index=False))

    @app.cli.group("erp")
    def erp_group():
        """ERP·SAP 연동."""

    @erp_group.command("status")
    def erp_status():
        for k, v in erp.settings_view():
            click.echo(f"{k}: {v}")
        counts = sap.summary()
        click.echo("대기열: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "없음"))

    @erp_group.command("test")
    def erp_test():
        """연결 확인 (거래는 보내지 않음). 실패하면 종료 코드 1."""
        result = erp.ping()
        audit.log(None, "ERP_TEST", "erp", config.SAP_MODE, {"ok": result.ok, "via": "cli"})
        click.echo(("연결됨: " if result.ok else "실패: ") + result.message)
        if not result.ok:
            raise SystemExit(1)

    @erp_group.command("send")
    def erp_send():
        """대기열 지금 전송."""
        counts = sap.process_outbox()
        audit.log(None, "SAP_RUN", "sap_outbox", "", {**counts, "via": "cli"})
        click.echo(", ".join(f"{k} {v}" for k, v in counts.items()))

    @erp_group.command("master-sync")
    def erp_master_sync():
        """자재·원가센터 마스터 지금 받기 (MM_SAP_MASTER_SYNC=1 필요)."""
        if not master_sync.enabled():
            raise click.ClickException("마스터 동기화가 꺼져 있습니다 (MM_SAP_MASTER_SYNC=1).")
        try:
            counts = master_sync.sync()
        except erp.ErpError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(", ".join(f"{k} {v}" for k, v in counts.items()))

    @app.cli.command("backup")
    def backup_now():
        """DB 백업 지금 (MM_BACKUP_DIR)."""
        try:
            click.echo(backup.run())
        except Exception as exc:
            raise click.ClickException(f"백업 실패: {exc}") from exc

    @app.cli.command("storage-flush")
    def storage_flush():
        """S3 장애 동안 이 서버에 임시로 둔 파일을 S3로 올린다."""
        store = storage.get()
        if not hasattr(store, "flush"):
            click.echo("로컬 저장소라 올릴 것이 없습니다.")
            return
        try:
            click.echo(store.flush())
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("sso-outage")
    @click.option("--hours", type=float, required=True, help="허용 시간 (0이면 끄기)")
    def sso_outage(hours: float):
        """SSO 장애 모드: 정해진 시간 동안 비밀번호 계정 로그인 허용."""
        until = sso.set_outage(hours, None)
        click.echo(f"SSO 장애 모드: {until}까지" if until else "SSO 장애 모드 해제")

    @app.cli.command("doctor")
    def doctor_cmd():
        """운영 점검 (거래는 보내지 않음). 실패가 있으면 종료 코드 1."""
        checks = doctor.run()
        width = max(len(c.name) for c in checks)
        for c in checks:
            click.echo(f"[{doctor.LABEL[c.status]:^5}] {c.name.ljust(width)}  {c.message}")
        audit.log(None, "DOCTOR", "system", "", {c.name: c.status for c in checks})
        if any(c.status == "fail" for c in checks):
            raise SystemExit(1)
