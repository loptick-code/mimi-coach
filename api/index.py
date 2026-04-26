"""Entry point pour Vercel.

Vercel detecte ce fichier (api/*.py) et route toutes les requetes
HTTP vers l'app FastAPI exportee ici.
"""

from app.main import app  # noqa: F401

# Vercel utilise `app` comme variable ASGI par defaut.
