"""웹 화면(FreqUI) 로그인용 비밀번호와 키를 무작위로 만들어 config.private.json에 저장.
이 파일은 깃허브에 올라가지 않아요. 처음 한 번만 실행하면 됩니다.

  python setup_secrets.py
"""
import json
import secrets
from pathlib import Path

p = Path(__file__).with_name("config.private.json")
if p.exists():
    print(f"이미 있어요: {p} (다시 만들려면 파일을 지우고 실행)")
    cfg = json.loads(p.read_text(encoding="utf-8"))
else:
    cfg = {
        "api_server": {
            "enabled": True,
            "listen_ip_address": "0.0.0.0",
            "listen_port": 8080,
            "verbosity": "error",
            "enable_openapi": False,
            "jwt_secret_key": secrets.token_hex(32),
            "ws_token": secrets.token_urlsafe(32),
            "CORS_origins": [],
            "username": "freqtrader",
            "password": secrets.token_urlsafe(12),
        }
    }
    p.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    print(f"만들었어요: {p}")
a = cfg["api_server"]
print(f"\n웹 화면 주소: http://localhost:8080\n아이디: {a['username']}\n비밀번호: {a['password']}")
