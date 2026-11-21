"""Registro de vendas reservadas ou de balcão."""

import os
import uuid

from comum import dados, fila
from comum.http import ErroHttp, ler_corpo, resposta, rota, tratar_erros


def registrar_venda(event):
    corpo = ler_corpo(event)
    livro_id = dados.texto(corpo, "livroId", maximo=40)
    email = dados.email(corpo, obrigatorio=False)
    reserva_id = dados.texto(corpo, "reservaId", obrigatorio=False, maximo=64)
    colaborador = dados.texto(corpo, "colaborador", obrigatorio=False, maximo=100)
    livro = dados.tabela_livros().get_item(
        Key={"livroId": livro_id}, ConsistentRead=True
    ).get("Item")
    if not livro:
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")

    venda = {
        "vendaId": str(uuid.uuid4()), "livroId": livro_id, "titulo": livro["titulo"],
        "valor": dados.dinheiro(corpo, "valor", obrigatorio=False, padrao=livro.get("preco")),
        "dataVenda": dados.agora(),
    }
    if email:
        venda["email"] = email
    if colaborador:
        venda["colaborador"] = colaborador

    transacoes = []
    if email:
        # Com e-mail, a venda só pode consumir a reserva desse cliente.
        condicao = "#s = :reservado"
        valores = {
            ":vendido": fila.VENDIDO, ":reservado": fila.RESERVADO,
            ":agora": dados.agora(),
        }
        if reserva_id:
            condicao += " AND reservaId = :reserva"
            valores[":reserva"] = reserva_id
        transacoes.append({"Update": {
            "TableName": os.environ["TABELA_INTERESSES"],
            "Key": dados.chave_dynamodb({"livroId": livro_id, "email": email}),
            "UpdateExpression": "SET #s = :vendido, vendidoEm = :agora",
            "ConditionExpression": condicao,
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": dados.atributos_dynamodb(valores),
        }})
    else:
        # Venda de balcão sem cliente identificado usa somente estoque disponível.
        transacoes.append({"Update": {
            "TableName": os.environ["TABELA_LIVROS"],
            "Key": dados.chave_dynamodb({"livroId": livro_id}),
            "UpdateExpression": "SET quantidade = quantidade - :um",
            "ConditionExpression": "quantidade >= :um",
            "ExpressionAttributeValues": dados.atributos_dynamodb({":um": 1}),
        }})
    transacoes.append({"Put": {
        "TableName": os.environ["TABELA_VENDAS"],
        "Item": dados.atributos_dynamodb(venda),
        "ConditionExpression": "attribute_not_exists(vendaId)",
    }})
    try:
        dados.executar_transacao(transacoes)
    except Exception as erro:
        if dados.transacao_cancelada_por_condicao(erro, 0):
            if email:
                raise ErroHttp(409, "A reserva não existe, já foi vendida ou cancelada")
            raise ErroHttp(409, f"Não há exemplares disponíveis de \"{livro['titulo']}\"")
        raise

    atual = dados.tabela_livros().get_item(
        Key={"livroId": livro_id}, ConsistentRead=True
    ).get("Item") or {}
    venda["estoqueRestante"] = atual.get("quantidade", 0)
    venda["reservaConsumida"] = bool(email)
    return resposta(201, venda)


def listar_vendas(event):
    vendas = dados.varrer(dados.tabela_vendas())
    vendas.sort(key=lambda v: v.get("dataVenda", ""), reverse=True)
    return resposta(200, {
        "vendas": vendas, "quantidade": len(vendas),
        "total": sum((v.get("valor") or 0) for v in vendas),
    })


ROTAS = {"POST /vendas": registrar_venda, "GET /vendas": listar_vendas}


@tratar_erros
def lambda_handler(event, context):
    acao = ROTAS.get(rota(event))
    if not acao:
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return acao(event)
