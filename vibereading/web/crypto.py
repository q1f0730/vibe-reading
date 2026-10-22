"""用户模型 key 的服务器端加密存储：AES-256-GCM（cryptography 库），主密钥只来自环境变量 VR_SECRET_KEY。

- VR_SECRET_KEY：任意 ≥32 字符的随机串（建议 `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`），
  写在 ~/.config/vibereading.env（chmod 600），不进 git、不进数据库。经 HKDF-SHA256 派生出 32 字节密钥。
- 密文格式：v1:<base64url(12 字节随机 nonce + 密文 + 16 字节 tag)>；AAD 绑定 user_id，换用户/篡改都解不开。
- 没配置 VR_SECRET_KEY 时拒绝保存用户 key（不会退化成明文）。
"""
import base64, os, secrets

ENV = "VR_SECRET_KEY"


class CryptoUnavailable(Exception):
    pass


def _key():
    raw = os.environ.get(ENV, "")
    if len(raw) < 32:
        raise CryptoUnavailable(f"服务器没有配置加密密钥（环境变量 {ENV}，≥32 字符），不能保存模型 key")
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"vibereading/model-key/v1", info=b"aes-256-gcm").derive(raw.encode())


def available():
    try:
        _key(); return True
    except Exception:
        return False


def encrypt(plaintext, user_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("空 key")
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), f"user:{int(user_id)}".encode())
    return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token, user_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not token or not token.startswith("v1:"):
        raise ValueError("不认识的密文格式")
    blob = base64.urlsafe_b64decode(token[3:].encode())
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], f"user:{int(user_id)}".encode()).decode()
