# Cronograma MoldeTerm

Programa local (Python + HTML, sem framework) que monta o cronograma de produção da MoldeTerm
em quatro níveis — **HM → item → setor → grade hora a hora** — e compara o programado com as
horas realizadas exportadas do ERP.

Roda 100% offline: um servidor HTTP na `127.0.0.1` serve uma página única. Nenhum dado sai da
máquina. Só dependência externa: `openpyxl` (leitura de .xlsx).

```
python cronograma.py            # abre em http://127.0.0.1:8777
python cronograma.py --porta 8080 --sem-navegador
python importar_hms.py          # extrai itens e horas das planilhas de HM
python planejar.py "HM 060" 2026-12-15   # simula uma HM nova pela linha de comando
```

---

## Arquitetura

| Arquivo | Responsabilidade |
|---|---|
| `cronograma.py` | Servidor HTTP e rotas. Não contém regra de negócio. |
| `mestre.py` | **Núcleo.** Lê o cronograma mestre + horas dos itens, aplica os fatores, distribui as horas no tempo e monta a grade hora a hora. |
| `importar_hms.py` | Lê as planilhas "HM JOI" (modelo Tupy) e gera `pedidos/horas_itens.csv` e `pedidos/hms_cabecalho.csv`. |
| `apontamentos.py` | Lê a planilha de apontamentos do ERP e casa cada lançamento com uma linha do cronograma. |
| `planejar.py` | Encaixa uma HM nova na capacidade que sobra e grava as linhas no cronograma mestre. Também encaixa **um setor solto** numa HM que já está programada (`planejar_setor_faltante()`), para resolver as pendências `SETOR SEM JANELA`. |
| `motor.py` | Motor de capacidade finita por recurso individual (pessoa + máquina). Usado pela simulação de carteira por matriz de complexidade; **não** é o caminho principal hoje. Também exporta utilitários (`Calendario`, `ler`, `norm`, `num`) usados pelos outros módulos. |
| `web/index.html` | Toda a interface: HTML, CSS e JS em um arquivo, sem build. |
| `gerar_modelo_apontamentos.py` | Gera o modelo de planilha do ERP e um arquivo de exemplo. |

### Rotas

| Rota | O que faz |
|---|---|
| `GET /` | Serve `web/index.html` |
| `GET /api/dados` | Cronograma completo + apontamentos casados |
| `GET /api/novas` | HMs com planilha importada e se já estão no cronograma |
| `GET /api/simulacao` | Saída do `motor.py` (carteira por matriz de complexidade) |
| `GET /modelo` | Baixa `dados/MODELO_apontamentos.xlsx` |
| `POST /api/pasta` | Salva em `config.json` a pasta onde o ERP deixa o export |
| `POST /api/planejar` | Calcula as janelas de uma HM nova (não grava) |
| `POST /api/gravar` | Calcula e grava as linhas no `cronograma_mestre.xlsx`, com backup |
| `POST /api/planejar-setor` | Calcula a janela de um setor que ficou sem linha numa HM já programada (não grava) |
| `POST /api/gravar-setor` | Recalcula e grava essa linha única no `cronograma_mestre.xlsx`, com backup |

---

## Fluxo dos dados

```
pedidos/hms/*.xlsx  ──importar_hms.py──▶  pedidos/horas_itens.csv      (itens e horas por processo)
                                          pedidos/hms_cabecalho.csv    (cliente, PN, datas TARGET)

pedidos/cronograma_mestre.xlsx  ─┐
base/*.csv                       ├──mestre.py──▶  atividades (HM|item|setor) com grade hora a hora
pedidos/horas_itens.csv         ─┘

<pasta do ERP>/apontamentos.xlsx ──apontamentos.py──▶  realizado por atividade + grade vermelha
```

**Quem manda em quê:** as **datas e a estrutura de setores** vêm do `cronograma_mestre.xlsx`;
as **horas e os itens** vêm das planilhas de HM. Quando um setor não tem horas na planilha da HM,
a linha segue com as horas do cronograma e é marcada `fonte_horas = "CRONOGRAMA"`.

---

## Regras de negócio que não podem ser quebradas

1. **Nada é estimado.** Dado que falta vira pendência (`pendencias[]`), nunca um número inventado.
   Toda suposição vai para `premissas[]` e aparece na aba Pendências.
2. **A unidade cadastrada é respeitada.** Etapa em dias ocupa dias de calendário e nunca é
   convertida em horas nos relatórios.
3. **Hora extra não é usada para caber no prazo.** Quando não cabe, mostra-se o gargalo e as horas
   que faltaram — a data não é forçada.
4. **Recurso contingencial fica fora da capacidade base** (Amaral, bancada).
5. **Coluna `origem` em todo cadastro de `base/`**: `CADASTRADO` veio da base mestre do projeto;
   `PREMISSA` foi assumido e precisa de confirmação do Jonas.
6. **Fatores nas horas**: `calibracao_pct` (20%, regra das premissas do cronograma) × 
   `margem_seguranca_pct` (6%) = **1,272** — confirmado pelo Jonas em 14/09/2026: a margem **soma**
   à calibração, não substitui. Ficam em `base/parametros.csv` e são aplicados em um lugar só, no
   `mestre.py`. A HM 059 confirma o 1,20: 214,2 h de CAD × 1,2 = 257 h, exatamente o que está no
   cronograma mestre.
7. **Setor externo não consome capacidade.** `mestre.SETORES_EXTERNOS` (hoje só `TERCEIROS`, que é
   onde a eletroerosão está mapeada) fica fora da grade e da carga por setor, mas as horas **não
   somem**: vão para a lista `terceiros` e aparecem na aba Pendências. Nunca apague hora de
   serviço externo — ela é registro, não estimativa.

---

## Cadastros (`base/`)

| Arquivo | Conteúdo |
|---|---|
| `calendarios.csv` | Comercial 07:00–16:48, 1º turno 04:45–14:03, 2º turno 14:03–23:10, contingencial |
| `capacidade_setor.csv` | h/dia de cada setor e em quais calendários ele trabalha |
| `roteiro_setores.csv` | Sequência e liberação parcial: CAM abre com 40% do CAD, CNC com 40% do CAM, bancada com 50% da usinagem, digitalização com 60% da bancada |
| `de_para_processos.csv` | Processo da planilha da HM → setor do cronograma |
| `de_para_erp_setor.csv` | Processo do ERP → setor do cronograma |
| `feriados.csv` | Feriados excluídos do cálculo |
| `parametros.csv` | Fatores de calibração e margem |
| `recursos.csv`, `maquinas.csv`, `habilidades.csv`, `processos.csv`, `matriz_complexidade.csv`, `caixa_macho_matriz.csv`, `roteiro_processos.csv` | Base do `motor.py` (capacidade por pessoa e máquina, matriz C1–C5) |

---

## Convenções de código

- **Português** em nomes de função, variável, comentário e mensagem ao usuário. Sem acento em
  identificadores; com acento no texto que aparece na tela.
- **Só biblioteca padrão**, exceto `openpyxl`. Nada de framework web, nada de build no front.
- **Leitura de CSV tolerante**: `motor.ler()` detecta separador (`;` ou `,`) e codificação
  (utf-8-sig, cp1252, latin-1). `motor.num()` aceita `1.234,50` e `1234.50`.
- **Normalização**: `norm()` tira acento e caixa antes de comparar qualquer texto vindo de planilha.
- **Casamento de nomes é tolerante de propósito** — o ERP escreve diferente do cronograma.
  HM casa pelo número (`024` em qualquer formato); item casa por substring (o ERP manda `CX02B`,
  o cronograma tem `Alteration - Caixa de Macho ... Nº CX02B`).
- **Nunca gravar por cima de arquivo do usuário sem backup** — ver `planejar.gravar_no_mestre()`.
- **Ao gravar no cronograma mestre**, escreva nas colunas pelo **cabeçalho** da aba
  (`planejar.colunas_do_mestre()`) e use o setor com a grafia que já está na planilha
  (`planejar.nome_setor_no_cronograma()`) — "Digitalizacao" e "Digitalização" viram dois setores
  distintos no filtro da tela.

---

## Ao mexer aqui

- Mudou a distribuição de horas no tempo? Confira que a soma de `por_dia` continua igual a
  `horas` da atividade, e que nada cai em fim de semana ou feriado.
- Mudou o front? Não existe build: edite `web/index.html` e recarregue a página.
- Os totais por setor têm de bater com a aba **Resumo** do `cronograma_mestre.xlsx`
  (Projeto/CAD 685,5 h · CAM 365,8 h · Usinagem 1.563,8 h · Solda 281,9 h · Bancada 628,7 h).
  A Usinagem ficou 10,2 h abaixo do que dava antes de 14/09/2026: são as 8 h base de eletroerosão
  da HM 024 (× 1,272) que passaram para Terceiros.

---

## Pendências abertas com o Jonas

1. **HM 057** — a planilha enviada está com a aba *Orçamento Hora* em branco nos dois itens.
   Enquanto não vier preenchida, a HM segue no cronograma sem quebra por item (pendência
   `PLANILHA SEM HORAS`).
2. **Digitalização da HM 051** — 52 h base na planilha da HM, sem linha no cronograma mestre. O
   encaixe automático já calcula a janela (21/09 a 30/09/2026), mas ela **fecha 1 dia depois da
   entrega de 29/09** — a data não foi forçada, falta o Jonas decidir (hora extra, terceirizar ou
   remanejar). A da HM 024 foi resolvida e gravada em 14/09/2026.
3. **Feriados divergentes** — a aba Resumo lista 3 feriados, a aba Premissas lista 5. Está usando 5.
4. **Perfil de distribuição** — hoje as horas são espalhadas por igual na janela. Se o consumo real
   for concentrado no começo ou no fim, o perfil precisa ser informado.
