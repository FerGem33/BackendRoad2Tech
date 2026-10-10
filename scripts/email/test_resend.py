"""Verifica la configuración SMTP de Resend definida en .env.local.

Uso:
    python3 scripts/email/test_resend.py               # sólo prueba conexión + login
    python3 scripts/email/test_resend.py --to a@b.com  # además envía un correo de prueba
    python3 scripts/email/test_resend.py --to a@b.com --api  # envía por la API HTTP (como api/internal/mail)
"""
import argparse
import json
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

try:  # el Python de python.org en macOS no trae certificados raíz
    import certifi
    CAFILE = certifi.where()
except ImportError:
    CAFILE = None

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ("RESEND_EMAIL", "RESEND_HOST", "RESEND_PORT", "RESEND_USER", "RESEND_PASS")


def load_env(path):
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def send_api(env, to):
    body = json.dumps({
        "from": f"Road2Tech <{env['RESEND_EMAIL']}>", "to": [to],
        "subject": "Road2Tech - prueba API Resend",
        "html": "<p>Correo de prueba enviado desde scripts/email/test_resend.py.</p>",
    }).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST", headers={
        "Authorization": f"Bearer {env['RESEND_PASS']}",
        "Content-Type": "application/json",
        "User-Agent": "road2tech-test/1.0",
    })
    ctx = ssl.create_default_context(cafile=CAFILE)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            print(f"API OK: id={json.load(resp)['id']} ({env['RESEND_EMAIL']} -> {to})")
    except urllib.error.HTTPError as e:
        sys.exit(f"API rechazó el correo: {e.code} {e.read().decode()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=ROOT / ".env.local", type=Path)
    parser.add_argument("--to", help="destinatario del correo de prueba")
    parser.add_argument("--api", action="store_true", help="enviar por la API HTTP en vez de SMTP (requiere --to)")
    args = parser.parse_args()

    env = load_env(args.env)
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        sys.exit(f"Faltan variables en {args.env}: {', '.join(missing)}")

    if args.api:
        if not args.to:
            sys.exit("--api requiere --to")
        send_api(env, args.to)
        return

    host, port = env["RESEND_HOST"], int(env["RESEND_PORT"])
    print(f"Conectando a {host}:{port} como {env['RESEND_USER']}...")
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(cafile=CAFILE), timeout=15) as smtp:
        smtp.login(env["RESEND_USER"], env["RESEND_PASS"])
        print("Login OK")

        if args.to:
            msg = EmailMessage()
            msg["From"] = env["RESEND_EMAIL"]
            msg["To"] = args.to
            msg["Subject"] = "Road2Tech - prueba SMTP Resend"
            msg.set_content("Correo de prueba enviado desde scripts/email/test_resend.py.")
            smtp.send_message(msg)
            print(f"Correo enviado de {env['RESEND_EMAIL']} a {args.to}")


if __name__ == "__main__":
    main()
