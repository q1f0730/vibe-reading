"""用户自己的模型配置：保存（key 加密）、测试、后台探针、构造 Engine。

安全：
  - key 只以 AES-GCM 密文存库（crypto.py），解密只发生在任务线程 / 探针线程里，用完即弃；
  - 对外（API / 页面 / 日志 / 报错）只出现掩码（…abcd）；所有错误信息经 llm.redact；
  - base_url 必须是 http(s)，且默认不允许指向内网 / 回环 / 链路本地地址（防 SSRF；VR_ALLOW_PRIVATE_LLM=1 可放开，本地自建模型用）。
"""
import ipaddress, json, os, socket, threading, time
from urllib.parse import urlsplit

from . import db, crypto
from .. import llm, probe, tiers

EDITABLE = ("kind", "base_url", "model", "label", "timeout", "max_total_seconds", "concurrency", "stream", "reasoning", "temperature")
REASONING = ("auto", "off", "minimal", "low", "medium", "high", "instant")


class ConfigError(Exception):
    pass


def check_public_url(url):
    if os.environ.get("VR_ALLOW_PRIVATE_LLM") == "1":
        return
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ConfigError("base_url 必须是 http(s):// 地址")
    if u.scheme == "http" and os.environ.get("VR_ALLOW_HTTP_LLM") != "1":
        raise ConfigError("为保护 key，base_url 必须是 https://")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ConfigError(f"解析不了域名 {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ConfigError("base_url 指向内网 / 本机地址，已拒绝")


def clean_config(body):
    """表单 → 公开配置 dict（不含 key）。不合法抛 ConfigError。"""
    d = {}
    for k in EDITABLE:
        if k in body and body[k] not in (None, ""):
            d[k] = body[k]
    d["kind"] = d.get("kind") or "openai"
    if d["kind"] not in llm.KINDS:
        raise ConfigError("provider 类型只能是 openai（兼容）/ anthropic / gemini")
    try:
        if "timeout" in d: d["timeout"] = float(d["timeout"])
        if "max_total_seconds" in d: d["max_total_seconds"] = float(d["max_total_seconds"])
        if "concurrency" in d: d["concurrency"] = int(d["concurrency"])
        if "temperature" in d: d["temperature"] = max(0.0, min(2.0, float(d["temperature"])))
    except (TypeError, ValueError):
        raise ConfigError("数值选项格式不对")
    d["stream"] = bool(body.get("stream"))
    if d.get("reasoning", "auto") not in REASONING:
        raise ConfigError("推理强度只能是 " + " / ".join(REASONING))
    for k in ("base_url", "model", "label"):
        if k in d:
            d[k] = str(d[k]).strip()[:300]
    try:
        cfg = llm.ModelConfig.from_dict(d)          # 复用 dataclass 的校验
    except (ValueError, TypeError) as e:
        raise ConfigError(str(e))
    check_public_url(cfg.base_url)
    pub = cfg.to_public(); pub.pop("key", None); pub.pop("simulate", None); pub.pop("extra", None)
    return pub


def get_row(user_id):
    return db.conn().execute("SELECT * FROM model_configs WHERE user_id=?", (user_id,)).fetchone()


def public_view(row):
    if not row:
        return None
    cfg = json.loads(row["config_json"])
    prof = json.loads(row["profile_json"]) if row["profile_json"] else None
    return {"config": cfg, "key": row["key_hint"] or "", "has_key": bool(row["key_enc"]), "status": row["status"], "tier": row["tier"],
            "error": row["error"], "profile": prof, "updated_at": row["updated_at"]}


def build_client(cfg_pub, api_key):
    return llm.Client(llm.ModelConfig.from_dict(cfg_pub, api_key=api_key))


def save(user_id, body):
    """保存配置（key 留空 = 沿用已存的）。返回 row 的公开视图；探针在后台线程跑。"""
    cfg = clean_config(body)
    row = get_row(user_id)
    key = body.get("api_key")
    if isinstance(key, str) and key.strip():
        key = key.strip()
        if len(key) > 4000 or any(c.isspace() for c in key):
            raise ConfigError("key 格式不对")
        try:
            enc = crypto.encrypt(key, user_id)
        except crypto.CryptoUnavailable as e:
            raise ConfigError(str(e))
        hint = llm.mask_key(key)
    elif row and row["key_enc"]:
        enc, hint = row["key_enc"], row["key_hint"]
    else:
        raise ConfigError("需要填写 API key")
    now = time.time()
    db.conn().execute("INSERT INTO model_configs(user_id, config_json, key_enc, key_hint, profile_json, tier, status, error, updated_at) "
                      "VALUES(?,?,?,?,NULL,NULL,'probing',NULL,?) ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json, "
                      "key_enc=excluded.key_enc, key_hint=excluded.key_hint, profile_json=NULL, tier=NULL, status='probing', error=NULL, updated_at=excluded.updated_at",
                      (user_id, json.dumps(cfg, ensure_ascii=False), enc, hint, now))
    threading.Thread(target=_probe_job, args=(user_id, now), daemon=True, name=f"probe-{user_id}").start()
    return public_view(get_row(user_id))


def _probe_job(user_id, stamp):
    c = db.connect()
    try:
        row = c.execute("SELECT * FROM model_configs WHERE user_id=?", (user_id,)).fetchone()
        if not row or row["updated_at"] != stamp:
            return
        key = crypto.decrypt(row["key_enc"], user_id)
        client = build_client(json.loads(row["config_json"]), key)
        del key
        prof = probe.run(client)
        st = "ready" if prof.get("ok") else "failed"
        err = None if prof.get("ok") else "; ".join(prof.get("tier_reasons") or prof.get("errors") or ["探针失败"])[:400]
        c.execute("UPDATE model_configs SET profile_json=?, tier=?, status=?, error=? WHERE user_id=? AND updated_at=?",
                  (json.dumps(prof, ensure_ascii=False), prof.get("tier"), st, err, user_id, stamp))
    except Exception as e:
        c.execute("UPDATE model_configs SET status='failed', error=? WHERE user_id=? AND updated_at=?",
                  (llm.redact(f"{type(e).__name__}: {e}")[:300], user_id, stamp))
    finally:
        c.close()


def test(user_id, body):
    """设置页「测试」：用表单里的配置（key 留空用已存的）跑一次连通性探针，不保存。"""
    cfg = clean_config(body)
    key = (body.get("api_key") or "").strip()
    if not key:
        row = get_row(user_id)
        if not row or not row["key_enc"]:
            raise ConfigError("需要填写 API key")
        key = crypto.decrypt(row["key_enc"], user_id)
    client = build_client(cfg, key)
    del key
    p = probe.run(client, quick=True)
    ping = p["probes"].get("ping", {})
    return {"ok": bool(p.get("ok")), "latency": ping.get("latency"), "reply": ping.get("reply"), "reasoning": ping.get("reasoning"),
            "error": ping.get("error"), "adaptations": p.get("adaptations") or list(client.events)}


def delete(user_id):
    db.conn().execute("DELETE FROM model_configs WHERE user_id=?", (user_id,))


def ready(user_id):
    row = get_row(user_id)
    return bool(row and row["status"] == "ready" and row["key_enc"])


def engine_for_user(user_id):
    """任务线程里调用：解密 key → Engine。用户模型被内容过滤时不偷偷改用平台模型（直接拆批 / 规则兜底）。"""
    row = get_row(user_id)
    if not row or row["status"] != "ready":
        raise ConfigError("你的模型配置还没有通过探针（设置页可以重新探测）")
    cfg = json.loads(row["config_json"])
    check_public_url(cfg["base_url"])
    key = crypto.decrypt(row["key_enc"], user_id)
    prof = json.loads(row["profile_json"]) if row["profile_json"] else {}
    if prof.get("max_output_ok"):
        cfg["max_output"] = int(prof["max_output_ok"])
    eng = tiers.Engine(build_client(cfg, key), prof, prof.get("tier"), fallback=None, source="user")
    del key
    return eng
