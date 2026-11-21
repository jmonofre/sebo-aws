"""Utilitários HTTP para Lambdas integradas ao API Gateway (HTTP API, payload 2.0)."""

import functools
import json
import traceback
from decimal import Decimal

CABECALHOS = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
}


class ErroHttp(Exception):
    """Erro de negócio que deve virar uma resposta HTTP com o status indicado."""

    def __init__(self, status, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _json_padrao(valor):
    # O DynamoDB devolve números como Decimal, que o json não sabe serializar.
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    if isinstance(valor, set):
        return sorted(valor)
    raise TypeError(f"Tipo não serializável: {type(valor).__name__}")


def resposta(status, corpo):
    return {
        "statusCode": status,
        "headers": CABECALHOS,
        "body": json.dumps(corpo, default=_json_padrao, ensure_ascii=False),
    }


def ler_corpo(event):
    """Converte o corpo JSON da requisição em dict; erro 400 se for inválido."""
    corpo = event.get("body")
    if not corpo:
        return {}
    if event.get("isBase64Encoded"):
        import base64

        corpo = base64.b64decode(corpo).decode("utf-8")
    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        raise ErroHttp(400, "Corpo da requisição não é um JSON válido")
    if not isinstance(dados, dict):
        raise ErroHttp(400, "Corpo da requisição deve ser um objeto JSON")
    return dados


def rota(event):
    """Retorna a rota chamada, ex.: 'PUT /livros/{livroId}/estoque'."""
    return event.get("routeKey", "")


def parametro_caminho(event, nome):
    valor = (event.get("pathParameters") or {}).get(nome)
    if not valor:
        raise ErroHttp(400, f"Parâmetro de caminho '{nome}' ausente")
    return valor


def tratar_erros(handler):
    """Decorador: transforma ErroHttp em resposta e qualquer outra exceção em 500."""

    @functools.wraps(handler)
    def envolvido(event, context):
        try:
            return handler(event, context)
        except ErroHttp as erro:
            return resposta(erro.status, {"erro": erro.mensagem})
        except Exception:
            # Vai para o CloudWatch Logs; o cliente recebe uma mensagem genérica.
            traceback.print_exc()
            return resposta(500, {"erro": "Erro interno no servidor"})

    return envolvido
