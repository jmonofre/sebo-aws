"""Lambda gerenciarEstoque — operações do colaborador sobre o acervo.

Rotas:
    POST /livros                        cadastra um livro (pode ter quantidade 0 = "em falta")
    PUT  /livros/{livroId}/estoque      registra a chegada de N exemplares
    GET  /livros/{livroId}/interesses   lista os clientes interessados no livro

A chegada de exemplares só incrementa a quantidade. Quem avisa os clientes é a
Lambda notificarChegada, disparada pelo DynamoDB Stream quando a quantidade
aumenta — esta função não precisa saber que os avisos existem.
"""

import uuid

from comum import dados
from comum.http import ErroHttp, ler_corpo, parametro_caminho, resposta, rota, tratar_erros


def cadastrar_livro(event):
    corpo = ler_corpo(event)
    livro = {
        "livroId": dados.texto(corpo, "livroId", obrigatorio=False, maximo=40)
        or "L" + uuid.uuid4().hex[:8].upper(),
        "titulo": dados.texto(corpo, "titulo"),
        "autor": dados.texto(corpo, "autor"),
        "preco": dados.dinheiro(corpo, "preco"),
        "conservacao": dados.texto(corpo, "conservacao", obrigatorio=False, maximo=60) or "não informado",
        "quantidade": dados.inteiro(corpo, "quantidade", minimo=0, obrigatorio=False, padrao=0),
        "cadastradoEm": dados.agora(),
    }
    try:
        dados.tabela_livros().put_item(
            Item=livro,
            ConditionExpression="attribute_not_exists(livroId)",  # não sobrescreve
        )
    except Exception as erro:
        if dados.erro_condicional(erro):
            raise ErroHttp(409, f"Já existe um livro com id '{livro['livroId']}'")
        raise
    return resposta(201, livro)


def registrar_chegada(event):
    livro_id = parametro_caminho(event, "livroId")
    chegaram = dados.inteiro(ler_corpo(event), "quantidade", minimo=1)
    try:
        # Incremento ATÔMICO no servidor: dois colaboradores registrando
        # chegadas ao mesmo tempo não perdem atualizações (sem ler-modificar-escrever).
        atualizado = dados.tabela_livros().update_item(
            Key={"livroId": livro_id},
            UpdateExpression="SET quantidade = quantidade + :n, ultimaChegadaEm = :t",
            ConditionExpression="attribute_exists(livroId)",
            ExpressionAttributeValues={":n": chegaram, ":t": dados.agora()},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except Exception as erro:
        if dados.erro_condicional(erro):
            raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")
        raise
    return resposta(200, atualizado)


def listar_interessados(event):
    livro_id = parametro_caminho(event, "livroId")
    if not dados.tabela_livros().get_item(Key={"livroId": livro_id}).get("Item"):
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")
    interesses = dados.consultar(
        dados.tabela_interesses(),
        KeyConditionExpression="livroId = :id",
        ExpressionAttributeValues={":id": livro_id},
    )
    interesses.sort(key=lambda i: i.get("criadoEm", ""))
    return resposta(200, {"livroId": livro_id, "interesses": interesses})


ROTAS = {
    "POST /livros": cadastrar_livro,
    "PUT /livros/{livroId}/estoque": registrar_chegada,
    "GET /livros/{livroId}/interesses": listar_interessados,
}


@tratar_erros
def lambda_handler(event, context):
    acao = ROTAS.get(rota(event))
    if not acao:
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return acao(event)
