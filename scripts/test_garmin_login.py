"""Diagnostic du login Garmin.

Usage :
    .venv/Scripts/python.exe -m scripts.test_garmin_login <email> <password>

Affiche le maximum d'infos pour comprendre pourquoi le login echoue.
"""

from __future__ import annotations

import logging
import sys
import traceback

# Active les logs DEBUG pour voir ce que fait la lib
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s : %(message)s",
)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage : python -m scripts.test_garmin_login <email> <password>")
        sys.exit(1)

    email, password = sys.argv[1], sys.argv[2]

    print(f"\n=== Test login Garmin pour {email} ===\n")

    try:
        from garminconnect import Garmin

        client = Garmin(
            email=email,
            password=password,
            return_on_mfa=True,
        )
        print("\n>>> Tentative de login...\n")
        mfa_status, _ = client.login()
        print(f"\n>>> mfa_status = {mfa_status!r}\n")

        if mfa_status == "needs_mfa":
            print("❌ Garmin demande la 2FA pour ton compte.")
            print(
                "   Solution : desactive la 2FA sur ton compte Garmin "
                "(garmin.com / Account / Privacy & Security)."
            )
            return

        # Si on arrive la, le login a marché
        print("✅ Login OK !")
        try:
            name = client.get_full_name()
            print(f"   Connecte en tant que : {name}")
        except Exception as exc:
            print(f"   (impossible de recuperer le nom : {exc})")

        token = client.client.dumps()
        print(f"   Token genere : {len(token)} caracteres")

    except Exception as exc:
        print(f"\n❌ Erreur lors du login : {exc}\n")
        print("--- Stack trace complete ---")
        traceback.print_exc()
        print("\n--- Pistes ---")
        s = str(exc).lower()
        if "401" in s or "unauthorized" in s or "invalid" in s:
            print(" - Identifiants incorrects : revere ton email/mdp en te connectant")
            print("   directement sur https://connect.garmin.com")
            print(" - 2FA active : desactive-la temporairement sur Garmin")
            print(" - Compte bloque suite a trop de tentatives : attends 15-30 min")
            print(" - VPN/IP suspect : essaie sans VPN")
            print(" - Captcha declenche par Garmin : connecte-toi une fois sur le")
            print("   site web normal pour debloquer, puis relance.")
        elif "captcha" in s:
            print(" - Garmin demande un captcha. Connecte-toi sur le site web normal,")
            print("   resous le captcha, puis relance.")
        else:
            print(" - Erreur reseau / endpoint Garmin down : reessaie dans qq minutes")


if __name__ == "__main__":
    main()
