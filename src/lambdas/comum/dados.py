"""Acesso ao DynamoDB e validação dos dados de entrada."""

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from comum.http import ErroHttp

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_serializador = TypeSerializer()
_desserializador = TypeDeserializer()
_logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _dynamodb():
    # Criado uma única vez por instância da Lambda e reaproveitado entre
    # invocações "quentes" (evita recriar conexões a cada requisição).
    return boto3.resource("dynamodb")


def tabela_livros():
    return _dynamodb().Table(os.environ["TABELA_LIVROS"])


def tabela_interesses():
    return _dynamodb().Table(os.environ["TABELA_INTERESSES"])


def tabela_vendas():
    return _dynamodb().Table(os.environ["TABELA_VENDAS"])


@lru_cache(maxsize=None)
def cliente_dynamodb():
    return boto3.client("dynamodb")


def atributos_dynamodb(item):
    """Converte um item Python para o formato exigido pelo cliente low-level."""
    return {chave: _serializador.serialize(valor) for chave, valor in item.items()}


def valor_dynamodb(valor):
    """Converte um valor Python para um AttributeValue do cliente low-level."""
    return _serializador.serialize(valor)


def chave_dynamodb(chave):
    return atributos_dynamodb(chave)


def executar_transacao(transacoes):
    """Executa uma transação low-level, com compatibilidade para o Moto local."""
    cliente = cliente_dynamodb()
    try:
        return cliente.transact_write_items(TransactItems=transacoes)
    except Exception as erro:
        motivos = motivos_cancelamento(erro)
        if _codigo_erro(erro) == "TransactionCanceledException":
            _logger.error(
                "Transação DynamoDB cancelada: codigo=%s motivos=%s",
                _codigo_erro(erro),
                [
                    {
                        "indice": indice,
                        "codigo": (motivo or {}).get("Code"),
                        "motivo": (motivo or {}).get("Message"),
                    }
                    for indice, motivo in enumerate(motivos)
                ],
            )
        if not motivos or not all((m or {}).get("Code") == "TypeError" for m in motivos):
            raise
        # Algumas versões do Moto ainda esperam valores Python no endpoint
        # simulado. A tentativa primária acima permanece no formato da AWS.
        simples = []
        for transacao in transacoes:
            operacao = {}
            for nome, configuracao in transacao.items():
                configuracao = dict(configuracao)
                for campo in ("Key", "Item", "ExpressionAttributeValues"):
                    if campo in configuracao:
                        configuracao[campo] = {
                            chave: _desserializar_atributo(valor)
                            for chave, valor in configuracao[campo].items()
                        }
                operacao[nome] = configuracao
            simples.append(operacao)
        return cliente.transact_write_items(TransactItems=simples)


def _codigo_erro(erro):
    return getattr(erro, "response", {}).get("Error", {}).get("Code")


def motivos_cancelamento(erro):
    """Retorna os motivos da transação na mesma ordem de `TransactItems`."""
    motivos = getattr(erro, "response", {}).get("CancellationReasons", [])
    return motivos if isinstance(motivos, list) else []


def transacao_cancelada_por_condicao(erro, indice):
    """Indica se uma operação específica falhou por sua ConditionExpression."""
    if indice < 0 or _codigo_erro(erro) != "TransactionCanceledException":
        return False
    motivos = motivos_cancelamento(erro)
    return (
        len(motivos) > indice
        and (motivos[indice] or {}).get("Code") == "ConditionalCheckFailed"
    )


def _desserializar_atributo(valor):
    if isinstance(valor, dict) and len(valor) == 1 and next(iter(valor)) in "BSN":
        return _desserializador.deserialize(valor)
    if isinstance(valor, dict) and len(valor) == 1 and next(iter(valor)) in "M":
        return {chave: _desserializar_atributo(item) for chave, item in valor["M"].items()}
    if isinstance(valor, dict) and len(valor) == 1 and next(iter(valor)) in "L":
        return [_desserializar_atributo(item) for item in valor["L"]]
    return valor


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def varrer(tabela, **kwargs):
    """Scan paginado: o DynamoDB devolve no máximo 1 MB por chamada."""
    itens, resp = [], tabela.scan(**kwargs)
    itens.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = tabela.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        itens.extend(resp["Items"])
    return itens


def consultar(tabela, **kwargs):
    """Query paginada."""
    itens, resp = [], tabela.query(**kwargs)
    itens.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = tabela.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        itens.extend(resp["Items"])
    return itens


def erro_condicional(erro):
    """True se a exceção do boto3 for uma falha de ConditionExpression."""
    return getattr(erro, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


# ---------------------------------------------------------------- validação


def texto(dados, campo, obrigatorio=True, maximo=200):
    valor = dados.get(campo)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if obrigatorio:
            raise ErroHttp(400, f"Campo '{campo}' é obrigatório")
        return None
    if not isinstance(valor, str):
        raise ErroHttp(400, f"Campo '{campo}' deve ser texto")
    valor = valor.strip()
    if len(valor) > maximo:
        raise ErroHttp(400, f"Campo '{campo}' excede {maximo} caracteres")
    return valor


def email(dados, campo="email", obrigatorio=True):
    valor = texto(dados, campo, obrigatorio=obrigatorio, maximo=254)
    if valor is None:
        return None
    # Normalizado em minúsculas: precisa bater exatamente com a filter policy do SNS.
    valor = valor.lower()
    if not _EMAIL.match(valor):
        raise ErroHttp(400, f"Campo '{campo}' não é um e-mail válido")
    return valor


def inteiro(dados, campo, minimo=0, obrigatorio=True, padrao=None):
    valor = dados.get(campo)
    if valor is None:
        if obrigatorio:
            raise ErroHttp(400, f"Campo '{campo}' é obrigatório")
        return padrao
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErroHttp(400, f"Campo '{campo}' deve ser um número inteiro")
    if valor < minimo:
        raise ErroHttp(400, f"Campo '{campo}' deve ser >= {minimo}")
    return valor


def dinheiro(dados, campo, obrigatorio=True, padrao=None):
    valor = dados.get(campo)
    if valor is None:
        if obrigatorio:
            raise ErroHttp(400, f"Campo '{campo}' é obrigatório")
        return padrao
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        raise ErroHttp(400, f"Campo '{campo}' deve ser numérico")
    try:
        # str() evita imprecisão de float (ex.: 19.9 -> Decimal('19.9'))
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ErroHttp(400, f"Campo '{campo}' deve ser numérico")
    if valor < 0:
        raise ErroHttp(400, f"Campo '{campo}' não pode ser negativo")
    return valor
