# Cronograma MoldeTerm

Programa que roda no seu computador. Mostra o cronograma que está em execução em quatro
níveis, do macro ao hora a hora:

```
HM 024
  └─ Alteration - Caixa de Macho Hansberg Horizontal 100L Nº CX02B
       └─ Usinagem · Grebo/Feeler/Iru/Hartford/Torno
            └─ grade hora a hora, das 04h às 23h, dia por dia
```

Clique na HM e abrem os itens. Clique no item e abrem os setores alocados para ele.
Clique no setor e abre, logo abaixo, a grade hora a hora daquele setor naquele item.

---

## 1. Instalar (uma vez só)

Instale o Python: <https://www.python.org/downloads/> — marque **"Add Python to PATH"** na
primeira tela. Depois, no Prompt de Comando: `pip install openpyxl` (o programa lê .xlsx).

## 2. Usar

Duplo clique em **`INICIAR.bat`** (Windows) ou **`INICIAR.command`** (Mac). O navegador abre
no cronograma. Mudou algum arquivo? Clique em **"Recarregar"**.

---

## 3. De onde vem cada coisa

| O quê | De onde |
|---|---|
| HM, setor, janela de datas, prioridade, entrega | `pedidos/cronograma_mestre.xlsx` (abas Cronograma Project, Resumo e Premissas) |
| Itens de cada HM e horas por processo | `pedidos/hms/*.xlsx` — as planilhas "HM JOI ..." |
| Fatores aplicados nas horas | `base/parametros.csv` |
| Capacidade e turno de cada setor | `base/capacidade_setor.csv` + `base/calendarios.csv` |
| Feriados | `base/feriados.csv` |
| De-para dos processos da planilha para os setores | `base/de_para_processos.csv` |
| Realizado (opcional) | `dados/apontamentos.csv` exportado do ERP |

### Quando chegar uma HM nova

1. Jogue o `.xlsx` da HM na pasta **`pedidos/hms`**.
2. Rode `python importar_hms.py` (ou duplo clique no `IMPORTAR_HMS.bat`). Ele lê a aba
   *RFQ Summary* e a aba *Orçamento Hora*, extrai os itens e as horas por processo, e grava
   `pedidos/horas_itens.csv`.
3. Acrescente as linhas dessa HM no `pedidos/cronograma_mestre.xlsx` (setor, janela, prioridade).
4. Clique em "Recarregar" na tela.

O importador usa a coluna **"Quantidade Horas Pós Kickoff"**; quando ela está vazia, usa
**"Quantidade Horas Relatório Final"**. Material P20, Material 1045 e Acessórios **não** são
horas e ficam de fora. Bloco sem horas preenchidas vira aviso, nunca estimativa.

---

## 4. Os fatores aplicados nas horas

Ficam em `base/parametros.csv` e valem para **todas** as horas do cronograma:

| Parâmetro | Valor | De onde veio |
|---|---|---|
| `calibracao_pct` | 20% | Regra já registrada na aba Premissas do cronograma mestre ("horas calibradas = estimadas × 1,20") |
| `margem_seguranca_pct` | 6% | Margem de segurança que você pediu |

Fator final: **1,20 × 1,06 = 1,272** — você confirmou em 14/09/2026 que os 6% **somam** à
calibração de 20%, não substituem. A tela mostra o fator aplicado no cabeçalho, sempre.

---

## 5. As abas

**Cronograma** — os quatro níveis. A barra mostra as horas já com o fator. Linha vermelha
vertical = hoje; tracejado roxo = data de entrega da HM; colunas rosadas = feriados.
Barra azul = horas vindas da planilha da HM. Barra hachurada cinza = linha que seguiu com as
horas do cronograma, sem quebra por item. Barra vermelha = a janela é mais curta do que a
capacidade do setor permite.

**Carga por setor** — horas totais, capacidade por dia, pico diário e em que data ele acontece.

**Planilha da HM × cronograma** — compara, setor por setor, o que a planilha da HM pede com o
que o cronograma mestre reservou. É onde o desvio aparece.

**Resumo e premissas da planilha** — as abas Resumo e Premissas do cronograma mestre, mais os
parâmetros que o programa aplicou.

**Pendências** — o que falta definir ou está em conflito, e as premissas assumidas. Quando a
pendência é **SETOR SEM JANELA** (a planilha da HM tem horas de um setor que não tem linha no
cronograma), aparece o botão **Calcular janela**: o programa acha o início pela regra de liberação
parcial, encaixa as horas na capacidade que sobra e mostra se ainda cabe na entrega — e só grava
depois que você confirmar. Logo abaixo, o quadro **Horas de terceiros** lista o que é feito fora
da MoldeTerm (hoje a eletroerosão): as horas ficam registradas, mas não ocupam nenhum setor.

---

## 6. Como a grade hora a hora é montada

As horas de cada setor são espalhadas **por igual** nos dias úteis entre o início e o fim da
janela do cronograma (pulando fim de semana e feriado). Dentro do dia, são espalhadas nas horas
do turno daquele setor — comercial 07:00–16:48 para CAD, CAM, solda e bancada; 1º + 2º turno
04:45–23:10 para a usinagem CNC.

O número em cada célula é **quantos recursos do setor ficam ocupados naquela hora**. Passa de 1
porque o setor tem várias pessoas e máquinas: 2,5 na usinagem às 10h significa duas máquinas e
meia ocupadas em média naquela hora com aquele item.

Se o consumo real for concentrado no começo ou no fim da janela em vez de uniforme, me avise
que eu troco o perfil de distribuição.

---

## 7. Importar as horas realizadas do ERP

Cada linha do cronograma tem **duas barras**: a de cima, azul, é o programado; a de baixo,
vermelha, é o que foi realmente apontado. Enquanto não houver apontamento, fica um espaço
tracejado no lugar dela. O mesmo vale na grade hora a hora: uma grade azul (programado) e uma
vermelha (realizado), uma embaixo da outra.

### Configurar a pasta uma vez

Abra a aba **Apontamentos do ERP**. No campo de cima, cole o caminho da pasta onde você salva o
export — abra a pasta no Explorador do Windows, clique na barra de endereço, copie e cole aqui.
Clique em **Salvar pasta e importar**.

A partir daí, toda vez que você clicar em **Recarregar**, o programa pega sozinho o arquivo
**mais recente** dessa pasta cujo nome comece com `apontamentos`. Não precisa mexer em mais nada:
o ERP salva por cima, você recarrega, o cronograma atualiza.

Se deixar a pasta em branco, ele usa a pasta `dados` aqui de dentro do programa.

### Como a planilha deve vir

Clique em **Baixar modelo de planilha** na mesma aba e mande esse arquivo para quem gera o export
do ERP. As colunas:

| Coluna | O que é |
|---|---|
| `Data` | Dia do apontamento. **Obrigatória** |
| `Hora Inicial` | hh:mm — é ela que permite montar a grade hora a hora |
| `Hora Final` | hh:mm — com ela o programa calcula as horas sozinho |
| `HM` | Pode vir como HM 024, HM024, 2026-024 ou só 024 |
| `Item` | Pode ser só o código: CX02B, CX01A, MD01. Vazio = a hora entra no setor sem quebra por item |
| `Processo` | CNC GRANDE, AJUSTAGEM, CAM, PROJETOS, SOLDAGEM... **Obrigatória** |
| `Operador` | Opcional |
| `Horas` | Opcional se vierem hora inicial e final |

A ordem das colunas não importa e os nomes podem variar — o programa reconhece sinônimos (OS,
Ordem de Serviço, Colaborador, Tempo Realizado). O que ele não reconhecer, você ensina em
`mapeamento.json`. A tradução do processo do ERP para o setor do cronograma está em
`base/de_para_erp_setor.csv`.

A aba **Apontamentos do ERP** mostra, a cada importação: qual arquivo foi lido e quando, quantas
horas entraram, quais colunas ele reconheceu, quais processos não estão no de-para e quais
lançamentos não casaram com nenhuma linha do cronograma — com o motivo de cada um.

> O arquivo `dados/apontamentos_EXEMPLO.xlsx` que veio junto tem **horas fictícias**, só para você
> ver a tela funcionando. Apague quando tiver o export de verdade.

---

## 8. Colocar uma HM nova no cronograma

Três passos, e o programa calcula as datas sozinho.

**1.** Coloque o arquivo "HM JOI 2026-0XX" na pasta `pedidos/hms`.

**2.** Duplo clique em **`IMPORTAR_HMS.bat`**. Ele lê os itens, as horas por processo e também o
**TARGET Final** do RFQ Summary — a data de entrega já vem da própria planilha.

**3.** Abra a aba **Planejar HM nova**. A HM aparece na lista marcada como *fora do cronograma*.
Clique em **Planejar**, confira a data de entrega (já vem preenchida), escolha a prioridade e
clique em **Calcular as janelas**.

O programa então:

- pega as horas da planilha e aplica os fatores (×1,272);
- olha, dia a dia, **quanto sobra da capacidade de cada setor** depois do que já está no
  cronograma — as HMs que já estão lá não se mexem;
- respeita as regras de liberação parcial cadastradas em `base/roteiro_setores.csv`: CAM abre com
  40% do CAD liberado, CNC com 40% do CAM, bancada com 50% da usinagem, digitalização com 60% da
  bancada;
- devolve início e fim de cada setor, quanto ocupa por dia, e em que data a HM fecha.

Se couber no prazo, a tela mostra em verde a folga em dias e qual setor define a data. Se não
couber, mostra em vermelho quantos dias faltaram e qual setor segurou — **sem forçar a data**. Aí
a decisão de hora extra, troca de recurso, terceirização ou mudança de prioridade é sua.

Conferido o resultado, clique em **Gravar esta HM no cronograma**. Ele acrescenta as linhas na
planilha `cronograma_mestre.xlsx` e guarda uma cópia da versão anterior em `pedidos/backup`.

> Para um **dispositivo ou peça avulsa**, que não tem planilha Tupy, acrescente as linhas direto no
> `cronograma_mestre.xlsx` (Projeto, Setor, Recurso, Horas, Início, Fim, Prioridade) e recarregue.
> Ele entra no cronograma e consome capacidade, mas sem quebra por item.
