// Interface do sebo: aba do cliente (catálogo + interesse) e do colaborador
// (estoque, chegadas, vendas). Toda comunicação é feita com a API HTTP.
"use strict";

// ------------------------------------------------------------------ API

function urlDaApi() {
  const configurada = window.SEBO_CONFIG && window.SEBO_CONFIG.apiUrl;
  return (typeof configurada === "string" ? configurada : "").trim().replace(/\/+$/, "");
}

async function api(metodo, caminho, corpo) {
  const base = urlDaApi();
  if (!base) throw new Error("URL da API não configurada. Gere src/frontend/config.js a partir do arquivo .env.");
  const opcoes = { method: metodo, headers: {} };
  if (corpo !== undefined) {
    opcoes.headers["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(corpo);
  }
  let resp;
  try {
    resp = await fetch(base + caminho, opcoes);
  } catch (e) {
    throw new Error("Não foi possível falar com a API. Confira a URL e se o laboratório da AWS está ativo.");
  }
  const dados = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(dados.erro || `Erro ${resp.status}`);
  return dados;
}

// ------------------------------------------------------------------ utilidades

const $ = (sel) => document.querySelector(sel);

function el(tag, atributos = {}, ...filhos) {
  // Cria elementos com textContent (nunca innerHTML com dados da API: evita XSS)
  const no = document.createElement(tag);
  for (const [k, v] of Object.entries(atributos)) {
    if (v === null || v === undefined) continue;  // atributo ausente (ex.: disabled: null)
    if (k === "class") no.className = v;
    else if (k.startsWith("on")) no.addEventListener(k.slice(2), v);
    else no.setAttribute(k, v);
  }
  for (const f of filhos.flat()) {
    if (f !== null && f !== undefined) no.append(f instanceof Node ? f : String(f));
  }
  return no;
}

const reais = (v) => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const dataHora = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—");

function notificar(mensagem, tipo = "info") {
  const n = el("div", { class: `notificacao ${tipo}`, role: "status" }, mensagem);
  $("#notificacoes").append(n);
  setTimeout(() => n.remove(), tipo === "erro" ? 8000 : 6000);
}

function dadosDoFormulario(form) {
  return Object.fromEntries(new FormData(form).entries());
}

// Fecha o diálogo e resolve com os dados do formulário (ou null se cancelado)
function abrirDialogo(dialogo) {
  const form = dialogo.querySelector("form");
  form.reset();
  dialogo.returnValue = "";  // senão herdaria o "enviar" da última vez que foi aberto
  dialogo.showModal();
  return new Promise((resolver) => {
    dialogo.addEventListener("close", () => {
      resolver(dialogo.returnValue === "enviar" ? dadosDoFormulario(form) : null);
    }, { once: true });
  });
}

// ------------------------------------------------------------------ cliente

let catalogo = [];

async function carregarCatalogo() {
  const alvo = $("#catalogo");
  alvo.replaceChildren(el("p", { class: "vazio" }, "Carregando…"));
  try {
    catalogo = (await api("GET", "/livros")).livros;
    desenharCatalogo();
  } catch (e) {
    alvo.replaceChildren(el("p", { class: "vazio" }, e.message));
  }
}

function desenharCatalogo() {
  const termo = $("#busca").value.trim().toLowerCase();
  const visiveis = catalogo.filter((l) =>
    !termo || l.titulo.toLowerCase().includes(termo) || (l.autor || "").toLowerCase().includes(termo));

  $("#catalogo").replaceChildren(...(visiveis.length ? visiveis.map(cartaoLivro)
    : [el("p", { class: "vazio" }, "Nenhum livro encontrado.")]));
}

function cartaoLivro(livro) {
  const etiqueta = livro.disponivel
    ? el("span", { class: "etiqueta ok" }, livro.quantidade === 1 ? "1 exemplar" : `${livro.quantidade} exemplares`)
    : el("span", { class: "etiqueta falta" }, "Em falta — avisamos quando chegar");
  return el("article", { class: "livro" },
    etiqueta,
    el("h3", {}, livro.titulo),
    el("span", { class: "autor" }, livro.autor),
    el("span", { class: "conservacao" }, `Conservação: ${livro.conservacao || "—"}`),
    el("div", { class: "rodape" },
      el("span", { class: "preco" }, reais(livro.preco)),
      el("button", { class: "botao pequeno", onclick: () => pedirLivro(livro) }, "Tenho interesse")));
}

async function pedirLivro(livro) {
  $("#interesse-livro").textContent = `${livro.titulo} — ${livro.autor}`;
  const dados = await abrirDialogo($("#dialogo-interesse"));
  if (!dados) return;
  try {
    const r = await api("POST", "/interesses", { livroId: livro.livroId, email: dados.email, nome: dados.nome || undefined });
    notificar(r.mensagem, r.status === "RESERVADO" ? "sucesso" : "alerta");
  } catch (e) {
    notificar(e.message, "erro");
  }
}

// ------------------------------------------------------------------ colaborador

async function carregarEstoque() {
  const corpo = $("#tabela-estoque");
  try {
    const { livros } = await api("GET", "/livros");
    catalogo = livros;
    corpo.replaceChildren(...livros.map(linhaEstoque));
    const seletor = $("#venda-balcao-livro");
    seletor.replaceChildren(...livros.map((livro) =>
      el("option", { value: livro.livroId, disabled: livro.quantidade < 1 ? "" : null },
        `${livro.titulo} (${livro.quantidade} disponível${livro.quantidade === 1 ? "" : "is"})`)));
    seletor.disabled = !livros.some((livro) => livro.quantidade > 0);
    $("#abrir-venda-balcao").disabled = seletor.disabled;
  } catch (e) {
    corpo.replaceChildren(el("tr", {}, el("td", { colspan: 5, class: "vazio" }, e.message)));
  }
}

function linhaEstoque(livro) {
  const qtd = el("input", { type: "number", min: "1", step: "1", value: "1", "aria-label": "Exemplares que chegaram" });
  return el("tr", {},
    el("td", {}, livro.titulo, el("span", { class: "sub" }, `${livro.autor} · ${livro.livroId}`)),
    el("td", {}, reais(livro.preco)),
    el("td", { class: "num" }, livro.quantidade),
    el("td", {}, el("div", { class: "chegada" }, qtd,
      el("button", { class: "botao pequeno secundario", onclick: () => registrarChegada(livro, qtd) }, "Chegaram"))),
    el("td", {}, el("div", { class: "acoes-linha" },
      el("button", { class: "botao pequeno secundario", onclick: () => verInteressados(livro) }, "Interessados"),
      el("span", { class: "sutil" }, "Venda no cartão abaixo"))));
}

async function registrarChegada(livro, campo) {
  const quantidade = parseInt(campo.value, 10);
  if (!(quantidade >= 1)) return notificar("Informe uma quantidade maior que zero.", "erro");
  try {
    const r = await api("PUT", `/livros/${encodeURIComponent(livro.livroId)}/estoque`, { quantidade });
    notificar(`"${livro.titulo}": estoque agora é ${r.quantidade}. Clientes aguardando serão avisados por e-mail.`, "sucesso");
    carregarEstoque();
  } catch (e) {
    notificar(e.message, "erro");
  }
}

async function venderBalcao(livro) {
  $("#venda-livro").textContent = `${livro.titulo} — ${reais(livro.preco)} (${livro.quantidade} em estoque)`;
  const dialogo = $("#dialogo-venda");
  const promessa = abrirDialogo(dialogo);
  dialogo.querySelector("[name=valor]").value = Number(livro.preco).toFixed(2);
  const dados = await promessa;
  if (!dados) return;
  const corpo = { livroId: livro.livroId };
  if (dados.valor) corpo.valor = Number(dados.valor);
  if (dados.colaborador) corpo.colaborador = dados.colaborador;
  try {
    const r = await api("POST", "/vendas", corpo);
    notificar(`Venda registrada: "${r.titulo}" por ${reais(r.valor)}. Restam ${r.estoqueRestante}.`, "sucesso");
    carregarEstoque();
    carregarVendas();
  } catch (e) {
    notificar(e.message, "erro");
  }
}

async function confirmarVendaReserva(livro, interesse) {
  const dialogo = $("#dialogo-venda-reserva");
  $("#venda-reserva-livro").textContent = `${livro.titulo} — ${reais(livro.preco)}`;
  const promessa = abrirDialogo(dialogo);
  $("#venda-reserva-email").value = interesse.email;
  dialogo.querySelector("[name=valor]").value = Number(livro.preco).toFixed(2);
  const dados = await promessa;
  if (!dados) return;
  if (!window.confirm(`Confirmar a venda reservada de "${livro.titulo}" para ${interesse.email}?`)) return;
  const corpo = {
    livroId: livro.livroId, email: interesse.email, reservaId: interesse.reservaId,
    valor: Number(dados.valor), colaborador: dados.colaborador || undefined,
  };
  try {
    const r = await api("POST", "/vendas", corpo);
    notificar(`Venda da reserva registrada: "${r.titulo}".`, "sucesso");
    dialogo.close();
    $("#dialogo-interessados").close();
    carregarEstoque();
    carregarVendas();
  } catch (e) {
    notificar(e.message, "erro");
  }
}

const ROTULO_STATUS = {
  AGUARDANDO: "aguardando", RESERVADO: "reservado", VENDIDO: "comprou",
  CANCELADO: "cancelado",
};

async function verInteressados(livro) {
  $("#interessados-livro").textContent = livro.titulo;
  const lista = $("#lista-interessados");
  lista.replaceChildren(el("li", {}, "Carregando…"));
  $("#dialogo-interessados").showModal();
  try {
    const { interesses } = await api("GET", `/livros/${encodeURIComponent(livro.livroId)}/interesses`);
    if (!interesses.length) {
      lista.replaceChildren(el("li", { class: "sutil" }, "Ninguém registrou interesse ainda."));
      return;
    }
    const linhas = interesses.map((i) => {
      const filhos = [
        el("span", {}, i.nome ? `${i.nome} <${i.email}>` : i.email),
        el("span", { class: "sutil" }, `${ROTULO_STATUS[i.status] || i.status} · ${dataHora(i.criadoEm)}`),
      ];
      if (i.status === "RESERVADO") {
        filhos.push(el("button", {
          class: "botao pequeno",
          onclick: () => confirmarVendaReserva(livro, i),
        }, "Confirmar venda"));
        if (i.notificacaoStatus !== "ENVIADA") {
          filhos.push(el("button", {
            class: "botao pequeno secundario",
            onclick: () => reenviarNotificacao(livro, i),
          }, "Reenviar aviso"));
        }

        filhos.push(el("button", {
          class: "botao pequeno perigo",
          onclick: () => cancelarReserva(livro, i),
        }, "Cancelar reserva"));
      }
      return el("li", {}, ...filhos);
    });
    lista.replaceChildren(...linhas);
  } catch (e) {
    lista.replaceChildren(el("li", {}, e.message));
  }
}

async function reenviarNotificacao(livro, interesse) {
  try {
    const r = await api("POST", "/notificacoes/reprocessar", {
      livroId: livro.livroId,
      email: interesse.email,
    });
    notificar(r.reenviada ? `Aviso reenviado para ${interesse.email}.` : "O aviso já foi publicado.", "sucesso");
    verInteressados(livro);
  } catch (e) {
    notificar(e.message, "erro");
  }
}

async function cancelarReserva(livro, interesse) {
  if (!window.confirm(`Cancelar a reserva de ${interesse.email} para "${livro.titulo}"?`)) return;
  try {
    const r = await api("POST", "/reservas/cancelar", {
      livroId: livro.livroId,
      email: interesse.email,
      reservaId: interesse.reservaId,
    });
    notificar(`Reserva cancelada. ${r.proximaReserva ? "A unidade foi reservada para o próximo cliente." : "Não há outro cliente aguardando."}`, "sucesso");
    $("#dialogo-interessados").close();
    carregarEstoque();
  } catch (e) {
    notificar(e.message, "erro");
  }
}

async function carregarVendas() {
  const corpo = $("#tabela-vendas");
  try {
    const r = await api("GET", "/vendas");
    $("#total-vendas").textContent = `${r.quantidade} venda(s) · ${reais(r.total)}`;
    corpo.replaceChildren(...(r.vendas.length ? r.vendas.map((v) => el("tr", {},
      el("td", {}, dataHora(v.dataVenda)),
      el("td", {}, v.titulo),
      el("td", {}, v.email || "—"),
      el("td", { class: "num" }, reais(v.valor))))
      : [el("tr", {}, el("td", { colspan: 4, class: "vazio" }, "Nenhuma venda registrada."))]));
  } catch (e) {
    corpo.replaceChildren(el("tr", {}, el("td", { colspan: 4, class: "vazio" }, e.message)));
  }
}

async function cadastrarLivro(evento) {
  evento.preventDefault();
  const form = evento.target;
  const d = dadosDoFormulario(form);
  try {
    const livro = await api("POST", "/livros", {
      titulo: d.titulo, autor: d.autor, preco: Number(d.preco),
      quantidade: parseInt(d.quantidade, 10) || 0, conservacao: d.conservacao || undefined,
    });
    notificar(`"${livro.titulo}" cadastrado com id ${livro.livroId}.`, "sucesso");
    form.reset();
    carregarEstoque();
  } catch (e) {
    notificar(e.message, "erro");
  }
}

// ------------------------------------------------------------------ navegação e inicialização

function mostrarAba(nome) {
  document.querySelectorAll(".aba").forEach((b) => {
    const ativa = b.dataset.aba === nome;
    b.classList.toggle("ativa", ativa);
    b.setAttribute("aria-selected", ativa);
  });
  $("#aba-cliente").hidden = nome !== "cliente";
  $("#aba-colaborador").hidden = nome !== "colaborador";
  if (nome === "cliente") carregarCatalogo();
  else { carregarEstoque(); carregarVendas(); }
}

function verificarConfiguracao() {
  const aviso = $("#aviso");
  if (urlDaApi()) { aviso.hidden = true; return; }
  aviso.textContent = "Configuração ausente: defina API_GATEWAY_URL no arquivo .env e execute python src/scripts/preparar_frontend.py.";
  aviso.hidden = false;
}

document.querySelectorAll(".aba").forEach((b) => b.addEventListener("click", () => mostrarAba(b.dataset.aba)));
document.querySelectorAll("[data-fechar]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close("cancelar")));
$("#abrir-venda-balcao").addEventListener("click", () => {
  const livro = catalogo.find((item) => item.livroId === $("#venda-balcao-livro").value);
  if (livro) venderBalcao(livro);
});
$("#busca").addEventListener("input", desenharCatalogo);
$("#recarregar-estoque").addEventListener("click", () => { carregarEstoque(); carregarVendas(); });
$("#form-cadastro").addEventListener("submit", cadastrarLivro);

verificarConfiguracao();
mostrarAba("cliente");
