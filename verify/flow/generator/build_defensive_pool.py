#!/usr/bin/env python3
"""避险观察池页面生成器。

只读冻结的 /workspace/ashare_digest/build_page.py（encrypt_bundle、site_salt、PBKDF2_ITERS）。
不读取 ops-hub，不改 V3.3 / 早盘 / 晚间账本，不把观察 JSON 写进公开仓库。

  python3 build_defensive_pool.py --check --latest web/latest.json --history web/history.json
  python3 build_defensive_pool.py --preview /tmp/defensive-pool-preview --latest web/latest.json
  VERIFY_DASHBOARD_PASSWORD=... python3 build_defensive_pool.py --publish --latest ... --history ...
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(HERE, "template.html")
DIGEST_DEFAULT = "/workspace/ashare_digest"
PAGES_DEFAULT = "/workspace/ashare_digest/pages"
LOCK_DEFAULT = "/workspace/locks/ashare_hot_pages.flock"
SUB_PATH = "verify/flow/index.html"
TZ = dt.timezone(dt.timedelta(hours=8))
PBKDF2_ITERS = 600000
SCHEMA = "defensive-watch-daily/1.0"
AUTHOR_NAME = "riollejason-spec"
AUTHOR_EMAIL = "riollejason-spec@users.noreply.github.com"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORBIDDEN_KEYS = {"password", "passwd", "token", "secret", "authorization", "tushare_token", "verify_dashboard_password"}
BANNED_FIELDS = {"total_score", "score", "composite_score", "buy_score"}


class Reject(Exception):
    pass


def die(msg, code=1):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(code)


def scrub(s):
    return re.sub(r"x-access-token:[^@\s]*@", "x-access-token:***@", s or "")


def load_json(path):
    if not path:
        return None
    if os.path.basename(path).startswith(".env"):
        die("refuse to read an env file")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise Reject(os.path.basename(path) + " 必须是对象")
    return data


def walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in FORBIDDEN_KEYS or lk in BANNED_FIELDS:
                raise Reject("不允许的字段 " + path + str(k))
            walk_keys(v, path + str(k) + ".")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_keys(v, path + "[%d]." % i)


def num(v, path):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Reject(path + " 必须是数字或 null")
    if v != v or v in (float("inf"), float("-inf")):
        raise Reject(path + " 不是有限数字")
    return v


def num_or_none(v, path):
    if v is None:
        return None
    return num(v, path)


def need_str(v, path):
    if not isinstance(v, str) or not v.strip():
        raise Reject(path + " 必须是非空字符串")
    return v


def need_date(v, path):
    if not isinstance(v, str) or not DATE_RE.fullmatch(v):
        raise Reject(path + " 必须是 YYYY-MM-DD")
    return v


def opt_int(v, path):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise Reject(path + " 必须是整数或 null")
    return v


def bench_slot(b):
    blob = (b.get("role") or "") + (b.get("name") or "")
    kind = b.get("kind") or ""
    if "黄金" in blob or kind == "fund":
        return "gold"
    if "成长" in blob:
        return "growth"
    if "大盘" in blob or "沪深300" in (b.get("name") or ""):
        return "market"
    return "other"


def tri_state(v):
    if v == "unreviewed":
        return "unreviewed"
    if v is False or v in ("none", "no", "clear"):
        return "clear"
    if v is True or (isinstance(v, str) and v.strip()):
        return "present"
    return "missing"


def banner_for(pool_status, mode):
    protected = pool_status == "proposed_for_confirmation" or mode in ("candidate_baseline", "backfill")
    if pool_status == "confirmed" and mode == "confirmed" and not protected:
        return {"tone": "final", "title": "名单已定稿", "lines": ["定稿版本。仍不是买入名单，也没有总分。"]}
    if protected or pool_status != "confirmed":
        lines = ["建议采用，待定稿。", "候选核验中，保护保持。不是买入名单。"]
        if pool_status not in ("proposed_for_confirmation", "confirmed"):
            lines.insert(0, "名单状态未识别，不视为定稿。")
        return {"tone": "candidate", "title": "候选核验", "lines": lines}
    return {"tone": "candidate", "title": "候选核验", "lines": ["状态组合不能解除保护。", "建议采用，待定稿。"]}


def finality_view(doc):
    code = doc.get("data_finality")
    vendor = doc.get("provider_finality_flag_available") is True
    if code == "post_schedule_snapshot_verified_20of20":
        text = "19点后复核已完成，池内原始资金字段与初次快照一致。这不是供应商终值保证。"
    elif code == "pre_official_19h_snapshot_recheck_required":
        text = "仍是19点前快照，需要在官方更新后再复核。不是供应商终值。"
    elif isinstance(code, str) and code:
        text = "终态标记为 " + code + "。不把它写成供应商终值保证。"
    else:
        text = "没有终态标记。不把它写成供应商终值保证。"
    return {"code": code, "text": text, "vendor_final": vendor}


def norm_window(w, path):
    if not isinstance(w, dict):
        raise Reject(path + " 必须是对象")
    expected = w.get("expected_days")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise Reject(path + ".expected_days 无效")
    flow_days = w.get("flow_days")
    price_days = w.get("price_days")
    if isinstance(flow_days, bool) or not isinstance(flow_days, int) or flow_days < 0:
        raise Reject(path + ".flow_days 无效")
    if isinstance(price_days, bool) or not isinstance(price_days, int) or price_days < 0:
        raise Reject(path + ".price_days 无效")
    complete = w.get("complete")
    if not isinstance(complete, bool):
        raise Reject(path + ".complete 必须是布尔值")
    if complete and flow_days < expected:
        raise Reject(path + " 标成完整但资金天数不足")
    inflow = opt_int(w.get("inflow_days"), path + ".inflow_days")
    if inflow is not None and (inflow < 0 or inflow > flow_days):
        raise Reject(path + ".inflow_days 不能大于有资金的天数，也不能把缺口算进去")
    cumulative = num_or_none(w.get("cumulative_net_yuan"), path + ".cumulative_net_yuan")
    amount = num_or_none(w.get("amount_yuan"), path + ".amount_yuan")
    ratio = num_or_none(w.get("net_to_amount_pct"), path + ".net_to_amount_pct")
    if (not complete or flow_days < expected) and (cumulative is not None or ratio is not None):
        raise Reject(path + " 窗口不完整时累计净额和占比必须为 null")
    return {
        "expected_days": expected,
        "price_days": price_days,
        "flow_days": flow_days,
        "inflow_days": inflow,
        "cumulative_net_yuan": cumulative,
        "amount_yuan": amount,
        "net_to_amount_pct": ratio,
        "complete": complete,
    }


def norm_interference(raw):
    raw = raw or {}
    if not isinstance(raw, dict):
        raise Reject("interference 必须是对象")
    changed = raw.get("adjustment_factor_changed")
    if changed is not None and not isinstance(changed, bool):
        raise Reject("adjustment_factor_changed 必须是布尔值或 null")
    return {
        "adjustment_factor_changed": changed,
        "repurchase": {"state": tri_state(raw.get("repurchase"))},
        "major_announcement": {"state": tri_state(raw.get("major_announcement"))},
        "index_rebalance": {"state": tri_state(raw.get("index_rebalance"))},
        "review_status": raw.get("review_status") if isinstance(raw.get("review_status"), str) else "",
        "note": raw.get("note") if isinstance(raw.get("note"), str) else "",
    }


def norm_stock(raw, trade_date, synthetic, index):
    path = "stocks[%d]" % index
    if not isinstance(raw, dict):
        raise Reject(path + " 必须是对象")
    code = need_str(raw.get("code"), path + ".code")
    if synthetic:
        if not code.startswith("SYN"):
            raise Reject(path + " 合成样本代码必须以 SYN 开头")
    elif not re.fullmatch(r"\d{6}", code):
        raise Reject(path + ".code 必须是六位数字")
    windows_in = raw.get("windows")
    if not isinstance(windows_in, dict):
        raise Reject(path + ".windows 缺失")
    windows = {}
    for key in ("3", "5", "10"):
        if key not in windows_in:
            raise Reject(path + ".windows 缺少 " + key)
        windows[key] = norm_window(windows_in[key], path + ".windows." + key)
    mf = raw.get("moneyflow") if isinstance(raw.get("moneyflow"), dict) else {}
    cn = mf.get("class_net_yuan") if isinstance(mf.get("class_net_yuan"), dict) else {}
    date = need_date(raw.get("date"), path + ".date")
    date_match = date == trade_date
    pe = raw.get("price_evidence") if isinstance(raw.get("price_evidence"), dict) else {}
    return {
        "code": code,
        "ts_code": raw.get("ts_code") if isinstance(raw.get("ts_code"), str) else "",
        "name": need_str(raw.get("name"), path + ".name"),
        "sector": need_str(raw.get("sector"), path + ".sector"),
        "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else "",
        "risk": raw.get("risk") if isinstance(raw.get("risk"), str) else "",
        "weight": num_or_none(raw.get("weight"), path + ".weight"),
        "date": date,
        "date_match": date_match,
        "status": raw.get("status") if isinstance(raw.get("status"), str) else "",
        "net_flow_yuan": num_or_none(raw.get("net_flow_yuan"), path + ".net_flow_yuan"),
        "net_flow_to_amount_pct": num_or_none(raw.get("net_flow_to_amount_pct"), path + ".net_flow_to_amount_pct"),
        "return_1d_pct": num_or_none(raw.get("return_1d_pct"), path + ".return_1d_pct"),
        "return_5d_pct": num_or_none(raw.get("return_5d_pct"), path + ".return_5d_pct"),
        "return_20d_pct": num_or_none(raw.get("return_20d_pct"), path + ".return_20d_pct"),
        "amount_yuan": num_or_none(raw.get("amount_yuan"), path + ".amount_yuan"),
        "turnover_pct": num_or_none(raw.get("turnover_pct"), path + ".turnover_pct"),
        "excess_1d_vs_csi300_pp": num_or_none(raw.get("excess_1d_vs_csi300_pp"), path + ".excess_1d"),
        "ohlc": {
            "open": num_or_none(raw.get("open"), path + ".open"),
            "high": num_or_none(raw.get("high"), path + ".high"),
            "low": num_or_none(raw.get("low"), path + ".low"),
            "close": num_or_none(raw.get("close"), path + ".close"),
            "pre_close": num_or_none(raw.get("pre_close"), path + ".pre_close"),
        },
        "windows": windows,
        "moneyflow": {
            "net_mf_amount": num_or_none(mf.get("net_mf_amount"), path + ".net_mf_amount"),
            "class_net_yuan": {
                "sm": num_or_none(cn.get("sm"), path + ".sm"),
                "md": num_or_none(cn.get("md"), path + ".md"),
                "lg": num_or_none(cn.get("lg"), path + ".lg"),
                "elg": num_or_none(cn.get("elg"), path + ".elg"),
            },
            "large_pair_net_yuan": num_or_none(mf.get("large_pair_net_yuan"), path + ".large_pair"),
        },
        "price_evidence": {
            "low_5d_vs_previous5_pct": num_or_none(pe.get("low_5d_vs_previous5_pct"), path + ".low5"),
            "max_drawdown_10d_pct": num_or_none(pe.get("max_drawdown_10d_pct"), path + ".dd"),
            "volume_spike_1d": pe.get("volume_spike_1d") if isinstance(pe.get("volume_spike_1d"), bool) else None,
            "flow_price_conflict_5d": pe.get("flow_price_conflict_5d") if isinstance(pe.get("flow_price_conflict_5d"), bool) else None,
            "provisional_accumulation_candidate": pe.get("provisional_accumulation_candidate") if isinstance(pe.get("provisional_accumulation_candidate"), bool) else None,
        },
        "interference": norm_interference(raw.get("interference")),
    }


def norm_benchmark(raw, trade_date, index):
    path = "benchmarks[%d]" % index
    if not isinstance(raw, dict):
        raise Reject(path + " 必须是对象")
    date = raw.get("date")
    if date is not None:
        date = need_date(date, path + ".date")
    fresh = date == trade_date
    def shown(key):
        value = num_or_none(raw.get(key), path + "." + key)
        return value if fresh else None
    return {
        "ts_code": raw.get("ts_code") if isinstance(raw.get("ts_code"), str) else "",
        "name": need_str(raw.get("name"), path + ".name"),
        "kind": raw.get("kind") if isinstance(raw.get("kind"), str) else "",
        "role": raw.get("role") if isinstance(raw.get("role"), str) else "",
        "slot": bench_slot(raw),
        "date": date,
        "status": raw.get("status") if isinstance(raw.get("status"), str) else "",
        "return_1d_pct": shown("return_1d_pct"),
        "return_5d_pct": shown("return_5d_pct"),
        "return_20d_pct": shown("return_20d_pct"),
    }


def norm_sectors(metrics, stocks):
    raw = metrics.get("sectors")
    cards = []
    seen = set()
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise Reject("metrics.sectors 必须是数组")
    for i, sec in enumerate(raw):
        if not isinstance(sec, dict):
            raise Reject("metrics.sectors[%d] 无效" % i)
        name = need_str(sec.get("name"), "sector.name")
        pool_n = sec.get("pool_n")
        price_n = sec.get("price_n")
        if isinstance(pool_n, bool) or not isinstance(pool_n, int):
            raise Reject("sector.pool_n 无效")
        if isinstance(price_n, bool) or not isinstance(price_n, int):
            raise Reject("sector.price_n 无效")
        cards.append({
            "name": name,
            "pool_n": pool_n,
            "price_n": price_n,
            "equal_weight_return_1d_pct": num_or_none(sec.get("equal_weight_return_1d_pct"), "sector.eq"),
            "up_n": sec.get("up_n") if isinstance(sec.get("up_n"), int) and not isinstance(sec.get("up_n"), bool) else None,
            "inflow_n": sec.get("inflow_n") if isinstance(sec.get("inflow_n"), int) and not isinstance(sec.get("inflow_n"), bool) else None,
            "flow_n": sec.get("flow_n") if isinstance(sec.get("flow_n"), int) and not isinstance(sec.get("flow_n"), bool) else None,
            "partial": price_n < pool_n,
        })
        seen.add(name)
    for s in stocks:
        if s["sector"] not in seen:
            cards.append({
                "name": s["sector"], "pool_n": sum(1 for x in stocks if x["sector"] == s["sector"]),
                "price_n": None, "equal_weight_return_1d_pct": None, "up_n": None,
                "inflow_n": None, "flow_n": None, "partial": True,
            })
            seen.add(s["sector"])
    return cards


def norm_history(history, pool_version):
    if history is None:
        return {"attached": False, "version_match": True, "caveat": "", "points": []}
    if history.get("schema") != "defensive-watch-history/1.0":
        raise Reject("history.schema 必须是 defensive-watch-history/1.0")
    series = history.get("series")
    if not isinstance(series, list):
        raise Reject("history.series 必须是数组")
    points = []
    seen = set()
    for i, day in enumerate(series):
        if not isinstance(day, dict):
            raise Reject("history 日记录无效")
        date = need_date(day.get("date"), "history.date")
        if date in seen:
            raise Reject("history 同一天有多条")
        seen.add(date)
        metrics = day.get("metrics") if isinstance(day.get("metrics"), dict) else {}
        cov = day.get("coverage") if isinstance(day.get("coverage"), dict) else {}
        bench = {}
        for b in day.get("benchmarks") or []:
            if isinstance(b, dict) and isinstance(b.get("ts_code"), str):
                bench[b["ts_code"]] = {"return_1d_pct": num_or_none(b.get("return_1d_pct"), "history.bench")}
        breadth = day.get("market_breadth") if isinstance(day.get("market_breadth"), dict) else {}
        points.append({
            "date": date,
            "mode": day.get("mode") if isinstance(day.get("mode"), str) else "",
            "partial": cov.get("status") != "complete_core",
            "eq1": num_or_none(metrics.get("equal_weight_return_1d_pct"), "history.eq1"),
            "eq5": num_or_none(metrics.get("equal_weight_return_5d_pct"), "history.eq5"),
            "eq20": num_or_none(metrics.get("equal_weight_return_20d_pct"), "history.eq20"),
            "ex1": num_or_none(metrics.get("excess_1d_vs_csi300_pp"), "history.ex1"),
            "bench": bench,
            "breadth_up_ratio": num_or_none(breadth.get("up_ratio_pct"), "history.breadth"),
        })
    return {
        "attached": True,
        "version_match": history.get("pool_version") == pool_version,
        "caveat": history.get("caveat") if isinstance(history.get("caveat"), str) else "回填不能当作当时选股或样本外证据。",
        "points": points,
    }


def attach_review(stocks, review, pool_version):
    info = {"attached": False, "version_match": True, "recommendation": "", "concentration_warning": "", "financial_scope": ""}
    if review is None:
        return info
    if review.get("schema") != "defensive-watch-candidate-review/1.0":
        raise Reject("review.schema 不匹配")
    info["attached"] = True
    info["version_match"] = review.get("pool_version") == pool_version
    for key, dest in (
        ("recommendation", "recommendation"),
        ("concentration_warning", "concentration_warning"),
        ("financial_evidence_scope", "financial_scope"),
    ):
        if isinstance(review.get(key), str):
            info[dest] = review[key]
    by_code = {}
    for row in review.get("stocks") or []:
        if isinstance(row, dict) and isinstance(row.get("code"), str):
            by_code[row["code"]] = row
    for s in stocks:
        row = by_code.get(s["code"])
        if not row:
            continue
        cf = row.get("cashflow_2025") if isinstance(row.get("cashflow_2025"), dict) else None
        s["review"] = {
            "recommendation": row.get("recommendation") if isinstance(row.get("recommendation"), str) else "",
            "cashflow_2025": None if cf is None else {
                "unit": cf.get("unit") if isinstance(cf.get("unit"), str) else "",
                "operating_cashflow": num_or_none(cf.get("operating_cashflow"), "cf"),
                "purchase_construct_long_term_assets": num_or_none(cf.get("purchase_construct_long_term_assets"), "capex"),
                "operating_less_simple_capex": num_or_none(cf.get("operating_less_simple_capex"), "diff"),
                "note": cf.get("note") if isinstance(cf.get("note"), str) else "",
            },
        }
    return info


def norm_pool_file(pool, pool_version):
    if pool is None:
        return None
    if pool.get("schema") != "defensive-watch-pool/1.0":
        raise Reject("pool.schema 不匹配")
    return {
        "version_match": pool.get("version") == pool_version,
        "selection_method": pool.get("selection_method") if isinstance(pool.get("selection_method"), str) else "",
        "change_policy": pool.get("change_policy") if isinstance(pool.get("change_policy"), str) else "",
        "selection_timing": pool.get("selection_timing") if isinstance(pool.get("selection_timing"), str) else "",
        "selection_source": pool.get("selection_source") if isinstance(pool.get("selection_source"), str) else "",
        "status": pool.get("status") if isinstance(pool.get("status"), str) else "",
    }


def coverage_view(cov, n_stocks):
    if not isinstance(cov, dict):
        raise Reject("coverage 缺失")
    pool_n = cov.get("pool_n")
    if pool_n != n_stocks or pool_n != 20:
        raise Reject("coverage.pool_n 与股票数必须都是 20")
    status = cov.get("status")
    if status not in ("complete_core", "partial", "pending"):
        raise Reject("coverage.status 无效")
    items = []
    ok = status == "complete_core"
    for key, label in (
        ("prices_n", "行情"),
        ("moneyflow_n", "资金"),
        ("turnover_n", "换手"),
        ("adjusted_return_20d_n", "20日复权"),
    ):
        n = cov.get(key)
        if isinstance(n, bool) or not isinstance(n, int):
            raise Reject("coverage." + key + " 无效")
        good = n == pool_n
        ok = ok and good
        items.append({"id": key, "label": label, "n": n, "of": pool_n, "ok": good})
    if ok:
        text = "核心字段 20/20 齐全。这只说明完整度，不是研究有效，也不是市场状态。"
        display = "complete"
    else:
        text = "核心覆盖未齐全。完整度标记不能当成已经补齐。"
        display = "incomplete"
    return {"status": status, "display_status": display, "status_text": text, "items": items, "pool_n": pool_n}


def prepare(latest, history=None, pool=None, review=None, preview=False):
    for doc in (latest, history, pool, review):
        if doc is not None:
            walk_keys(doc)
    if not isinstance(latest, dict):
        raise Reject("latest 缺失")
    if latest.get("schema") != SCHEMA:
        raise Reject("schema 必须是 " + SCHEMA)
    synthetic = latest.get("fixture") == "synthetic"
    if synthetic and any(k == "fixture" for k in ()):
        pass
    if latest.get("fixture") not in (None, "synthetic"):
        raise Reject("fixture 只能是 synthetic 或省略")
    if latest.get("timezone") != "Asia/Shanghai":
        raise Reject("timezone 必须是 Asia/Shanghai")
    trade_date = need_date(latest.get("date"), "date")
    pool_version = need_str(latest.get("pool_version"), "pool_version")
    pool_status = need_str(latest.get("pool_status"), "pool_status")
    mode = need_str(latest.get("mode"), "mode")
    fetched_at = need_str(latest.get("fetched_at"), "fetched_at")
    verified_at = latest.get("last_verified_at")
    if verified_at is not None and not isinstance(verified_at, str):
        raise Reject("last_verified_at 无效")
    stocks_in = latest.get("stocks")
    if not isinstance(stocks_in, list) or len(stocks_in) != 20:
        raise Reject("stocks 必须恰好 20 只")
    stocks = [norm_stock(s, trade_date, synthetic, i) for i, s in enumerate(stocks_in)]
    codes = [s["code"] for s in stocks]
    if len(set(codes)) != 20:
        raise Reject("股票代码重复")
    benches_in = latest.get("benchmarks")
    if not isinstance(benches_in, list) or len(benches_in) != 3:
        raise Reject("benchmarks 必须恰好 3 个")
    benchmarks = [norm_benchmark(b, trade_date, i) for i, b in enumerate(benches_in)]
    if sum(1 for b in benchmarks if b["slot"] == "gold") > 1:
        raise Reject("黄金对照只能有一个")
    metrics = latest.get("metrics")
    if not isinstance(metrics, dict):
        raise Reject("metrics 缺失")
    coverage = coverage_view(latest.get("coverage"), 20)
    sectors = norm_sectors(metrics, stocks)
    hist = norm_history(history, pool_version)
    pool_file = norm_pool_file(pool, pool_version)
    review_info = attach_review(stocks, review, pool_version)
    banner = banner_for(pool_status, mode)
    if pool_file and pool_file["status"] == "confirmed" and pool_status != "confirmed":
        banner["lines"].append("池文件里的状态与当日不一致，以当日状态为准，不解除保护。")
    method = latest.get("method") if isinstance(latest.get("method"), dict) else {}
    interpretation = None
    raw_it = latest.get("interpretation")
    if raw_it is not None:
        if not isinstance(raw_it, dict):
            raise Reject("interpretation 必须是对象")
        interpretation = {k: raw_it.get(k) if isinstance(raw_it.get(k), str) else "" for k in (
            "label", "reason", "institution_identity", "gold_lead_lag", "interference_review", "snapshot_note")}
    breadth = None
    raw_br = latest.get("market_breadth")
    if raw_br is not None:
        if not isinstance(raw_br, dict):
            raise Reject("market_breadth 必须是对象")
        breadth = {
            "universe": raw_br.get("universe") if isinstance(raw_br.get("universe"), str) else "",
            "status": raw_br.get("status") if isinstance(raw_br.get("status"), str) else "",
            "up": opt_int(raw_br.get("up"), "breadth.up"),
            "down": opt_int(raw_br.get("down"), "breadth.down"),
            "flat": opt_int(raw_br.get("flat"), "breadth.flat"),
            "traded_n": opt_int(raw_br.get("traded_n"), "breadth.traded_n"),
            "up_ratio_pct": num_or_none(raw_br.get("up_ratio_pct"), "breadth.up_ratio"),
            "coverage_pct": num_or_none(raw_br.get("coverage_pct"), "breadth.coverage"),
            "date": raw_br.get("date") if isinstance(raw_br.get("date"), str) else None,
        }
        if breadth["date"] and breadth["date"] != trade_date:
            breadth["up_ratio_pct"] = None
            breadth["status"] = "date_mismatch"
    unreviewed_n = 0
    factor_changed = 0
    for s in stocks:
        inf = s["interference"]
        if any(inf[k]["state"] == "unreviewed" for k in ("repurchase", "major_announcement", "index_rebalance")):
            unreviewed_n += 1
        if inf["adjustment_factor_changed"] is True:
            factor_changed += 1
    window_complete = {k: sum(1 for s in stocks if s["windows"][k]["complete"]) for k in ("3", "5", "10")}
    sample_limited = coverage["display_status"] != "complete"
    headlines = {
        "net_flow_yuan": num_or_none(metrics.get("net_flow_yuan"), "metrics.net_flow_yuan"),
        "inflow_n": opt_int(metrics.get("inflow_n"), "metrics.inflow_n"),
        "up_n": opt_int(metrics.get("up_n"), "metrics.up_n"),
        "pool_n": 20,
        "equal_weight_return_1d_pct": num_or_none(metrics.get("equal_weight_return_1d_pct"), "metrics.eq1"),
        "equal_weight_return_5d_pct": num_or_none(metrics.get("equal_weight_return_5d_pct"), "metrics.eq5"),
        "equal_weight_return_20d_pct": num_or_none(metrics.get("equal_weight_return_20d_pct"), "metrics.eq20"),
        "excess_1d_vs_csi300_pp": num_or_none(metrics.get("excess_1d_vs_csi300_pp"), "metrics.ex1"),
        "sample_limited": sample_limited,
    }
    conflict = {
        "flow_price_conflict_n": opt_int(metrics.get("flow_price_conflict_n"), "metrics.conflict"),
        "candidate_n": opt_int(metrics.get("candidate_n"), "metrics.candidate"),
        "threshold_version": method.get("threshold_version") if isinstance(method.get("threshold_version"), str) else "",
    }
    pending = []
    if banner["tone"] != "final":
        pending.append("名单尚未定稿。")
    if unreviewed_n:
        pending.append("回购、重大公告、指数调仓仍有未核验，不能写成没有干扰。")
    if latest.get("data_finality") == "pre_official_19h_snapshot_recheck_required":
        pending.append("19点前快照，待复核。")
    if sample_limited:
        pending.append("核心覆盖未齐。")
    if not hist["attached"]:
        pending.append("历史序列未附。")
    if not review_info["attached"]:
        pending.append("逐股复核说明未附。")
    elif not review_info["version_match"]:
        pending.append("复核说明的池版本与当日不一致。")
    pv = latest.get("post_update_verification")
    verification = None
    if isinstance(pv, dict):
        missing = pv.get("missing")
        changed = pv.get("changed")
        verification = {
            "checked_at": pv.get("checked_at") if isinstance(pv.get("checked_at"), str) else "",
            "returned_rows": pv.get("returned_rows") if isinstance(pv.get("returned_rows"), int) and not isinstance(pv.get("returned_rows"), bool) else None,
            "verified_pool_n": pv.get("verified_pool_n") if isinstance(pv.get("verified_pool_n"), int) and not isinstance(pv.get("verified_pool_n"), bool) else None,
            "missing_n": len(missing) if isinstance(missing, list) else None,
            "changed_n": len(changed) if isinstance(changed, list) else None,
            "all_pool_fields_unchanged": pv.get("all_pool_fields_unchanged") if isinstance(pv.get("all_pool_fields_unchanged"), bool) else None,
        }
    shown_at = verified_at or fetched_at
    method_view = {
        "moneyflow_api": method.get("moneyflow_api") if isinstance(method.get("moneyflow_api"), str) else "",
        "primary_net_field": method.get("primary_net_field") if isinstance(method.get("primary_net_field"), str) else "net_mf_amount",
        "moneyflow_warning": method.get("moneyflow_warning") if isinstance(method.get("moneyflow_warning"), str) else "",
        "adjusted_return": method.get("adjusted_return") if isinstance(method.get("adjusted_return"), str) else "",
        "pool_return": method.get("pool_return") if isinstance(method.get("pool_return"), str) else "",
        "flow_unit_multiplier_to_yuan": method.get("flow_unit_multiplier_to_yuan"),
        "daily_amount_multiplier_to_yuan": method.get("daily_amount_multiplier_to_yuan"),
    }
    return {
        "schema": SCHEMA,
        "fixture": "synthetic" if synthetic else None,
        "preview": bool(preview),
        "auto_repair": False,
        "banner": banner,
        "clock": {
            "trade_date": trade_date,
            "timezone": "Asia/Shanghai",
            "fetched_at": fetched_at,
            "verified_at": verified_at,
            "shown_at": shown_at,
            "shown_label": "复核时间" if verified_at else "取数时间",
            "revision": latest.get("revision"),
            "pool_version": pool_version,
            "pool_status": pool_status,
            "mode": mode,
        },
        "finality": finality_view(latest),
        "coverage": coverage,
        "unreviewed": {"stocks_unreviewed": unreviewed_n, "factor_changed_n": factor_changed},
        "pending": pending,
        "headlines": headlines,
        "window_complete": window_complete,
        "conflict": conflict,
        "sectors": sectors,
        "positive_sector_n": opt_int(metrics.get("positive_sector_n"), "metrics.positive_sector_n"),
        "sector_n": opt_int(metrics.get("sector_n"), "metrics.sector_n"),
        "breadth": breadth,
        "benchmarks": benchmarks,
        "method": method_view,
        "interpretation": interpretation,
        "stocks": stocks,
        "history": hist,
        "pool_file": pool_file,
        "review": review_info,
        "verification": verification,
        "events": {"available": False, "reason": "候选基线与回填不生成 5/10/20 日样本外表现；未到期的也不显示。"},
    }


def template_text():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        text = f.read()
    if text.count("__DATA__") != 1:
        die("template placeholder broken")
    return text


def embed(obj):
    raw = json.dumps(obj, ensure_ascii=False)
    return raw.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_html(view, plain):
    wrapper = {"plain": view} if plain else {"enc": view}
    return template_text().replace("__DATA__", embed(wrapper))


def load_crypto(digest=None):
    digest = digest or os.environ.get("ASHARE_DIGEST", DIGEST_DEFAULT)
    path = os.path.join(digest, "build_page.py")
    if not os.path.isfile(path):
        return None, digest
    spec = importlib.util.spec_from_file_location("ashare_frozen_build_page", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if getattr(mod, "PBKDF2_ITERS", None) != PBKDF2_ITERS:
        die("冻结 build_page.py 的 PBKDF2_ITERS 不是 600000")
    for name in ("encrypt_bundle", "site_salt"):
        if not hasattr(mod, name):
            die("冻结模块缺少 " + name)
    return mod, digest


def encrypt_bundle(bundle, password, salt, iterations=PBKDF2_ITERS):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(password.encode("utf-8"))
    iv = os.urandom(12)
    def b(x):
        return base64.b64encode(x).decode()
    salt_b64 = b(salt)
    aad = ("2|AES-256-GCM|PBKDF2-SHA256|%d|%s" % (iterations, salt_b64)).encode("ascii")
    ct = AESGCM(key).encrypt(iv, json.dumps(bundle, ensure_ascii=False).encode("utf-8"), aad)
    return {"v": 2, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": salt_b64, "iv": b(iv), "ct": b(ct)}


def decrypt_bundle(env, password):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt = base64.b64decode(env["salt"])
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=env["iter"]).derive(password.encode("utf-8"))
    aad = ("%s|%s|%s|%s|%s" % (env["v"], env["alg"], env["kdf"], env["iter"], env["salt"])).encode("ascii")
    pt = AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), aad)
    return json.loads(pt.decode("utf-8"))


def cjk_secrets(*docs):
    found = []
    def walk(o):
        if isinstance(o, str):
            if any(ord(ch) > 127 for ch in o) and len(o) >= 2:
                found.append(o)
            elif len(o) >= 32:
                found.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for doc in docs:
        if doc is not None:
            walk(doc)
    shell = template_text()
    return [s for s in found if s not in shell]


def assert_no_cleartext(html, secrets):
    leaked = [s for s in secrets if s and s in html]
    if leaked:
        die("密文页里出现了明文观察字段（%d 处），拒绝写出" % len(leaked))


def git_root(path):
    cur = os.path.realpath(path)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def assert_output_allowed(path, synthetic, publish=False):
    real = os.path.realpath(path)
    if real.endswith(os.path.join("verify", "flow", "archive", "index.html")):
        die("拒绝改写归档密文")
    if publish:
        if not real.endswith(os.path.join("verify", "flow", "index.html")):
            die("发布只能写 verify/flow/index.html")
        return
    if real.endswith(os.path.join("verify", "flow", "index.html")):
        die("预览和 encrypt-only 不能写 verify/flow/index.html")
    root = git_root(os.path.dirname(real) if os.path.splitext(real)[1] else real)
    if root and not synthetic:
        die("真实数据的明文或非发布密文不能写进 git 仓库")


def summary(view):
    return "\n".join([
        "schema " + view["schema"],
        "pool_status " + view["clock"]["pool_status"],
        "mode " + view["clock"]["mode"],
        "banner " + view["banner"]["tone"],
        "stocks 20",
        "coverage " + view["coverage"]["display_status"],
        "fixture " + ("synthetic" if view["fixture"] == "synthetic" else "no"),
        "auto_repair 0",
        "history_points " + str(len(view["history"]["points"])),
        "finality " + str(view["finality"]["code"]),
        "unreviewed_stocks " + str(view["unreviewed"]["stocks_unreviewed"]),
    ])


def password_from_env():
    pw = os.environ.get("VERIFY_DASHBOARD_PASSWORD", "")
    if not pw:
        die("env var VERIFY_DASHBOARD_PASSWORD is not set (nothing written)", 2)
    return pw


def assert_password_absent(pw, *docs):
    if not pw:
        return
    for doc in docs:
        if doc is not None and pw in json.dumps(doc, ensure_ascii=False):
            die("输入里含有访问口令，拒绝")


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, "-c", "credential.helper=", *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and r.returncode:
        die("git %s failed: %s" % (args[0], scrub(r.stderr).strip()))
    return r


def pages_idle():
    r = subprocess.run(["pgrep", "-af", "build_page.py|build_brain.py|build_verify.py"], capture_output=True, text=True)
    lines = [ln for ln in r.stdout.splitlines() if "pgrep" not in ln and "build_defensive_pool" not in ln]
    if any(lines):
        die("发布目录忙：有其他页面构建在跑", 3)


def commit_published_page(repo, html, remote=None, push=False, lock_file=None, check_idle=True):
    dest = os.path.join(repo, "verify", "flow", "index.html")
    assert_output_allowed(dest, synthetic=False, publish=True)
    watched = ("index.html", "verify/index.html", "brain/index.html", "verify/flow/archive/index.html", "verify/flow/archive/MANIFEST.json")
    before = {}
    for rel in watched:
        p = os.path.join(repo, rel)
        if os.path.isfile(p):
            before[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    lock_path = lock_file or LOCK_DEFAULT
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            die("发布锁被占用", 3)
        if check_idle:
            pages_idle()
        if git(repo, "status", "--porcelain").stdout.strip():
            die("页面仓库工作区不干净，停止", 3)
        if push:
            if not remote:
                die("发布缺少远端")
            git(repo, "fetch", remote, "main")
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            remote_head = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
            if head != remote_head:
                if git(repo, "merge-base", "--is-ancestor", head, remote_head, check=False).returncode:
                    die("与远端 main 分叉，停止（不强推）")
                git(repo, "merge", "--ff-only", remote_head)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)
        git(repo, "add", "--", SUB_PATH)
        staged = [x for x in git(repo, "diff", "--cached", "--name-only").stdout.split() if x]
        if staged and staged != [SUB_PATH]:
            git(repo, "reset", "-q", "--", SUB_PATH)
            die("暂存区出现 verify/flow/index.html 以外的文件")
        if not staged:
            print("publish: no changes")
        else:
            today = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
            r = git(repo, "-c", "user.name=%s" % AUTHOR_NAME, "-c", "user.email=%s" % AUTHOR_EMAIL,
                    "commit", "-q", "-m", "避险观察池更新 %s · verify/flow（独立页）" % today, "--", SUB_PATH, check=False)
            if r.returncode:
                git(repo, "reset", "-q", "--", SUB_PATH, check=False)
                git(repo, "checkout", "-q", "HEAD", "--", SUB_PATH, check=False)
                die("commit refused: " + scrub(r.stderr).strip())
            if push:
                r = git(repo, "push", remote, "HEAD:main", check=False)
                if r.returncode:
                    die("push failed: " + scrub(r.stderr).strip())
                git(repo, "fetch", remote, "main")
                if git(repo, "rev-parse", "FETCH_HEAD:%s" % SUB_PATH).stdout.strip() != git(repo, "rev-parse", "HEAD:%s" % SUB_PATH).stdout.strip():
                    die("远端页面与本地提交不一致")
        for rel, digest in before.items():
            p = os.path.join(repo, rel)
            if not os.path.isfile(p) or hashlib.sha256(open(p, "rb").read()).hexdigest() != digest:
                die("发布碰到了 " + rel)
    return dest


def archive_check():
    archive = os.path.abspath(os.path.join(HERE, "..", "archive"))
    html = os.path.join(archive, "index.html")
    man_path = os.path.join(archive, "MANIFEST.json")
    if not os.path.isfile(html) or not os.path.isfile(man_path):
        die("找不到 verify/flow/archive（生成器若不在站点仓库内，这是预期的）")
    man = json.load(open(man_path, encoding="utf-8"))
    digest = hashlib.sha256(open(html, "rb").read()).hexdigest()
    if digest != man.get("sha256"):
        die("归档密文与 MANIFEST 不一致")
    if os.path.getsize(html) != man.get("bytes"):
        die("归档密文长度与 MANIFEST 不一致")
    print("archive ok " + digest)


def print_manifest():
    for name in ("build_defensive_pool.py", "template.html", "INTEGRATION.md", "schema/defensive-watch-daily-1.0.md"):
        p = os.path.join(HERE, name)
        print(hashlib.sha256(open(p, "rb").read()).hexdigest(), name)


def build_view(args, preview):
    latest = load_json(args.latest)
    history = load_json(args.history) if args.history else None
    pool = load_json(args.pool) if args.pool else None
    review = load_json(args.review) if args.review else None
    view = prepare(latest, history, pool, review, preview=preview)
    return latest, history, pool, review, view


def main(argv=None):
    ap = argparse.ArgumentParser(description="构建避险观察池加密页")
    ap.add_argument("--latest")
    ap.add_argument("--history")
    ap.add_argument("--pool")
    ap.add_argument("--review")
    ap.add_argument("--preview", metavar="DIR")
    ap.add_argument("--encrypt-only", metavar="DIR")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--archive-check", action="store_true")
    ap.add_argument("--allow-synthetic", action="store_true")
    ap.add_argument("--salt-b64")
    ap.add_argument("--pages", default=PAGES_DEFAULT)
    ap.add_argument("--iterations", type=int, default=PBKDF2_ITERS)
    args = ap.parse_args(argv)
    if args.manifest:
        print_manifest()
        return
    if args.archive_check:
        archive_check()
        return
    modes = [bool(args.preview), bool(args.encrypt_only), bool(args.publish), bool(args.check)]
    if sum(modes) != 1:
        die("请只选一种：--check / --preview / --encrypt-only / --publish")
    if not args.latest:
        die("缺少 --latest")
    if args.publish and not args.history:
        die("发布必须带 --history")
    if args.iterations < 100000 or args.iterations > 2000000:
        die("iterations 超出页面接受范围")
    if args.publish and args.iterations != PBKDF2_ITERS:
        die("发布必须使用 600000 次 PBKDF2")
    pw = ""
    if args.publish or args.encrypt_only:
        pw = password_from_env()
    latest, history, pool, review, view = build_view(args, preview=bool(args.preview))
    assert_password_absent(pw, latest, history, pool, review)
    synthetic = view["fixture"] == "synthetic"
    if args.check:
        print(summary(view))
        return
    if synthetic and not args.allow_synthetic:
        die("合成样本不能写入预览或发布；测试请加 --allow-synthetic，且不能 --publish")
    if args.publish and synthetic:
        die("拒绝发布合成样本")
    if args.preview:
        dest_dir = args.preview
        assert_output_allowed(os.path.join(dest_dir, "index.html"), synthetic)
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(view, plain=True))
        print("wrote plaintext preview (not for publish)")
        return
    secrets = cjk_secrets(latest, history, pool, review)
    if args.publish:
        mod, digest = load_crypto()
        if mod is None:
            die("发布需要只读 import 冻结的 build_page.py")
        env = mod.encrypt_bundle(view, pw, mod.site_salt(digest))
    else:
        if not args.salt_b64:
            die("encrypt-only 在没有冻结 build_page.py 时需要 --salt-b64")
        salt = base64.b64decode(args.salt_b64)
        if len(salt) != 16:
            die("salt 必须是 16 字节")
        env = encrypt_bundle(view, pw, salt, iterations=args.iterations)
    html = render_html(env, plain=False)
    assert_no_cleartext(html, secrets)
    if "已正式锁池" in html:
        die("页面模板含有禁止用语")
    if args.encrypt_only:
        dest_dir = args.encrypt_only
        assert_output_allowed(os.path.join(dest_dir, "index.html"), synthetic)
        os.makedirs(dest_dir, exist_ok=True)
        with open(os.path.join(dest_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        print("wrote encrypted html outside the site repo")
        return
    token = os.environ.get("GH_FINE_TOKEN", "")
    if not token:
        die("env var GH_FINE_TOKEN is not set")
    remote = "https://x-access-token:%s@github.com/riollejason-spec/ashare-hot.git" % token
    commit_published_page(args.pages, html, remote=remote, push=True)
    print("publish attempted only for verify/flow/index.html")


if __name__ == "__main__":
    try:
        main()
    except Reject as exc:
        die(str(exc), 2)
