"""Lambda consultarLivros — catálogo público do sebo (somente leitura).

Rotas:
    GET /livros             lista todos os livros, com indicação de disponibilidade
    GET /livros/{livroId}   detalhes de um livro
"""

from comum import dados
from comum.http import ErroHttp, parametro_caminho, resposta, rota, tratar_erros


def _com_disponibilidade(livro):
    return {**livro, "disponivel": livro.get("quantidade", 0) > 0}


def listar_livros(event):
    livros = dados.varrer(dados.tabela_livros())
    livros.sort(key=lambda l: l.get("titulo", "").lower())
    return resposta(200, {"livros": [_com_disponibilidade(l) for l in livros]})


def obter_livro(event):
    livro_id = parametro_caminho(event, "livroId")
    livro = dados.tabela_livros().get_item(Key={"livroId": livro_id}).get("Item")
    if not livro:
        raise ErroHttp(404, f"Livro '{livro_id}' não encontrado")
    return resposta(200, _com_disponibilidade(livro))


ROTAS = {
    "GET /livros": listar_livros,
    "GET /livros/{livroId}": obter_livro,
}


@tratar_erros
def lambda_handler(event, context):
    acao = ROTAS.get(rota(event))
    if not acao:
        raise ErroHttp(404, f"Rota não suportada: {rota(event)}")
    return acao(event)
