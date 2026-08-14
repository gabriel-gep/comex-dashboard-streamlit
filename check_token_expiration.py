"""
check_token_expiration.py

Script standalone (roda fora do Streamlit, via GitHub Actions agendado)
que verifica se o token da API DataWeb está perto de expirar e, se sim,
dispara um e-mail de alerta via API do SendGrid.

Variáveis de ambiente esperadas (configuradas como GitHub Secrets):
    TOKEN_EXPIRES_ON     -> data "YYYY-MM-DD" em que o token expira
                             (a data que a USITC mostrou ao gerar o token)
    SENDGRID_API_KEY     -> API key do SendGrid
    EMAIL_FROM            -> remetente verificado no SendGrid (Single
                              Sender Verification ou domínio autenticado)
    ALERT_TO              -> e-mail que vai receber o alerta
    WARN_DAYS_BEFORE      -> (opcional) dias de antecedência, default 15

Requer: pip install requests
"""

import datetime as dt
import os
import sys

import requests

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


def token_status(token_expires_on: str, warn_days_before: int) -> dict:
    expires_on = dt.date.fromisoformat(token_expires_on)
    days_left = (expires_on - dt.date.today()).days
    return {
        "expires_on": expires_on,
        "days_left": days_left,
        "should_warn": days_left <= warn_days_before,
        "is_expired": days_left < 0,
    }


def send_email(subject: str, body: str) -> None:
    api_key = os.environ["SENDGRID_API_KEY"]
    from_addr = os.environ["EMAIL_FROM"]
    to_addr = os.environ["ALERT_TO"]

    payload = {
        "personalizations": [{"to": [{"email": to_addr}]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }

    resp = requests.post(
        SENDGRID_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if resp.status_code not in (200, 202):
        raise RuntimeError(
            f"Falha ao enviar e-mail via SendGrid: "
            f"status {resp.status_code} -- {resp.text}"
        )


def main() -> None:
    token_expires_on = os.environ["TOKEN_EXPIRES_ON"]
    warn_days_before = int(os.environ.get("WARN_DAYS_BEFORE", "15"))

    status = token_status(token_expires_on, warn_days_before)

    print(f"Token expira em: {status['expires_on']} ({status['days_left']} dias restantes)")

    if status["is_expired"]:
        send_email(
            subject="🔴 URGENTE: Token DataWeb EXPIRADO",
            body=(
                f"O token da API USITC DataWeb expirou em {status['expires_on']}.\n\n"
                "Gere um novo token em https://dataweb.usitc.gov "
                "(aba API -> Generate Token) e atualize:\n"
                "- st.secrets['DATAWEB_TOKEN'] no Streamlit Cloud\n"
                "- o secret TOKEN_EXPIRES_ON no GitHub (com a nova data de expiração)\n"
            ),
        )
        print("E-mail de token EXPIRADO enviado via SendGrid.")
    elif status["should_warn"]:
        send_email(
            subject=f"🟡 Token DataWeb expira em {status['days_left']} dia(s)",
            body=(
                f"O token da API USITC DataWeb expira em {status['expires_on']} "
                f"({status['days_left']} dia(s) restantes).\n\n"
                "Gere um novo token em https://dataweb.usitc.gov "
                "(aba API -> Generate Token) e atualize:\n"
                "- st.secrets['DATAWEB_TOKEN'] no Streamlit Cloud\n"
                "- o secret TOKEN_EXPIRES_ON no GitHub (com a nova data de expiração)\n"
            ),
        )
        print("E-mail de aviso enviado via SendGrid.")
    else:
        print("Token ainda válido, nenhum e-mail necessário.")


if __name__ == "__main__":
    sys.exit(main())
