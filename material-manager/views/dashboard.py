"""대시보드 화면."""

from datetime import date, timedelta

import pandas as pd
from flask import Blueprint, g

import config
from core import repository as repo, services
from views.helpers import Table, chart, render_page
from views.stock import STOCK_FMT

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    stock = repo.stock_df(wh_ids=g.wh_ids)
    if stock.empty:
        return render_page("dashboard.html", "dashboard", empty=True)

    shortage = stock[stock["shortage"]].sort_values("shortage_qty", ascending=False)
    month_tx = repo.monthly_tx_count(date.today().replace(day=1).isoformat(), g.wh_ids)

    since = (date.today() - timedelta(days=29)).isoformat()
    trend = repo.trend_df(since, g.wh_ids)
    trend_chart = None
    if not trend.empty:
        days = pd.date_range(since, date.today()).strftime("%Y-%m-%d")
        pivot = (trend.pivot(index="tx_date", columns="tx_type", values="qty")
                 .reindex(days).fillna(0))
        trend_chart = chart([d[5:] for d in days],
                            {config.TX_LABEL[t]: pivot[t] for t in ("IN", "OUT") if t in pivot})

    by_cat = stock.groupby("category")["stock_value"].sum().sort_values(ascending=False)
    lots = repo.stock_by_lot(wh_ids=g.wh_ids)
    lots = lots[lots["days_left"].notna()]
    expired = lots[lots["days_left"] < 0]
    expiring = lots[(lots["days_left"] >= 0) & (lots["days_left"] <= 30)]

    return render_page(
        "dashboard.html", "dashboard", empty=False,
        material_cnt=len(stock), stock_value=stock["stock_value"].sum(),
        shortage_cnt=len(shortage), month_in=month_tx.get("IN", 0), month_out=month_tx.get("OUT", 0),
        trend_chart=trend_chart,
        cat_chart=chart(by_cat.index, {"재고금액": by_cat.values}, money=True),
        shortage=Table(services.stock_display(shortage), STOCK_FMT),
        expired_cnt=len(expired), expiring_cnt=len(expiring),
        expiry=Table(pd.concat([expired, expiring])[["code", "name", "wh_code", "lot_no", "expiry_date", "days_left", "stock"]]
                     .rename(columns={"code": "자재코드", "name": "자재명", "wh_code": "창고", "lot_no": "로트",
                                      "expiry_date": "유효기한", "days_left": "남은 일수", "stock": "현재고"}),
                     {"현재고": "{:,.2f}", "남은 일수": "{:,.0f}"},
                     tones=["danger"] * len(expired) + ["warn"] * len(expiring)),
    )
