#!/usr/bin/env python3
"""Gera src/frontend/config.js a partir da variável API_GATEWAY_URL."""

import json
import os
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_ENV = RAIZ / ".env"
DESTINO = RAIZ / "src" / "frontend" / "config.js"


def carregar_env() -> dict[str, str]:
    valores: dict[str, str] = {}
    if ARQUIVO_ENV.is_file():
        for linha in ARQUIVO_ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            valores[chave.strip()] = valor.strip().strip("\"'")
    return valores


def main() -> None:
    url = os.environ.get("API_GATEWAY_URL") or carregar_env().get("API_GATEWAY_URL", "")
    url = url.strip().rstrip("/")
    destino = json.dumps(url, ensure_ascii=False)
    DESTINO.write_text(
        f"window.SEBO_CONFIG = Object.freeze({{ apiUrl: {destino} }});\n",
        encoding="utf-8",
    )
    if not url:
        print("AVISO: API_GATEWAY_URL não foi configurada. config.js foi gerado com uma URL vazia.")
    else:
        print(f"Configurado: {DESTINO}")


if __name__ == "__main__":
    main()
