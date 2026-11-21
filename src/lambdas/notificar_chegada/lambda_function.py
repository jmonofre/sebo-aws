"""Lambda notificarChegada — avisa por e-mail quem aguardava um livro que chegou.

Gatilho: DynamoDB Stream da tabela de livros (não é chamada pela API).
Cada alteração num livro gera um registro com a versão antiga (OldImage) e a
nova (NewImage) do item. Se a quantidade AUMENTOU, exemplares chegaram e os
clientes com status AGUARDANDO naquele livro recebem uma reserva e o e-mail.

Entrega "pelo menos uma vez": se esta função falhar, o Lambda reprocessa o
mesmo lote do Stream. Reprocessar é seguro porque cada aviso passa pela
transição condicional AGUARDANDO -> RESERVADO (comum/fila.py): quem já foi
reservado na tentativa anterior é pulado.
"""

from boto3.dynamodb.types import TypeDeserializer

from comum import dados, fila
from comum.http import ErroHttp, ler_corpo, resposta, rota, tratar_erros
from comum.notificacoes import email_livro_chegou

_desserializar = TypeDeserializer().deserialize


def _imagem(registro, qual):
    """Converte NewImage/OldImage do formato DynamoDB JSON ({'N': '3'}) para Python."""
    bruto = registro.get("dynamodb", {}).get(qual) or {}
    return {campo: _desserializar(valor) for campo, valor in bruto.items()}


def processar_chegada(livro, chegaram):
    reservas = fila.reservar_disponiveis(livro, limite=chegaram, modelo_email=email_livro_chegou)
    pendentes = fila.reprocessar_pendentes(livro, email_livro_chegou)
    print(f"Livro {livro['livroId']}: chegaram {chegaram}, {len(reservas)} reserva(s)")
    return len(reservas), pendentes


def reprocessar_notificacao(event):
    corpo = ler_corpo(event)
    livro_id = dados.texto(corpo, "livroId", maximo=40)
    email = dados.email(corpo)
    livro = dados.tabela_livros().get_item(
        Key={"livroId": livro_id}, ConsistentRead=True
    ).get("Item")
    if not livro:
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")
    interesse = dados.tabela_interesses().get_item(
        Key={"livroId": livro_id, "email": email}, ConsistentRead=True
    ).get("Item")
    if not interesse:
        raise ErroHttp(404, "Interesse não encontrado")
    if interesse.get("status") != fila.RESERVADO:
        raise ErroHttp(409, "O interesse não possui uma reserva ativa")
    enviada = interesse.get("notificacaoStatus") == fila.NOTIFICACAO_ENVIADA
    if enviada:
        return resposta(200, {"status": fila.NOTIFICACAO_ENVIADA, "reenviada": False})
    publicada = fila.notificar_reserva(livro, interesse, email_livro_chegou)
    atual = dados.tabela_interesses().get_item(
        Key={"livroId": livro_id, "email": email}, ConsistentRead=True
    ).get("Item") or {}
    return resposta(200, {
        "status": atual.get("notificacaoStatus", fila.NOTIFICACAO_PENDENTE),
        "reenviada": publicada,
        "destinatario": email,
    })


def _processar_stream(event):
    total = 0
    for registro in event.get("Records", []):
        if registro.get("eventName") not in ("INSERT", "MODIFY"):
            continue  # REMOVE: livro apagado, nada a avisar
        novo, antigo = _imagem(registro, "NewImage"), _imagem(registro, "OldImage")
        chegaram = int(novo.get("quantidade", 0)) - int(antigo.get("quantidade", 0))
        if chegaram <= 0:
            continue  # venda (diminuiu) ou alteração sem mudança de estoque
        reservados, pendentes = processar_chegada(novo, chegaram)
        total += reservados
    return {"reservados": total}


@tratar_erros
def _processar_api(event, context):
    if rota(event) != "POST /notificacoes/reprocessar":
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return reprocessar_notificacao(event)


def lambda_handler(event, context):
    if "Records" in event:
        return _processar_stream(event)
    return _processar_api(event, context)
