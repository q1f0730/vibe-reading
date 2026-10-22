"""生成任务：线程执行，进度与日志实时写入 SQLite（重启后可查；未完成的在启动时标为失败）。"""
import json, os, re, secrets, shutil, threading, time, traceback
from . import db
from .. import planner, llm
from ..pipeline import build
from . import models as umodels

MAX_CONCURRENT = int(os.environ.get("VR_MAX_JOBS", 2))
_sem = threading.BoundedSemaphore(MAX_CONCURRENT)

# 进度页的四步：切分 → 规划 → 渲染 → 校验
STEPS = [("segment", "切分", "Segment"), ("plan", "规划", "Plan"), ("render", "渲染", "Render"), ("verify", "校验", "Verify")]
STAGE_STEP = {"segment": "segment", "engine": "plan", "outline": "plan", "detail": "plan", "detail+": "plan", "plan": "plan",
              "cast": "plan", "chapters": "plan", "chapter+": "plan",
              "render": "render", "verify": "verify", "shots": "verify", "done": "done"}
STAGE_PCT = {"segment": 6, "engine": 8, "outline": 10, "cast": 12, "chapters": 14, "detail": 30, "plan": 62, "hero": 64, "render": 80, "verify": 88, "shots": 95, "done": 100}


def book_dir(book_id):
    d = db.data_dir() / "books" / book_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def remove_book_files(book_id):
    d = db.data_dir() / "books" / book_id
    if d.is_dir() and d.parent == db.data_dir() / "books":
        shutil.rmtree(d, ignore_errors=True)


def active_job_for_user(user_id):
    return db.conn().execute("SELECT id FROM jobs WHERE user_id=? AND status IN ('queued','running')", (user_id,)).fetchone()


def start(book_id, user_id, kind="build", plan=None, model=None, verify=True, hero=None, hero_max=2):
    """kind: build（首次）/ replan（重新调用 LLM 规划）/ rerender（沿用已有分镜重新渲染）。"""
    jid = secrets.token_urlsafe(9); now = time.time(); c = db.conn()
    c.execute("INSERT INTO jobs(id, book_id, user_id, kind, status, stage, pct, log, created_at, updated_at) "
              "VALUES(?,?,?,?, 'queued','queued',1,?,?,?)", (jid, book_id, user_id, kind, json.dumps(["排队中"], ensure_ascii=False), now, now))
    c.execute("UPDATE books SET status='queued', error=NULL, updated_at=? WHERE id=?", (now, book_id))
    threading.Thread(target=_run, args=(jid, book_id, kind, plan, model, verify, hero, hero_max), daemon=True, name=f"job-{jid}").start()
    return jid


def _run(jid, book_id, kind, plan, model, verify, hero=None, hero_max=2):
    with _sem:
        c = db.conn(); t0 = time.time()
        log = []; state = {"pct": 1, "batches": 3, "stage": "segment", "last": 0}
        lock = threading.Lock()   # 整本书模式下进度回调来自多个章节线程
        secret_box = []           # 本任务解密出的 key：日志 / 报错里逐字抹掉

        def save(force=False, **extra):
            now = time.time()
            if not force and now - state["last"] < 0.4:
                return
            state["last"] = now
            sets = ", ".join(f"{k}=?" for k in extra)
            c.execute(f"UPDATE jobs SET stage=?, pct=?, log=?, updated_at=?{', ' + sets if sets else ''} WHERE id=?",
                      (state["stage"], round(state["pct"], 1), json.dumps(log[-300:], ensure_ascii=False), now, *extra.values(), jid))

        def prog(stage, msg):
          msg = llm.redact(msg, *secret_box)
          with lock:
            if stage != "done":
                log.append(f"{time.time() - t0:05.1f}|{stage}|{msg}")
            if stage == "detail":
                try: state["batches"] = int(msg.split("：")[1].split(" ")[0])
                except Exception: pass
            if stage == "detail+":
                state["pct"] = min(58, state["pct"] + 28 / max(1, state["batches"]))
            elif stage == "chapter+":   # "…（3/18）" → 14% + 已完成章节占比 × 48%
                m = re.search(r"（(\d+)/(\d+)）", msg)
                if m:
                    state["pct"] = max(state["pct"], 14 + 48 * int(m.group(1)) / max(1, int(m.group(2))))
            elif stage == "hero+":   # "…（2/5）" → 64% + 已完成占比 × 14%
                m = re.search(r"（(\d+)/(\d+)）", msg)
                if m:
                    state["pct"] = max(state["pct"], 64 + 14 * int(m.group(1)) / max(1, int(m.group(2))))
            elif stage in STAGE_PCT:
                state["pct"] = max(state["pct"], STAGE_PCT[stage])
            state["stage"] = stage
            save(force=stage in ("segment", "plan", "render", "verify", "chapter+", "cast", "hero", "hero+"))

        try:
            b = c.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            if not b:
                raise RuntimeError("书已被删除")
            c.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (time.time(), jid))
            c.execute("UPDATE books SET status='running', updated_at=? WHERE id=?", (time.time(), book_id))
            log.append(f"{0:05.1f}|start|《{b['title']}》· {b['char_count']} 字 · " +
                       {"build": "首次生成", "replan": "重新规划", "rerender": "沿用分镜重新渲染"}[kind])
            plan_in = plan
            if kind == "rerender":
                if not b["plan_json"]:
                    raise RuntimeError("这本书还没有分镜，无法只重新渲染")
                plan_in = json.loads(b["plan_json"])
            out = book_dir(book_id) / "book.html"
            engine = None
            if not plan_in and model == "user":
                engine = umodels.engine_for_user(b["user_id"])     # key 只在这个线程里解密
                secret_box.append(engine.client.cfg.api_key)
                prog("engine", f"使用你的模型：{engine.label} · {engine.tier} 档（批次 ≤{engine.strategy.batch_paras} 段 · 修复 {engine.strategy.repair_rounds} 轮）")
            rep = build(b["text"], out, title=b["title"], author=b["author"] or None, theme=b["theme"],
                        verify=verify, plan_in=plan_in, model=None if (plan_in or model == "user") else model, progress=prog,
                        hero=hero, hero_max=hero_max, engine=engine)
            plan_json = (out.parent / "book.plan.json").read_text(encoding="utf-8")
            v = rep.get("verify")
            llm_d = rep.get("llm", {})
            q = rep.get("quality") or {}
            used_model = "plan.json" if llm_d.get("provider") == "external" and kind != "rerender" else (
                b["model"] if kind == "rerender" else f"{llm_d.get('provider')}:{llm_d.get('model')}")
            if v is not None:
                log.append(f"{time.time() - t0:05.1f}|verify|" + (f"原文 {v.get('matched')}/{v.get('segments')} 段逐字一致" if v.get("text_identical")
                                                                  else "校验未通过：" + json.dumps(v, ensure_ascii=False)[:300]))
            now = time.time()
            if kind == "rerender" and b["quality_json"]:
                old = json.loads(b["quality_json"])     # 只重新渲染：模型相关的分数沿用上次规划的
                q = dict(q, model=old.get("model"), model_label=old.get("model_label"), tier=old.get("tier"), source=old.get("source"),
                         cost=old.get("cost"), scores=dict(q.get("scores") or {}, schema=(old.get("scores") or {}).get("schema"),
                                                          schema_first_pass=(old.get("scores") or {}).get("schema_first_pass"),
                                                          coverage=(old.get("scores") or {}).get("coverage")))
            if q:
                log.append(f"{time.time() - t0:05.1f}|quality|质量 {q.get('overall')} · {q.get('tier') or '—'} 档 · 降级段 {round(100 * (q.get('degraded_paragraph_ratio') or 0), 1)}% · 下限{'守住' if q.get('floor_ok') else '未守住'}")
            c.execute("UPDATE books SET status='done', plan_json=?, html_path=?, model=?, segments=?, verify_json=?, seconds=?, "
                      "quality_json=?, tier=?, error=NULL, updated_at=? WHERE id=?",
                      (plan_json, f"books/{book_id}/book.html", used_model, rep.get("segments"),
                       json.dumps(v, ensure_ascii=False) if v is not None else None, rep.get("total_seconds"),
                       json.dumps(q, ensure_ascii=False) if q else None, q.get("tier") if q else None, now, book_id))
            state.update(stage="done", pct=100)
            log.append(f"{now - t0:05.1f}|done|完成 · {rep.get('total_seconds')}s")
            save(force=True, status="done", finished_at=now)
        except Exception as e:
            err = llm.redact(f"{type(e).__name__}: {e}", *secret_box)
            log.append(f"{time.time() - t0:05.1f}|error|{err}")
            tb = llm.redact(traceback.format_exc(), *secret_box)
            print(f"[job {jid}] {tb}", flush=True)
            now = time.time()
            c.execute("UPDATE books SET status='failed', error=?, updated_at=? WHERE id=?", (err[:500], now, book_id))
            save(force=True, status="failed", error=err[:500], finished_at=now)


def llm_status():
    return {k: planner.llm_available(k) for k in planner.PROVIDERS}


def user_llm_status(user_id):
    """平台模型 + 用户自己的模型（探针通过才算可用）。"""
    d = llm_status()
    d["user"] = bool(user_id) and umodels.ready(user_id)
    return d
