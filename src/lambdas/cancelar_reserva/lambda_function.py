"""Cancelamento explícito de uma reserva pelo colaborador da livraria."""

import os

from comum import dados, fila
from comum.http import ErroHttp, ler_corpo, resposta, rota, tratar_erros
from comum.notificacoes import email_livro_disponivel


def cancelar_reserva(event):
    corpo = ler_corpo(event)
    livro_id = dados.texto(corpo, "livroId", maximo=40)
    email = dados.email(corpo)
    reserva_id = dados.texto(corpo, "reservaId", obrigatorio=False, maximo=64)

    livro = dados.tabela_livros().get_item(
        Key={"livroId": livro_id}, ConsistentRead=True
    ).get("Item")
    if not livro:
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")

    condicao = "#s = :reservado"
    valores = {":cancelado": fila.CANCELADO, ":reservado": fila.RESERVADO, ":agora": dados.agora()}
    if reserva_id:
        condicao += " AND reservaId = :reserva"
        valores[":reserva"] = reserva_id

    try:
        dados.executar_transacao([
            {"Update": {
                "TableName": os.environ["TABELA_INTERESSES"],
                "Key": dados.chave_dynamodb({"livroId": livro_id, "email": email}),
                "UpdateExpression": "SET #s = :cancelado, canceladoEm = :agora",
                "ConditionExpression": condicao,
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": dados.atributos_dynamodb(valores),
            }},
            {"Update": {
                "TableName": os.environ["TABELA_LIVROS"],
                "Key": dados.chave_dynamodb({"livroId": livro_id}),
                "UpdateExpression": "SET quantidade = quantidade + :um",
                "ConditionExpression": "attribute_exists(livroId)",
                "ExpressionAttributeValues": dados.atributos_dynamodb({":um": 1}),
            }},
        ])
    except Exception as erro:
        if getattr(erro, "response", {}).get("Error", {}).get("Code") == "TransactionCanceledException":
            raise ErroHttp(409, "A reserva não existe, já foi vendida ou já foi cancelada")
        raise

    proxima = fila.reservar_disponiveis(
        livro, limite=1, modelo_email=email_livro_disponivel
    )
    return resposta(200, {
        "status": fila.CANCELADO,
        "livroId": livro_id,
        "email": email,
        "proximaReserva": proxima[0] if proxima else None,
    })


@tratar_erros
def lambda_handler(event, context):
    if rota(event) != "POST /reservas/cancelar":
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return cancelar_reserva(event)
