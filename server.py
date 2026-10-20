#!/usr/bin/env python3
"""VibeReading 极简本地上传服务：上传 txt → 进度 → 打开生成的阅读页。
用法：python3 server.py [--port 8765]   然后浏览器打开 http://127.0.0.1:8765
仅依赖标准库 + vibereading 包（jsonschema）。生成结果在 ./server_out/。"""
import argparse, json, pathlib, sys, threading, time, uuid, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from vibereading.pipeline import build  # noqa: E402
from vibereading import planner  # noqa: E402

OUT = ROOT / "server_out"; OUT.mkdir(exist_ok=True)
JOBS = {}
STAGE_PCT = {"segment": 5, "outline": 10, "detail": 30, "detail+": None, "plan": 60, "render": 88, "verify": 92, "shots": 95, "done": 100}

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VibeReading · 上传</title><style>
:root{--bg:#faf9f7;--t:#1a1a1a;--t2:#6a6a6a;--r:#e5e2db;--a:#c41e3a}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Noto Serif CJK SC','Songti SC',serif;background:var(--bg);color:var(--t);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
main{width:min(560px,100%)}h1{font-size:2.4rem;letter-spacing:.12em;margin-bottom:.3rem}p.s{color:var(--t2);margin-bottom:2rem;line-height:1.8}
label.drop{display:block;border:1.5px dashed var(--t2);padding:2.2rem 1rem;text-align:center;cursor:pointer;color:var(--t2);transition:.2s}
label.drop:hover,label.drop.over{border-color:var(--a);color:var(--t)}label.drop.small{margin-top:12px;padding:1rem;font-size:.9rem}
#llm{font-size:12.5px;color:var(--t2);margin-top:10px;line-height:1.7}#llm b{color:var(--a);font-weight:400}input[type=file]{display:none}
.row{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}.row input,.row select{flex:1;min-width:120px;padding:10px 12px;border:1px solid var(--r);background:#fff;font:inherit}
button{margin-top:18px;width:100%;padding:13px;background:var(--t);color:var(--bg);border:0;font:inherit;letter-spacing:.3em;cursor:pointer}button:disabled{opacity:.4;cursor:default}
.bar{height:3px;background:var(--r);margin-top:26px;overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--a);transition:width .6s}
#log{margin-top:12px;font-size:13px;color:var(--t2);line-height:1.9;min-height:3em;white-space:pre-wrap}#done a{color:var(--a)}
small.n{display:block;margin-top:14px;color:var(--t2);font-size:12px;line-height:1.7}
</style></head><body><main>
<h1>VibeReading</h1><p class="s">把一份小说 txt 变成可滚动的图文阅读页。原文逐字保留，AI 只负责分镜。</p>
<label class="drop" id="drop">点击选择或拖入 .txt 文件<br><span id="fname"></span><input type="file" id="f" accept=".txt,text/plain"></label>
<div class="row"><input id="title" placeholder="标题（可选，留空由 AI 判断）"><input id="author" placeholder="作者（可选）"></div>
<label class="drop small" id="pdrop">可选：上传分镜 plan.json（跳过 AI，直接渲染）<br><span id="pname"></span><input type="file" id="pf" accept=".json,application/json"></label>
<div id="llm" class="n"></div>
<div class="row"><select id="model"></select><select id="theme"><option value="paper">默认主题：纸与墨</option><option value="night">默认主题：夜间</option></select>
<select id="verify"><option value="1">生成后自动校验</option><option value="0">不校验（更快）</option></select></div>
<button id="go" disabled>生成阅读页</button>
<div class="bar"><i id="pct"></i></div><div id="log"></div><div id="done"></div>
<small class="n">文本会发送给所选模型做分镜规划（模型只返回分镜 JSON，正文一字不改）。单次建议 ≤ 20 万字。</small>
</main><script>
const $=s=>document.querySelector(s);let text=null,name='',plan=null,CFG={llm:{}};
fetch('/api/config').then(r=>r.json()).then(c=>{CFG=c;const ok=Object.entries(c.llm).filter(([k,v])=>v);
 $('#model').innerHTML=Object.entries(c.labels).map(([k,l])=>`<option value="${k}" ${c.llm[k]?'':'disabled'}>${l}${c.llm[k]?'':'（未配置 key）'}</option>`).join('')+'<option value="plan">只用上传的 plan.json</option>';
 if(!ok.length){$('#model').value='plan';$('#llm').innerHTML='<b>未检测到 LLM 凭证</b>：请在服务端设置环境变量 INCEPTION_API_KEY（Mercury）或 DASHSCOPE_API_KEY（千问）后重启服务；现在可以上传现成的 plan.json 直接渲染。'}
 else $('#model').value=ok[0][0];upd()});
function upd(){$('#go').disabled=!(text&&text.trim())||($('#model').value==='plan'&&!plan)}
$('#model').onchange=upd;
$('#pf').onchange=e=>{const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{try{plan=JSON.parse(r.result);$('#pname').textContent=f.name;$('#model').value='plan'}catch(err){plan=null;$('#pname').textContent='不是合法 JSON'}upd()};r.readAsText(f)};
function load(file){name=file.name;$('#fname').textContent=file.name;const r=new FileReader();
 r.onload=()=>{const buf=new Uint8Array(r.result);let t=new TextDecoder('utf-8',{fatal:false}).decode(buf);
  if((t.match(/\uFFFD/g)||[]).length>5){try{t=new TextDecoder('gb18030').decode(buf)}catch(e){}}
  text=t;upd();$('#fname').textContent=file.name+' · '+text.length+' 字'};r.readAsArrayBuffer(file)}
$('#f').onchange=e=>e.target.files[0]&&load(e.target.files[0]);
const d=$('#drop');d.ondragover=e=>{e.preventDefault();d.classList.add('over')};d.ondragleave=()=>d.classList.remove('over');
d.ondrop=e=>{e.preventDefault();d.classList.remove('over');e.dataTransfer.files[0]&&load(e.dataTransfer.files[0])};
$('#go').onclick=async()=>{$('#go').disabled=true;$('#log').textContent='';$('#done').textContent='';
 const r=await fetch('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,text,plan:$('#model').value==='plan'?plan:null,model:$('#model').value==='plan'?null:$('#model').value,title:$('#title').value,author:$('#author').value,theme:$('#theme').value,verify:$('#verify').value==='1'})});
 const j=await r.json();if(!r.ok){$('#log').textContent=j.error;$('#go').disabled=false;return}poll(j.job)};
async function poll(id){const r=await fetch('/api/job/'+id);const j=await r.json();$('#pct').style.width=j.pct+'%';$('#log').textContent=j.log.slice(-8).join('\n');
 if(j.status==='done'){let v=j.verify?(j.verify.ok?'校验通过：原文 '+j.verify.matched+'/'+j.verify.segments+' 段一致':'校验未通过，详见报告'):'';
  $('#done').innerHTML='<p style="margin-top:12px">完成 · '+j.seconds+'s · '+v+'</p><p><a href="/read/'+id+'.html" target="_blank">打开阅读页 →</a></p>';window.open('/read/'+id+'.html','_blank');$('#go').disabled=false;return}
 if(j.status==='error'){$('#log').textContent+='\n出错：'+j.error;$('#go').disabled=false;return}setTimeout(()=>poll(id),1000)}
</script></body></html>"""


def run_job(jid, body):
    job = JOBS[jid]; t0 = time.time()
    def prog(stage, msg):
        job["log"].append(f"[{time.time() - t0:5.1f}s] {msg}")
        if stage == "detail+":
            job["pct"] = min(85, job["pct"] + 50 / max(1, job.get("batches", 3)))
        elif STAGE_PCT.get(stage) is not None:
            job["pct"] = max(job["pct"], STAGE_PCT[stage])
        if stage == "detail":
            try: job["batches"] = int(msg.split("：")[1].split(" ")[0])
            except Exception: pass
    try:
        rep = build(body["text"], OUT / f"{jid}.html", title=body.get("title") or None, author=body.get("author") or None,
                    theme=body.get("theme", "paper"), verify=bool(body.get("verify")), plan_in=body.get("plan") or None,
                    model=body.get("model") or None, progress=prog)
        job.update(status="done", pct=100, verify=rep.get("verify"), seconds=rep["total_seconds"])
    except Exception as e:
        job.update(status="error", error=f"{type(e).__name__}: {e}"); job["log"].append(traceback.format_exc()[-800:])


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/config":
            return self._send(200, {"llm": {k: planner.llm_available(k) for k in planner.PROVIDERS},
                                    "labels": {"mercury": "快速：Mercury mercury-3", "qwen": "质量：千问 qwen3.8-max"}})
        if self.path.startswith("/api/job/"):
            j = JOBS.get(self.path.rsplit("/", 1)[1])
            return self._send(200, j) if j else self._send(404, {"error": "no such job"})
        if self.path.startswith("/read/"):
            f = OUT / pathlib.Path(self.path).name
            if f.suffix == ".html" and f.exists():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/build":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        if n > 8_000_000:
            return self._send(413, {"error": "文件太大（>8MB）"})
        try:
            body = json.loads(self.rfile.read(n))
            assert isinstance(body.get("text"), str) and body["text"].strip()
            assert body.get("plan") is None or isinstance(body["plan"], dict)
        except Exception:
            return self._send(400, {"error": "需要 JSON {text: ...}"})
        if not body.get("plan") and not planner.llm_available(body.get("model")):
            return self._send(400, {"error": "服务端没有配置 LLM key（INCEPTION_API_KEY / DASHSCOPE_API_KEY），请上传 plan.json"})
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"status": "running", "pct": 1, "log": [f"收到 {body.get('name', '')}（{len(body['text'])} 字）"], "name": body.get("name")}
        threading.Thread(target=run_job, args=(jid, body), daemon=True).start()
        self._send(200, {"job": jid})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"VibeReading 上传服务：http://{a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
