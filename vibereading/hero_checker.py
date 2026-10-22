"""hero 浏览器校验池：一个常驻 node 进程（tools/hero_check.js --serve）+ 一个 Chromium + 页面池限并发。

- 每个模块独立校验、独立超时（默认 40s）：超时只判它一个，其余照常；
- node 进程卡死（某个模块开始后超过 超时+宽限 还没结果）→ 杀掉重启，未完成的重新排队（卡住的那个判超时）；
- 进程起不来 → 返回 infra 错误（调用方决定怎么兜底），不抛异常。

用法：
    with CheckerPool(concurrency=2, timeout=40) as pool:
        res = pool.check({"h1-1": doc, ...}, deadline=time.time() + 300)   # {id: {"ok", "errors", "metrics", "timeout"?, "infra"?}}
线程安全：多个线程可以同时调用 check（各自等自己的 id）。
"""
import json, os, pathlib, subprocess, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK_JS = ROOT / "tools" / "hero_check.js"
DEFAULT_TIMEOUT = float(os.environ.get("VR_HERO_CHECK_TIMEOUT", 40))
DEFAULT_CONC = int(os.environ.get("VR_HERO_CHECK_CONCURRENCY", 0)) or max(1, min(3, (os.cpu_count() or 2) - 1))
GRACE = 20.0


def available():
    return CHECK_JS.exists() and (ROOT / "tools/node_modules/puppeteer-core").exists() and \
        os.path.exists(os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"))


class CheckerPool:
    def __init__(self, concurrency=None, timeout=None, fast=True, cmd=None):
        self.conc = max(1, int(concurrency or DEFAULT_CONC))
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.fast = fast
        self.cmd = cmd            # 测试可替换成假的校验器命令
        self.proc = None
        self.lock = threading.RLock(); self.cv = threading.Condition(self.lock)
        self.results = {}         # id → result
        self.started = {}         # id → start time（node 报 start 的时刻）
        self.pending = {}         # id → payload line（已发给当前进程、还没结果）
        self.restarts = 0
        self.timeouts = 0
        self.dead = None          # 起不来时的原因

    # -------------------------------------------------------------- process
    def _spawn(self):
        cmd = self.cmd or ["node", str(CHECK_JS), "--serve", "--concurrency", str(self.conc), "--timeout", str(self.timeout)] + (["--fast"] if self.fast else [])
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except Exception as e:
            self.dead = f"{type(e).__name__}: {e}"; return None
        self.proc = p
        threading.Thread(target=self._reader, args=(p,), daemon=True, name="hero-check-reader").start()
        return p

    def _reader(self, p):
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            with self.cv:
                if p is not self.proc:
                    return
                if o.get("event") == "start":
                    self.started[o.get("id")] = time.time()
                elif o.get("event") == "fatal":
                    self.dead = o.get("error") or "fatal"
                elif "id" in o and "ok" in o:
                    if o.get("timeout"):
                        self.timeouts += 1
                    self.results[o["id"]] = o; self.pending.pop(o["id"], None); self.started.pop(o["id"], None)
                self.cv.notify_all()
        with self.cv:
            if p is self.proc:
                self.proc = None
            self.cv.notify_all()

    def _ensure(self):
        """→ (ok, spawned)。新起进程时把所有未完成的（含本次新加的）都发过去。"""
        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            if not self._spawn():
                return False, False
            for line in list(self.pending.values()):     # 进程（重）启：未完成的重新排队
                self._write(line)
            return True, True
        return True, False

    def _write(self, line):
        try:
            self.proc.stdin.write(line + "\n"); self.proc.stdin.flush(); return True
        except Exception:
            return False

    def _kill(self):
        p, self.proc = self.proc, None
        if p:
            try:
                p.kill(); p.wait(timeout=5)
            except Exception:
                pass
            self._close_pipes(p)
        self.started.clear()

    @staticmethod
    def _close_pipes(p):
        for f in (p.stdin, p.stdout):
            try:
                f and f.close()
            except Exception:
                pass

    def close(self):
        with self.cv:
            p, self.proc = self.proc, None
        if p:
            try:
                p.stdin.close(); p.wait(timeout=15)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            self._close_pipes(p)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -------------------------------------------------------------- api
    def check(self, docs, deadline=None, timeout=None):
        """docs: {id: srcdoc}。deadline：绝对时间，过了还没校验完的判超时（infra=False, timeout=True）。"""
        ids = list(docs)
        if not ids:
            return {}
        per = float(timeout or self.timeout)
        with self.cv:
            for k in ids:
                self.results.pop(k, None)
                self.pending[k] = json.dumps({"id": k, "doc": docs[k], "timeout": per, "fast": self.fast}, ensure_ascii=False)
            ok, spawned = self._ensure()
            if not ok:
                for k in ids:
                    self.pending.pop(k, None)
                return {k: {"ok": False, "infra": True, "errors": [f"校验器无法启动：{self.dead}"], "metrics": {}} for k in ids}
            if not spawned:
                for k in ids:
                    self._write(self.pending[k])
            while True:
                if all(k in self.results for k in ids):
                    break
                now = time.time()
                if deadline is not None and now >= deadline:
                    for k in ids:
                        if k not in self.results:
                            self.pending.pop(k, None)
                            self.results[k] = {"ok": False, "timeout": True, "budget": True, "errors": ["hero 阶段时间预算用完，未校验"], "metrics": {}}
                    break
                stuck = [k for k, t in self.started.items() if now - t > per + GRACE]
                if stuck or self.proc is None or self.proc.poll() is not None:
                    # 进程卡死或退出：卡住的判超时，其余重排
                    for k in stuck:
                        self.pending.pop(k, None); self.timeouts += 1
                        self.results[k] = {"ok": False, "timeout": True, "errors": [f"校验超时（单个模块 {int(per)}s，校验器无响应已重启）"], "metrics": {}}
                    self._kill(); self.restarts += 1
                    if self.restarts > 6 or not self._ensure()[0]:
                        for k in ids:
                            if k not in self.results:
                                self.pending.pop(k, None)
                                self.results[k] = {"ok": False, "infra": True, "errors": ["校验器反复崩溃"], "metrics": {}}
                        break
                    continue
                self.cv.wait(timeout=1.0)
            return {k: self.results.pop(k) for k in ids}
