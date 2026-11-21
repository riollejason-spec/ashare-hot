#!/usr/bin/env python3
"""避险观察池生成器测试。夹具都是合成数据。真实研究包只在设置了环境变量时另跑，且不写入仓库。"""
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.dirname(HERE)
BUILDER = os.path.join(GEN, "build_defensive_pool.py")

spec = importlib.util.spec_from_file_location("defensive_pool", BUILDER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SECTORS = ["水电核电", "水务燃气", "收费公路", "基础食品", "常用药医药流通"]
PW = "test-password-not-production"


def window(complete=True, inflow=1, cumulative=10, ratio=1.0, expected=None, flow=None):
    expected = 5 if expected is None else expected
    flow = expected if flow is None else flow
    if not complete:
        cumulative = None
        ratio = None
        inflow = None
    return {
        "expected_days": expected, "price_days": flow, "flow_days": flow, "inflow_days": inflow,
        "cumulative_net_yuan": cumulative, "amount_yuan": 20 if complete else None,
        "net_to_amount_pct": ratio, "complete": complete,
    }


def stock(i, **over):
    code = "SYN%03d" % i
    base = {
        "code": code, "ts_code": code + ".SY", "name": "合成样本%02d" % i, "sector": SECTORS[i // 4],
        "reason": "合成理由", "risk": "合成风险", "weight": 0.05, "date": "2026-11-20", "status": "traded",
        "source": "synthetic", "units": {}, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "pre_close": 1,
        "return_1d_pct": 0.1, "amount_yuan": 100, "volume_lots": 1, "turnover_pct": 0.2,
        "adj_factor": 1, "return_5d_pct": 0.2, "return_20d_pct": None,
        "excess_1d_vs_csi300_pp": 0.3, "excess_5d_vs_csi300_pp": None, "excess_20d_vs_csi300_pp": None,
        "net_flow_yuan": 7, "net_flow_to_amount_pct": 0.4,
        "moneyflow": {"net_mf_amount": 0.0007, "class_net_yuan": {"sm": 1, "md": 1, "lg": 1, "elg": 1}, "large_pair_net_yuan": 2},
        "windows": {"3": window(expected=3), "5": window(expected=5), "10": window(expected=10)},
        "price_evidence": {
            "low_5d_vs_previous5_pct": None, "return_5d_vs_csi300_pp": None, "max_drawdown_10d_pct": None,
            "volume_spike_1d": False, "flow_price_conflict_5d": False, "provisional_accumulation_candidate": False,
        },
        "interference": {
            "adjustment_factor_changed": False, "repurchase": "unreviewed",
            "major_announcement": "unreviewed", "index_rebalance": "unreviewed",
            "review_status": "partial_adjustment_only",
            "note": "未核验不等于无干扰",
        },
    }
    base.update(over)
    return base


def latest_doc():
    stocks = [stock(i) for i in range(20)]
    stocks[1]["name"] = "合成</script><img src=x onerror=alert(1)>"
    stocks[2]["date"] = "2026-11-19"
    stocks[2]["return_1d_pct"] = 9.8765
    stocks[3]["windows"]["5"] = window(complete=False, expected=5, flow=4)
    stocks[4]["amount_yuan"] = None
    stocks[4]["turnover_pct"] = 0
    stocks[4]["net_flow_yuan"] = None
    return {
        "schema": "defensive-watch-daily/1.0",
        "fixture": "synthetic",
        "pool_version": "SYNTH-proposal",
        "pool_status": "proposed_for_confirmation",
        "date": "2026-11-20",
        "timezone": "Asia/Shanghai",
        "fetched_at": "2026-11-20T18:00:00+08:00",
        "last_verified_at": "2026-11-20T19:01:31+08:00",
        "revision": 1,
        "run_id": "synthetic-run",
        "mode": "candidate_baseline",
        "status": "complete_core",
        "data_finality": "post_schedule_snapshot_verified_20of20",
        "provider_finality_flag_available": False,
        "coverage": {"prices_n": 20, "moneyflow_n": 20, "turnover_n": 20, "adjusted_return_20d_n": 20, "pool_n": 20, "status": "complete_core"},
        "metrics": {
            "equal_weight_return_1d_pct": 1.25, "equal_weight_return_5d_pct": None, "equal_weight_return_20d_pct": None,
            "excess_1d_vs_csi300_pp": 0.5, "up_n": 3, "inflow_n": 4, "net_flow_yuan": 100,
            "positive_sector_n": 1, "sector_n": 5, "candidate_n": 0, "flow_price_conflict_n": 0,
            "sectors": [
                {"name": name, "pool_n": 4, "price_n": 4, "equal_weight_return_1d_pct": (0.2 if i == 0 else -0.2),
                 "up_n": 4 if i == 0 else 0, "inflow_n": 1, "flow_n": 4}
                for i, name in enumerate(SECTORS)
            ],
        },
        "stocks": stocks,
        "benchmarks": [
            {"ts_code": "SYN-MKT", "name": "合成大盘", "kind": "index", "role": "大盘基准", "date": "2026-11-20", "status": "ok",
             "close": 1, "return_1d_pct": -0.5, "return_5d_pct": None, "return_20d_pct": None, "source": "synthetic", "units": {}},
            {"ts_code": "SYN-GRO", "name": "合成成长", "kind": "index", "role": "成长风格对照", "date": "2026-11-20", "status": "ok",
             "close": 1, "return_1d_pct": -1, "return_5d_pct": None, "return_20d_pct": None, "source": "synthetic", "units": {}},
            {"ts_code": "SYN-GLD", "name": "合成黄金", "kind": "fund", "role": "黄金对照，不占20只", "date": "2026-11-20", "status": "ok",
             "close": 1, "return_1d_pct": -0.2, "return_5d_pct": None, "return_20d_pct": None, "source": "synthetic", "units": {}},
        ],
        "market_breadth": {"date": "2026-11-20", "source": "synthetic", "universe": "合成全A", "status": "ok",
                           "up": 3, "down": 4, "flat": 0, "traded_n": 7, "listed_n": 8, "coverage_pct": 80, "up_ratio_pct": 5.5, "amount_yuan": 10},
        "interpretation": {
            "label": "合成描述：局部承接", "reason": "合成原因，池收益为正也不当作广泛回暖",
            "institution_identity": "不可由大小单推断身份比例", "gold_lead_lag": "不预设黄金随后",
            "interference_review": "公告未核验", "snapshot_note": "不是供应商终值",
        },
        "method": {
            "moneyflow_api": "moneyflow", "primary_net_field": "net_mf_amount",
            "flow_unit_multiplier_to_yuan": 10000, "daily_amount_multiplier_to_yuan": 1000,
            "adjusted_return": "端点复权，不填因子", "pool_return": "输入给定等权",
            "moneyflow_warning": "不能用四档差额替代主净额", "threshold_version": "provisional-v1-unvalidated",
        },
    }


def history_doc():
    def day(date, mode, eq1, eq20):
        return {
            "date": date, "mode": mode, "pool_version": "SYNTH-proposal",
            "coverage": {"prices_n": 20, "moneyflow_n": 20, "turnover_n": 20, "adjusted_return_20d_n": 0 if eq20 is None else 20, "pool_n": 20,
                         "status": "partial" if eq20 is None else "complete_core"},
            "metrics": {"equal_weight_return_1d_pct": eq1, "equal_weight_return_5d_pct": None, "equal_weight_return_20d_pct": eq20,
                        "excess_1d_vs_csi300_pp": None, "up_n": 1, "inflow_n": 1, "net_flow_yuan": None},
            "benchmarks": [
                {"ts_code": "SYN-MKT", "return_1d_pct": -0.1},
                {"ts_code": "SYN-GRO", "return_1d_pct": None},
                {"ts_code": "SYN-GLD", "return_1d_pct": 0.1},
            ],
            "market_breadth": {"date": date, "up_ratio_pct": None if eq1 is None else 1, "up": 1, "down": 1, "flat": 0, "traded_n": 2,
                               "status": "ok", "universe": "合成全A", "source": "synthetic", "listed_n": 2, "coverage_pct": 1, "amount_yuan": None},
        }
    return {
        "schema": "defensive-watch-history/1.0", "pool_version": "SYNTH-proposal", "fetched_at": "2026-11-20T18:00:00+08:00",
        "caveat": "入池前数据全部为回填；不证明当时选股或样本外有效性",
        "series": [day("2026-11-18", "backfill", 10, None), day("2026-11-19", "backfill", None, None), day("2026-11-20", "candidate_baseline", 30, 1)],
    }


def write_inputs(dirpath):
    os.makedirs(dirpath, exist_ok=True)
    paths = {}
    for name, doc in (("latest.json", latest_doc()), ("history.json", history_doc())):
        paths[name] = os.path.join(dirpath, name)
        with open(paths[name], "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    return paths


class PrepareTests(unittest.TestCase):
    def test_candidate_banner_and_no_autolabel(self):
        view = mod.prepare(latest_doc(), history_doc())
        self.assertEqual(view["banner"]["tone"], "candidate")
        self.assertIn("待定稿", "".join(view["banner"]["lines"]))
        self.assertFalse(view["auto_repair"])
        self.assertEqual(view["headlines"]["net_flow_yuan"], 100)
        self.assertEqual(view["headlines"]["equal_weight_return_1d_pct"], 1.25)
        self.assertEqual(view["positive_sector_n"], 1)
        self.assertIsNone(view["stocks"][3]["windows"]["5"]["cumulative_net_yuan"])
        self.assertIsNone(view["stocks"][3]["windows"]["5"]["inflow_days"])
        self.assertFalse(view["stocks"][2]["date_match"])
        self.assertEqual(view["stocks"][1]["moneyflow"]["class_net_yuan"]["sm"], 1)
        self.assertNotEqual(view["headlines"]["net_flow_yuan"], 4)
        self.assertIn("19点后复核", view["finality"]["text"])
        self.assertFalse(view["finality"]["vendor_final"])
        self.assertEqual(view["history"]["points"][1]["eq1"], None)
        self.assertEqual(view["history"]["points"][0]["mode"], "backfill")

    def test_missing_interpretation_not_invented(self):
        doc = latest_doc()
        doc["interpretation"] = None
        doc["metrics"]["equal_weight_return_1d_pct"] = 3
        view = mod.prepare(doc, history_doc())
        self.assertIsNone(view["interpretation"])
        self.assertFalse(view["auto_repair"])

    def test_confirmed_only_when_explicit(self):
        doc = latest_doc()
        doc["fixture"] = None
        for s in doc["stocks"]:
            s["code"] = "%06d" % (910000 + int(s["code"][-3:]))
            s["ts_code"] = s["code"] + ".SH"
        doc["pool_status"] = "confirmed"
        doc["mode"] = "candidate_baseline"
        self.assertEqual(mod.prepare(doc)["banner"]["tone"], "candidate")
        doc["mode"] = "confirmed"
        self.assertEqual(mod.prepare(doc)["banner"]["tone"], "final")

    def test_rejects(self):
        doc = latest_doc()
        doc["stocks"][0]["windows"]["5"]["complete"] = False
        doc["stocks"][0]["windows"]["5"]["cumulative_net_yuan"] = 5
        with self.assertRaises(mod.Reject):
            mod.prepare(doc)
        doc = latest_doc()
        doc["coverage"]["prices_n"] = 19
        doc["coverage"]["status"] = "complete_core"
        view = mod.prepare(doc)
        self.assertEqual(view["coverage"]["display_status"], "incomplete")
        self.assertTrue(view["headlines"]["sample_limited"])
        doc = latest_doc()
        doc["stocks"][0]["total_score"] = 99
        with self.assertRaises(mod.Reject):
            mod.prepare(doc)
        doc = latest_doc()
        doc["pool_status"] = "proposed_for_confirmation"
        doc["stocks"] = doc["stocks"][:19]
        with self.assertRaises(mod.Reject):
            mod.prepare(doc)

    def test_archive_manifest(self):
        mod.archive_check()
        archive = os.path.join(ROOT, "verify", "flow", "archive", "index.html")
        live = os.path.join(ROOT, "verify", "flow", "index.html")
        self.assertEqual(hashlib.sha256(open(archive, "rb").read()).hexdigest(),
                         hashlib.sha256(open(live, "rb").read()).hexdigest())
        self.assertIn("资金流看板", open(archive, encoding="utf-8").read()[:400])

    def test_sources_have_no_real_codes(self):
        banned = __import__("re").compile(r"(?<!\d)[036]\d{5}(?!\d)")
        allow = {"600000"}
        for dirpath, _, files in os.walk(GEN):
            for name in files:
                if name.endswith((".py", ".html", ".md", ".json")):
                    text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                    hits = [m.group() for m in banned.finditer(text) if m.group() not in allow]
                    self.assertEqual(hits, [], name)
        template = open(os.path.join(GEN, "template.html"), encoding="utf-8").read()
        self.assertNotIn("已正式锁池", template)
        py = open(BUILDER, encoding="utf-8").read()
        self.assertEqual(py.lower().count("ops-hub"), 1)


class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_wrong_password(self):
        view = mod.prepare(latest_doc(), history_doc())
        salt = b"0123456789abcdef"
        env = mod.encrypt_bundle(view, PW, salt, iterations=120000)
        self.assertEqual(mod.decrypt_bundle(env, PW)["headlines"]["net_flow_yuan"], 100)
        with self.assertRaises(Exception):
            mod.decrypt_bundle(env, "wrong-password")

    def test_node_webcrypto_600000(self):
        view = {"schema": mod.SCHEMA, "marker": "合成节点核对"}
        env = mod.encrypt_bundle(view, PW, b"abcdef0123456789", iterations=600000)
        script = r"""
const crypto = globalThis.crypto;
const env = JSON.parse(process.env.ENV_JSON);
const pw = process.env.TEST_PW;
const b64d = s => Uint8Array.from(Buffer.from(s, 'base64'));
(async () => {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({name:'PBKDF2', salt:b64d(env.salt), iterations:env.iter, hash:'SHA-256'}, base, {name:'AES-GCM', length:256}, false, ['decrypt']);
  const aad = new TextEncoder().encode(env.v+'|'+env.alg+'|'+env.kdf+'|'+env.iter+'|'+env.salt);
  const pt = await crypto.subtle.decrypt({name:'AES-GCM', iv:b64d(env.iv), additionalData:aad}, key, b64d(env.ct));
  process.stdout.write(Buffer.from(pt).toString('utf8'));
})().catch(e => { console.error(e); process.exit(1); });
"""
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                           env={**os.environ, "ENV_JSON": json.dumps(env), "TEST_PW": PW})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["marker"], "合成节点核对")

    def test_encrypted_html_hides_sentinel(self):
        tmp = os.path.join("/tmp", "dw-enc-test")
        shutil.rmtree(tmp, ignore_errors=True)
        paths = write_inputs(tmp)
        salt = base64.b64encode(b"0123456789abcdef").decode()
        out = os.path.join(tmp, "out")
        r = subprocess.run(["python3", BUILDER, "--encrypt-only", out, "--allow-synthetic", "--salt-b64", salt,
                            "--iterations", "120000", "--latest", paths["latest.json"], "--history", paths["history.json"]],
                           capture_output=True, text=True, env={**os.environ, "VERIFY_DASHBOARD_PASSWORD": PW})
        self.assertEqual(r.returncode, 0, r.stderr)
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertNotIn("合成样本", html)
        self.assertNotIn("水电核电", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn('"enc"', html)

    def test_missing_password_writes_nothing(self):
        tmp = os.path.join("/tmp", "dw-nopw")
        shutil.rmtree(tmp, ignore_errors=True)
        paths = write_inputs(tmp)
        out = os.path.join(tmp, "out")
        env = {k: v for k, v in os.environ.items() if k != "VERIFY_DASHBOARD_PASSWORD"}
        r = subprocess.run(["python3", BUILDER, "--encrypt-only", out, "--allow-synthetic", "--salt-b64", "MDEyMzQ1Njc4OWFiY2RlZg==",
                            "--latest", paths["latest.json"]], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(out, "index.html")))
        self.assertIn("VERIFY_DASHBOARD_PASSWORD", r.stderr)

    def test_check_hides_names(self):
        tmp = os.path.join("/tmp", "dw-check")
        shutil.rmtree(tmp, ignore_errors=True)
        paths = write_inputs(tmp)
        r = subprocess.run(["python3", BUILDER, "--check", "--latest", paths["latest.json"], "--history", paths["history.json"]],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("banner candidate", r.stdout)
        self.assertNotIn("合成样本", r.stdout)
        self.assertNotIn("onerror", r.stdout)


class PublishGuardTests(unittest.TestCase):
    def make_repo(self):
        tmp = os.path.join("/tmp", "dw-pages-%s" % os.getpid())
        repo = os.path.join(tmp, "pages")
        bare = os.path.join(tmp, "bare.git")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(repo)
        subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
        open(os.path.join(repo, "index.html"), "w").write("root")
        os.makedirs(os.path.join(repo, "verify", "flow", "archive"))
        open(os.path.join(repo, "verify", "flow", "index.html"), "w").write("old")
        open(os.path.join(repo, "verify", "flow", "archive", "index.html"), "w").write("archive")
        subprocess.check_call(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"])
        subprocess.check_call(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init"])
        subprocess.check_call(["git", "clone", "-q", "--bare", repo, bare])
        subprocess.check_call(["git", "-C", repo, "remote", "add", "origin", bare])
        return repo, bare

    def test_commit_only_flow_page(self):
        repo, bare = self.make_repo()
        mod.commit_published_page(repo, "<html>enc</html>", remote=bare, push=True, lock_file="/tmp/dw-test.lock", check_idle=False)
        shown = subprocess.check_output(["git", "-C", bare, "show", "HEAD:verify/flow/index.html"], text=True)
        self.assertEqual(shown, "<html>enc</html>")
        self.assertEqual(subprocess.check_output(["git", "-C", bare, "show", "HEAD:index.html"], text=True), "root")
        self.assertEqual(subprocess.check_output(["git", "-C", bare, "show", "HEAD:verify/flow/archive/index.html"], text=True), "archive")
        names = subprocess.check_output(["git", "-C", repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], text=True).split()
        self.assertEqual(names, ["verify/flow/index.html"])

    def test_dirty_tree_refuses(self):
        repo, bare = self.make_repo()
        open(os.path.join(repo, "index.html"), "a").write("x")
        with self.assertRaises(SystemExit):
            mod.commit_published_page(repo, "new", remote=bare, push=False, lock_file="/tmp/dw-test.lock", check_idle=False)
        self.assertEqual(open(os.path.join(repo, "verify", "flow", "index.html")).read(), "old")

    def test_diverged_does_not_force(self):
        repo, bare = self.make_repo()
        other = repo + "-other"
        subprocess.check_call(["git", "clone", "-q", bare, other])
        open(os.path.join(other, "index.html"), "w").write("other")
        subprocess.check_call(["git", "-C", other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-aq", "-m", "other"])
        subprocess.check_call(["git", "-C", other, "push", "-q", "origin", "HEAD:main"])
        open(os.path.join(repo, "README"), "w").write("local")
        subprocess.check_call(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "README"])
        subprocess.check_call(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "local"])
        with self.assertRaises(SystemExit):
            mod.commit_published_page(repo, "nope", remote=bare, push=True, lock_file="/tmp/dw-test.lock", check_idle=False)
        head = subprocess.check_output(["git", "-C", bare, "rev-parse", "HEAD"], text=True).strip()
        other_head = subprocess.check_output(["git", "-C", other, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(head, other_head)

    def test_publish_cli_refuses_without_frozen_module(self):
        tmp = os.path.join("/tmp", "dw-pub-cli")
        shutil.rmtree(tmp, ignore_errors=True)
        paths = write_inputs(tmp)
        doc = json.load(open(paths["latest.json"], encoding="utf-8"))
        doc.pop("fixture", None)
        for i, s in enumerate(doc["stocks"]):
            s["code"] = "%06d" % (910000 + i)
            s["ts_code"] = s["code"] + ".SH"
            s["name"] = "格式样本%02d" % i
        with open(paths["latest.json"], "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        env = {**os.environ, "VERIFY_DASHBOARD_PASSWORD": PW, "ASHARE_DIGEST": tmp, "GH_FINE_TOKEN": "dummy"}
        r = subprocess.run(["python3", BUILDER, "--publish", "--latest", paths["latest.json"], "--history", paths["history.json"], "--pages", tmp],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("build_page.py", r.stderr)


class BrowserTests(unittest.TestCase):
    def render_preview(self):
        tmp = os.path.join("/tmp", "dw-browser")
        shutil.rmtree(tmp, ignore_errors=True)
        paths = write_inputs(tmp)
        out = os.path.join(tmp, "preview")
        r = subprocess.run(["python3", BUILDER, "--preview", out, "--allow-synthetic", "--latest", paths["latest.json"], "--history", paths["history.json"]],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def dump(self, directory, width=360):
        return self.dump_existing(self.render_preview(), width)

    def dump_existing(self, directory, width=360):
        html = os.path.join(directory, "index.html")
        probe = """
<script>
addEventListener('load', () => {
  const el = document.documentElement;
  document.querySelector('[data-act=view][data-v=price]')?.click();
  const naturalOverflow = el.scrollWidth - el.clientWidth;
  const naturalCw = el.clientWidth;
  document.documentElement.style.width = '320px';
  document.documentElement.style.maxWidth = '320px';
  document.body.style.width = '320px';
  document.body.style.maxWidth = '320px';
  const order = [...document.querySelectorAll('[data-sec]')].map(n => n.getAttribute('data-sec'));
  const out = {
    overflow: naturalOverflow,
    forced320: document.documentElement.scrollWidth - 320,
    order: order,
    tone: document.querySelector('[data-pool-tone]') && document.querySelector('[data-pool-tone]').getAttribute('data-pool-tone'),
    auto: document.querySelector('[data-auto-repair]') && document.querySelector('[data-auto-repair]').getAttribute('data-auto-repair'),
    reading: document.querySelector('[data-reading]') && document.querySelector('[data-reading]').getAttribute('data-reading'),
    net: (document.querySelector('[data-field=net] [data-raw]') || {}).getAttribute && document.querySelector('[data-field=net] [data-raw]').getAttribute('data-raw'),
    scripts: document.scripts.length,
    imgs: document.images.length,
    stale: !!document.querySelector('[data-stale]'),
    sentinel: document.body.innerText.indexOf('9.8765') >= 0,
    gaps: (document.querySelector('svg') || {}).getAttribute && document.querySelector('svg').getAttribute('data-gap-segments'),
    backfill: document.querySelectorAll('[data-mode=backfill]').length,
    unreviewed: document.querySelectorAll('[data-interference=unreviewed]').length,
    lockPhrase: document.body.innerText.indexOf('已正式锁池') >= 0,
    pending: document.body.innerText.indexOf('待定稿') >= 0,
    zeroTurnover: !!document.querySelector('[data-code=SYN004] [data-zero="1"]'),
    missingAmount: !!document.querySelector('[data-code=SYN004] [data-missing]'),
    cw: naturalCw
  };
  const pre = document.createElement('pre');
  pre.id = 'probe';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
});
</script>
"""
        text = open(html, encoding="utf-8").read().replace("</body>", probe + "</body>")
        open(html, "w", encoding="utf-8").write(text)
        prev = os.getcwd()
        os.chdir(directory)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        profile = os.path.join("/tmp", "chrome-%d-%d" % (os.getpid(), width))
        shutil.rmtree(profile, ignore_errors=True)
        cmd = [
            "google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--no-first-run", "--user-data-dir=" + profile, "--window-size=%d,900" % width, "--dump-dom",
            "http://127.0.0.1:%d/index.html" % port,
        ]
        try:
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=12)
                stdout = r.stdout
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or b""
        finally:
            httpd.shutdown()
            os.chdir(prev)
        text = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
        start = text.find('<pre id="probe">')
        self.assertGreater(start, 0, "probe missing")
        blob = text[start + len('<pre id="probe">'):].split("</pre>", 1)[0]
        return json.loads(blob)

    def test_narrow_layout_and_rules(self):
        out = self.render_preview()
        got = self.dump(out, 360)
        self.assertLessEqual(got["overflow"], 1)
        self.assertLess(got["cw"], 560)
        self.assertIn("@media(max-width:560px)", open(os.path.join(GEN, "template.html"), encoding="utf-8").read())
        self.assertEqual(got["order"][:8], ["banner", "coverage", "flow", "windows", "conflict", "sectors", "breadth", "benchmarks"])
        self.assertEqual(got["tone"], "candidate")
        self.assertEqual(got["auto"], "0")
        self.assertEqual(got["reading"], "given")
        self.assertEqual(got["net"], "100")
        self.assertEqual(got["scripts"], 3)
        self.assertEqual(got["imgs"], 0)
        self.assertTrue(got["stale"])
        self.assertFalse(got["sentinel"])
        self.assertGreaterEqual(int(got["gaps"]), 2)
        self.assertGreaterEqual(got["backfill"], 1)
        self.assertGreater(got["unreviewed"], 0)
        self.assertFalse(got["lockPhrase"])
        self.assertTrue(got["pending"])
        self.assertTrue(got["zeroTurnover"])
        self.assertTrue(got["missingAmount"])
        wide = self.dump(out, 1280)
        self.assertLessEqual(wide["overflow"], 1)


class PrivateBundleTests(unittest.TestCase):
    def test_private_bundle_if_present(self):
        base = os.environ.get("DEFENSIVE_WATCH_PRIVATE")
        if not base:
            self.skipTest("no private bundle")
        latest = json.load(open(os.path.join(base, "web", "latest.json"), encoding="utf-8"))
        history = json.load(open(os.path.join(base, "web", "history.json"), encoding="utf-8"))
        pool = json.load(open(os.path.join(base, "web", "pool.json"), encoding="utf-8"))
        review = json.load(open(os.path.join(base, "web", "review.json"), encoding="utf-8"))
        view = mod.prepare(latest, history, pool, review)
        self.assertEqual(view["banner"]["tone"], "candidate")
        self.assertIn("待定稿", "".join(view["banner"]["lines"]))
        self.assertFalse(view["auto_repair"])
        self.assertEqual(view["coverage"]["display_status"], "complete")
        self.assertEqual(view["headlines"]["net_flow_yuan"], latest["metrics"]["net_flow_yuan"])
        self.assertEqual(view["headlines"]["inflow_n"], latest["metrics"]["inflow_n"])
        self.assertEqual(view["headlines"]["up_n"], latest["metrics"]["up_n"])
        self.assertEqual(view["headlines"]["equal_weight_return_1d_pct"], latest["metrics"]["equal_weight_return_1d_pct"])
        self.assertEqual(view["positive_sector_n"], latest["metrics"]["positive_sector_n"])
        self.assertEqual(view["interpretation"]["label"], latest["interpretation"]["label"])
        self.assertEqual(sum(1 for p in view["history"]["points"] if p["mode"] == "backfill"), 26)
        self.assertTrue(any(s["interference"]["repurchase"]["state"] == "unreviewed" for s in view["stocks"]))
        self.assertFalse(view["finality"]["vendor_final"])
        out = "/tmp/dw-private-preview"
        if os.path.realpath(out).startswith(os.path.realpath(ROOT)):
            self.fail("preview path inside repo")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        html = mod.render_html(view, plain=True)
        # 真实明文只留在 /tmp
        open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
        salt = b"0123456789abcdef"
        env = mod.encrypt_bundle(view, PW, salt, iterations=120000)
        enc = mod.render_html(env, plain=False)
        for s in latest["stocks"]:
            self.assertNotIn(s["name"], enc)
            self.assertNotIn(s["ts_code"], enc)
        self.assertNotIn(latest["run_id"], enc)
        got = BrowserTests().dump_existing(out, 360)
        self.assertEqual(got["tone"], "candidate")
        self.assertEqual(got["auto"], "0")
        self.assertEqual(got["reading"], "given")
        same_net = abs(float(got["net"]) - float(latest["metrics"]["net_flow_yuan"])) < 1
        self.assertTrue(same_net)
        self.assertFalse(got["lockPhrase"])
        self.assertTrue(got["pending"])
        self.assertGreater(got["unreviewed"], 0)
        self.assertGreaterEqual(got["backfill"], 1)
        self.assertLessEqual(got["overflow"], 1)
        self.assertLess(got["cw"], 560)


if __name__ == "__main__":
    unittest.main()
