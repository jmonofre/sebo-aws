"""Lambda registrarInteresse — o cliente informa em qual livro tem interesse.

Rota:
    POST /interesses   {"livroId": "L001", "email": "ana@exemplo.com", "nome": "Ana"}

Regra do enunciado:
  * livro disponível   -> reserva imediata e e-mail "compareça à livraria"
  * livro indisponível -> cliente fica AGUARDANDO; será avisado pela Lambda
                          notificarChegada quando o livro chegar.

Para não perder avisos por condição de corrida, o cliente SEMPRE entra na fila
primeiro e só depois o estoque é conferido (leitura fortemente consistente):

  registrarInteresse:   grava AGUARDANDO ──► lê estoque
  gerenciarEstoque:                  incrementa estoque ──► Stream ──► notificarChegada lê a fila

Qualquer que seja a intercalação, ou esta função vê o estoque novo, ou a
notificarChegada vê o cliente na fila. Se as duas virem, a transição
condicional AGUARDANDO -> RESERVADO garante uma única reserva.
"""

from comum import dados, fila
from comum.http import ErroHttp, ler_corpo, resposta, rota, tratar_erros
from comum.notificacoes import email_livro_disponivel


def _ler_livro(livro_id):
    # ConsistentRead: queremos o estoque mais recente, não uma réplica atrasada.
    return dados.tabela_livros().get_item(Key={"livroId": livro_id}, ConsistentRead=True).get("Item")


def _entrar_na_fila(livro_id, email, nome):
    """Grava o interesse como AGUARDANDO. Retorna False se já estava aguardando."""
    try:
        dados.tabela_interesses().put_item(
            Item={
                "livroId": livro_id,
                "email": email,
                "nome": nome or "",
                "status": fila.AGUARDANDO,
                "criadoEm": dados.agora(),
            },
            # Se o cliente já está na fila, mantém o registro original
            # (e a posição dele, dada por criadoEm).
            ConditionExpression="attribute_not_exists(email) OR (#s <> :aguardando AND #s <> :reservado)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":aguardando": fila.AGUARDANDO, ":reservado": fila.RESERVADO},
        )
        return True
    except Exception as erro:
        if dados.erro_condicional(erro):
            return False
        raise


def registrar_interesse(event):
    corpo = ler_corpo(event)
    livro_id = dados.texto(corpo, "livroId", maximo=40)
    email = dados.email(corpo)
    nome = dados.texto(corpo, "nome", obrigatorio=False, maximo=100)

    livro = _ler_livro(livro_id)
    if not livro:
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")

    novo = _entrar_na_fila(livro_id, email, nome)

    reservas = fila.reservar_disponiveis(livro, limite=1, modelo_email=email_livro_disponivel)
    reserva = reservas[0] if reservas else None
    if reserva and reserva["email"] == email:
        return resposta(200, {
            "status": fila.RESERVADO,
            "reservaId": reserva["reservaId"],
            "mensagem": f"O livro \"{livro['titulo']}\" foi reservado para {email}. "
                        "Compareça à livraria para realizar a compra.",
        })
    atual = dados.tabela_interesses().get_item(
        Key={"livroId": livro_id, "email": email}, ConsistentRead=True
    ).get("Item") or {}
    return resposta(200, {
        "status": atual.get("status", fila.AGUARDANDO),
        "jaEstavaNaFila": not novo,
        "mensagem": f"O livro \"{livro['titulo']}\" não está disponível no momento. "
                    f"Avisaremos por e-mail ({email}) quando ele chegar.",
    })


@tratar_erros
def lambda_handler(event, context):
    if rota(event) != "POST /interesses":
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return registrar_interesse(event)
