"""Envio de e-mail aos clientes via tópico SNS.

Todos os clientes estão inscritos no MESMO tópico, cada um com uma
filter policy do tipo {"email": ["cliente@exemplo.com"]}. Publicamos a
mensagem com o atributo `email` do destinatário e o SNS entrega apenas
à assinatura cuja política casa com esse valor.
"""

import os
import unicodedata
from functools import lru_cache

import boto3


@lru_cache(maxsize=None)
def _sns():
    return boto3.client("sns")


def _assunto_seguro(assunto):
    # O assunto de e-mail do SNS deve ter < 100 caracteres e sem quebras de
    # linha; removemos acentos para evitar problemas de codificação.
    sem_acento = unicodedata.normalize("NFKD", assunto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.split())[:99]


def enviar_email(destinatario, assunto, mensagem):
    """Publica no tópico um e-mail direcionado a `destinatario`. Retorna o MessageId."""
    resp = _sns().publish(
        TopicArn=os.environ["TOPICO_ARN"],
        Subject=_assunto_seguro(assunto),
        Message=mensagem,
        MessageAttributes={"email": {"DataType": "String", "StringValue": destinatario}},
    )
    print(f"E-mail publicado para {destinatario}: {resp['MessageId']}")
    return resp["MessageId"]


def _preco(livro):
    preco = livro.get("preco")
    return "—" if preco is None else f"{preco:.2f}".replace(".", ",")


def email_livro_disponivel(livro, nome=None):
    saudacao = f"Olá, {nome}!" if nome else "Olá!"
    return (
        f"Livro disponível: {livro['titulo']}",
        f"{saudacao}\n\n"
        f"O livro \"{livro['titulo']}\", de {livro.get('autor', 'autor desconhecido')}, "
        f"está disponível no sebo.\n"
        f"Preço: R$ {_preco(livro)}\n\n"
        "Uma unidade foi reservada exclusivamente para você.\n"
        "Compareça à livraria para realizar a compra.\n\n"
        "— Sebo",
    )


def email_livro_chegou(livro, nome=None):
    saudacao = f"Olá, {nome}!" if nome else "Olá!"
    return (
        f"Chegou: {livro['titulo']}",
        f"{saudacao}\n\n"
        f"Boa notícia: o livro \"{livro['titulo']}\", de {livro.get('autor', 'autor desconhecido')}, "
        "que você estava aguardando, acabou de chegar ao sebo e foi reservado para você.\n"
        f"Preço: R$ {_preco(livro)}\n\n"
        "Compareça à livraria para efetivar a compra.\n\n"
        "— Sebo",
    )
