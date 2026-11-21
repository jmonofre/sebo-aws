"""Fila FIFO e reservas atômicas de exemplares."""

import os
import time
import uuid

from boto3.dynamodb.conditions import Attr, Key

from comum import dados
from comum.notificacoes import enviar_email

AGUARDANDO = "AGUARDANDO"
RESERVADO = "RESERVADO"
VENDIDO = "VENDIDO"
CANCELADO = "CANCELADO"
NOTIFICACAO_PENDENTE = "PENDENTE"
NOTIFICACAO_ENVIANDO = "ENVIANDO"
NOTIFICACAO_ENVIADA = "ENVIADA"
_PRAZO_BLOQUEIO = 300


def _ordem(interesse):
    return interesse.get("criadoEm", ""), interesse.get("email", "")


def _aguardando(livro_id):
    itens = dados.consultar(
        dados.tabela_interesses(),
        KeyConditionExpression=Key("livroId").eq(livro_id),
        FilterExpression=Attr("status").eq(AGUARDANDO),
        ConsistentRead=True,
    )
    return sorted(itens, key=_ordem)


def reservar_primeiro(livro, modelo_email=None):
    """Tenta reservar atomicamente o primeiro interessado elegível.

    Se outra execução ganhar a corrida, tenta o próximo interessado. Assim,
    duas reservas concorrentes não podem consumir a mesma unidade.
    """
    for interesse in _aguardando(livro["livroId"]):
        reservado_em = dados.agora()
        reserva_id = uuid.uuid4().hex
        try:
            dados.executar_transacao([
                {"Update": {
                    "TableName": os.environ["TABELA_LIVROS"],
                    "Key": dados.chave_dynamodb({"livroId": livro["livroId"]}),
                    "UpdateExpression": "SET quantidade = quantidade - :um",
                    "ConditionExpression": "quantidade >= :um",
                    "ExpressionAttributeValues": dados.atributos_dynamodb({":um": 1}),
                }},
                {"Update": {
                    "TableName": os.environ["TABELA_INTERESSES"],
                    "Key": dados.chave_dynamodb({"livroId": livro["livroId"], "email": interesse["email"]}),
                    "UpdateExpression": "SET #s = :reservado, reservadoEm = :inicio, reservaId = :reserva, "
                                         "notificacaoStatus = :pendente",
                    "ConditionExpression": "#s = :aguardando",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": dados.atributos_dynamodb({
                        ":reservado": RESERVADO, ":aguardando": AGUARDANDO,
                        ":inicio": reservado_em, ":reserva": reserva_id,
                        ":pendente": NOTIFICACAO_PENDENTE,
                    }),
                }},
            ])
        except Exception as erro:
            if dados.erro_condicional(erro) or getattr(erro, "response", {}).get("Error", {}).get("Code") == "TransactionCanceledException":
                continue
            raise

        resultado = {**interesse, "status": RESERVADO, "reservadoEm": reservado_em, "reservaId": reserva_id}
        if modelo_email:
            resultado["notificacaoStatus"] = NOTIFICACAO_PENDENTE
            notificar_reserva(livro, resultado, modelo_email)
        return resultado
    return None

def _adquirir_notificacao(interesse):
    """Obtém um bloqueio curto para impedir dois envios concorrentes."""
    agora = int(time.time())
    try:
        dados.tabela_interesses().update_item(
            Key={"livroId": interesse["livroId"], "email": interesse["email"]},
            UpdateExpression="SET notificacaoStatus = :enviando, notificacaoBloqueioAte = :ate",
            ConditionExpression="#s = :reservado AND "
                               "(attribute_not_exists(notificacaoStatus) OR "
                               "notificacaoStatus = :pendente OR "
                               "(notificacaoStatus = :enviando AND "
                               "notificacaoBloqueioAte < :agora))",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":reservado": RESERVADO, ":pendente": NOTIFICACAO_PENDENTE,
                ":enviando": NOTIFICACAO_ENVIANDO, ":agora": agora,
                ":ate": agora + _PRAZO_BLOQUEIO,
            },
        )
        return True
    except Exception as erro:
        if dados.erro_condicional(erro):
            return False
        raise


def _voltar_para_pendente(interesse):
    try:
        dados.tabela_interesses().update_item(
            Key={"livroId": interesse["livroId"], "email": interesse["email"]},
            UpdateExpression="SET notificacaoStatus = :pendente REMOVE notificacaoBloqueioAte",
            ConditionExpression="#s = :reservado AND notificacaoStatus = :enviando",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":reservado": RESERVADO, ":enviando": NOTIFICACAO_ENVIANDO,
                                        ":pendente": NOTIFICACAO_PENDENTE},
        )
    except Exception as erro:
        if not dados.erro_condicional(erro):
            raise


def notificar_reserva(livro, interesse, modelo_email):
    """Publica uma reserva pendente e confirma a publicação condicionalmente.

    O sucesso aqui significa que o SNS aceitou a publicação; a entrega final
    ao e-mail continua sendo responsabilidade da assinatura do SNS.
    """
    if interesse.get("notificacaoStatus") == NOTIFICACAO_ENVIADA:
        return False
    if not _adquirir_notificacao(interesse):
        return False
    try:
        message_id = enviar_email(interesse["email"], *modelo_email(livro, interesse.get("nome")))
        try:
            dados.tabela_interesses().update_item(
                Key={"livroId": interesse["livroId"], "email": interesse["email"]},
                UpdateExpression="SET notificacaoStatus = :enviada, notificacaoEnviadaEm = :agora, "
                                 "notificacaoMessageId = :mensagem REMOVE notificacaoBloqueioAte",
                ConditionExpression="#s = :reservado AND notificacaoStatus = :enviando",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":reservado": RESERVADO, ":enviando": NOTIFICACAO_ENVIANDO,
                    ":enviada": NOTIFICACAO_ENVIADA, ":agora": dados.agora(),
                    ":mensagem": message_id,
                },
            )
        except Exception as erro:
            if not dados.erro_condicional(erro):
                raise
        return True
    except Exception:
        _voltar_para_pendente(interesse)
        raise


def reprocessar_pendentes(livro, modelo_email):
    """Reenvia somente reservas deste livro que ainda não foram publicadas."""
    itens = dados.consultar(
        dados.tabela_interesses(),
        KeyConditionExpression=Key("livroId").eq(livro["livroId"]),
        ConsistentRead=True,
    )
    total = 0
    for interesse in itens:
        if interesse.get("status") != RESERVADO:
            continue
        if interesse.get("notificacaoStatus") == NOTIFICACAO_ENVIADA:
            continue
        if notificar_reserva(livro, interesse, modelo_email):
            continue
        total += 1
    return total


def reservar_disponiveis(livro, limite=None, modelo_email=None):
    reservados = []
    while limite is None or len(reservados) < limite:
        atual = dados.tabela_livros().get_item(
            Key={"livroId": livro["livroId"]}, ConsistentRead=True
        ).get("Item")
        if not atual or int(atual.get("quantidade", 0)) <= 0:
            break
        reserva = reservar_primeiro(atual, modelo_email=modelo_email)
        if not reserva:
            break
        reservados.append(reserva)
    return reservados
