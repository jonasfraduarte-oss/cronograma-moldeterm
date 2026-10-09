"""
MOTOR DE CRONOGRAMA POR CAPACIDADE FINITA - MoldeTerm
=====================================================
Le os cadastros da pasta base/ e a carteira da pasta pedidos/, e gera o
cronograma respeitando:
  - sequencia tecnologica e predecessoras (base/roteiro_processos.csv)
  - calendarios e turnos reais (base/calendarios.csv)
  - capacidade finita de PESSOAS (base/recursos.csv)
  - capacidade finita de MAQUINAS, que nao e a mesma da pessoa (base/maquinas.csv)
  - competencia: so aloca quem esta habilitado (base/habilidades.csv)
  - prioridade dos projetos e data de entrega (pedidos/carteira.csv)

Regras que o motor NAO quebra:
  - unidade cadastrada e respeitada: etapa em dias ocupa DIAS de calendario e
    nunca e convertida em horas nos relatorios;
  - hora extra nao e usada para caber no prazo - o atraso e mostrado como gargalo;
  - recurso contingencial (Amaral) fica fora da capacidade base;
  - nada e agendado sem horas cadastradas: falta vira pendencia, nao estimativa.
"""

import csv
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
BASE = RAIZ / "base"
PEDIDOS = RAIZ / "pedidos"

HORIZONTE_DIAS = 900


# ------------------------------------------------------------------ utilidades
def norm(t):
    if t is None:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def num(v, padrao=None):
    if v is None or str(v).strip() == "":
        return padrao
    t = str(v).strip().replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return padrao


def data_br(v):
    if v is None or str(v).strip() == "":
        return None
    t = str(v).strip().split(" ")[0]
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            pass
    return None


def ler(arquivo):
    """Le um CSV da base (separador ; e acentos tolerados)."""
    caminho = arquivo if arquivo.exists() else None
    if caminho is None:
        return []
    dados = caminho.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = dados.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        texto = dados.decode("latin-1", errors="replace")
    sep = ";" if texto[:2000].count(";") >= texto[:2000].count(",") else ","
    linhas = list(csv.DictReader(texto.splitlines(), delimiter=sep))
    saida = []
    for l in linhas:
        if any((v or "").strip() for v in l.values()):
            saida.append({(k or "").strip(): (v or "").strip() for k, v in l.items()})
    return saida


# ------------------------------------------------------------------ calendario
class Calendario:
    def __init__(self, nome, ini, fim, int_ini, int_fim, feriados):
        self.nome = nome
        self.blocos_dia = []
        h = lambda s: time(int(s[:2]), int(s[3:5]))
        if int_ini and int_fim:
            self.blocos_dia = [(h(ini), h(int_ini)), (h(int_fim), h(fim))]
        else:
            self.blocos_dia = [(h(ini), h(fim))]
        self.feriados = feriados

    def horas_dia(self):
        return sum((datetime.combine(date(2000, 1, 1), b) - datetime.combine(date(2000, 1, 1), a)).seconds
                   for a, b in self.blocos_dia) / 3600

    def util(self, d):
        return d.weekday() < 5 and d not in self.feriados

    def intervalos(self, d):
        if not self.util(d):
            return []
        return [(datetime.combine(d, a), datetime.combine(d, b)) for a, b in self.blocos_dia]

    def proximo_dia_util(self, d):
        while not self.util(d):
            d += timedelta(days=1)
        return d


# ------------------------------------------------------------------ agenda
class Agenda:
    """Ocupacao de um recurso (pessoa) ou de uma maquina."""

    def __init__(self, nome):
        self.nome = nome
        self.ocupado = []  # lista de (inicio, fim) ordenada

    def livre(self, ini, fim):
        for a, b in self.ocupado:
            if a < fim and ini < b:
                return False
        return True

    def reservar(self, blocos):
        self.ocupado.extend(blocos)
        self.ocupado.sort()

    def horas_ocupadas(self):
        return sum((b - a).total_seconds() for a, b in self.ocupado) / 3600

    def vagos(self, ini, fim):
        """Sub-intervalos livres dentro de (ini, fim)."""
        livres, cursor = [], ini
        for a, b in self.ocupado:
            if b <= ini or a >= fim:
                continue
            if a > cursor:
                livres.append((cursor, min(a, fim)))
            cursor = max(cursor, b)
            if cursor >= fim:
                break
        if cursor < fim:
            livres.append((cursor, fim))
        return [(a, b) for a, b in livres if b > a]


def interseccao(lista_a, lista_b):
    saida = []
    for a1, a2 in lista_a:
        for b1, b2 in lista_b:
            i, f = max(a1, b1), min(a2, b2)
            if f > i:
                saida.append((i, f))
    return sorted(saida)


def alocar_horas(horas, nao_antes, calendario, agenda_pessoa, agenda_maquina=None):
    """Distribui `horas` na primeira janela livre da pessoa (e da maquina, se houver)."""
    restante = timedelta(hours=horas)
    blocos, d = [], nao_antes.date()
    for _ in range(HORIZONTE_DIAS):
        for ini, fim in calendario.intervalos(d):
            ini = max(ini, nao_antes)
            if fim <= ini:
                continue
            livres = agenda_pessoa.vagos(ini, fim)
            if agenda_maquina is not None:
                livres = interseccao(livres, agenda_maquina.vagos(ini, fim))
            for a, b in livres:
                if restante <= timedelta(0):
                    break
                usa = min(restante, b - a)
                blocos.append((a, a + usa))
                restante -= usa
            if restante <= timedelta(0):
                break
        if restante <= timedelta(0):
            break
        d += timedelta(days=1)
    if restante > timedelta(0):
        return None
    return blocos


def alocar_dias(dias, nao_antes, calendario, agenda_pessoa, agenda_maquina=None):
    """Ocupa `dias` dias UTEIS inteiros do calendario (unidade em dias, sem converter em horas)."""
    blocos, usados, d = [], 0, calendario.proximo_dia_util(nao_antes.date())
    for _ in range(HORIZONTE_DIAS):
        if usados >= dias:
            break
        ints = calendario.intervalos(d)
        if ints:
            ini_dia = max(ints[0][0], nao_antes)
            candidatos = [(max(a, ini_dia), b) for a, b in ints if b > max(a, ini_dia)]
            ok = all(agenda_pessoa.livre(a, b) for a, b in candidatos)
            if agenda_maquina is not None:
                ok = ok and all(agenda_maquina.livre(a, b) for a, b in candidatos)
            if ok and candidatos:
                blocos.extend(candidatos)
                usados += 1
        d += timedelta(days=1)
    if usados < dias:
        return None
    return blocos


def dias_corridos_uteis(dias, nao_antes, calendario):
    """Duracao de espera externa (terceiros): passa o tempo sem consumir capacidade."""
    d, contados, ini = calendario.proximo_dia_util(nao_antes.date()), 0, None
    fim = nao_antes
    for _ in range(HORIZONTE_DIAS):
        if contados >= dias:
            break
        if calendario.util(d):
            ints = calendario.intervalos(d)
            if ini is None:
                ini = max(ints[0][0], nao_antes)
            fim = ints[-1][1]
            contados += 1
        d += timedelta(days=1)
    return [(ini or nao_antes, fim)]


# ------------------------------------------------------------------ carga
class Base:
    def __init__(self):
        feriados = {data_br(r.get("data")) for r in ler(BASE / "feriados.csv")}
        feriados.discard(None)
        self.feriados = feriados

        self.calendarios = {}
        for r in ler(BASE / "calendarios.csv"):
            self.calendarios[norm(r["calendario"])] = Calendario(
                r["calendario"], r["inicio"], r["fim"],
                r.get("intervalo_inicio"), r.get("intervalo_fim"), feriados)

        self.recursos = {}
        for r in ler(BASE / "recursos.csv"):
            cal = self.calendarios.get(norm(r.get("calendario")))
            if not cal:
                continue
            self.recursos[norm(r["recurso"])] = {
                "nome": r["recurso"], "setor": r.get("setor", ""), "funcao": r.get("funcao", ""),
                "calendario": cal, "maquina": norm(r.get("maquina")) or None,
                "capacidade_mes": num(r.get("capacidade_mensal_h"), 0),
                "tipo": (r.get("tipo") or "BASE").upper(),
                "agenda": Agenda(r["recurso"]),
            }

        self.maquinas = {}
        for r in ler(BASE / "maquinas.csv"):
            self.maquinas[norm(r["maquina"])] = {
                "nome": r["maquina"], "setor": r.get("setor", ""),
                "cap_dia": num(r.get("capacidade_dia_h"), 0),
                "cap_mes": num(r.get("capacidade_mes_h"), 0),
                "agenda": Agenda(r["maquina"]),
            }

        self.roteiro = {}
        for r in ler(BASE / "roteiro_processos.csv"):
            self.roteiro[norm(r["processo"])] = {
                "seq": int(num(r.get("seq"), 999)),
                "processo": r["processo"], "etapa": r.get("etapa_macro", ""),
                "setor": r.get("setor", ""), "unidade": (r.get("unidade_padrao") or "horas").lower(),
                "consome": (r.get("consome_capacidade") or "SIM").upper() == "SIM",
                "sobreposicao": num(r.get("sobreposicao_pct"), 0) or 0,
                "origem": r.get("origem", ""), "obs": r.get("observacao", ""),
            }

        self.habilidades = {}
        for r in ler(BASE / "habilidades.csv"):
            p = norm(r["processo"])
            self.habilidades.setdefault(p, []).append({
                "recurso": norm(r["recurso"]), "maquina": norm(r.get("maquina")) or None,
                "origem": r.get("origem", ""), "obs": r.get("observacao", ""),
            })

        self.matriz = {}
        for r in ler(BASE / "matriz_complexidade.csv"):
            chave = (norm(r["familia"]), norm(r["complexidade"]))
            self.matriz.setdefault(chave, []).append({
                "etapa": r["etapa"], "processo": norm(r["processo"]),
                "valor": num(r["valor"]), "unidade": r["unidade"].lower(),
            })

        self.processos = {norm(r["processo"]): r for r in ler(BASE / "processos.csv")}
        self.carteira = ler(PEDIDOS / "carteira.csv")
        self.horas_orc = ler(PEDIDOS / "horas_orcamento.csv")


# ------------------------------------------------------------------ atividades
def montar_atividades(b):
    """Gera a lista de atividades de cada conjunto, a partir do orcamento
    (prevalece) ou da matriz de complexidade (padrao)."""
    atividades, pendencias = [], []

    orc = {}
    for r in b.horas_orc:
        orc.setdefault((r["projeto"], r["conjunto"]), []).append(r)

    for linha in b.carteira:
        proj, conj = linha["projeto"], linha["conjunto"]
        familia, complex_ = norm(linha.get("familia")), norm(linha.get("complexidade"))
        itens, fonte = [], ""

        if (proj, conj) in orc:
            fonte = "ORCAMENTO"
            for r in orc[(proj, conj)]:
                p = norm(r["processo"])
                valor, unidade = num(r.get("valor")), (r.get("unidade") or "").lower()
                if valor is None or unidade not in ("horas", "dias"):
                    pendencias.append({
                        "tipo": "HORA SEM UNIDADE", "projeto": proj, "conjunto": conj,
                        "processo": r["processo"],
                        "detalhe": r.get("observacao") or "Valor sem unidade de hora/dia no orcamento - atividade nao agendada",
                    })
                    continue
                itens.append({"processo": p, "valor": valor, "unidade": unidade,
                              "etapa": b.roteiro.get(p, {}).get("etapa", ""), "fonte": fonte,
                              "obs": r.get("observacao", "")})
        elif (familia, complex_) in b.matriz:
            fonte = "MATRIZ"
            for m in b.matriz[(familia, complex_)]:
                itens.append({"processo": m["processo"], "valor": m["valor"], "unidade": m["unidade"],
                              "etapa": m["etapa"], "fonte": fonte, "obs": ""})
        else:
            pendencias.append({
                "tipo": "SEM HORAS", "projeto": proj, "conjunto": conj, "processo": "-",
                "detalhe": f"Sem horas de orcamento e sem matriz para familia/complexidade "
                           f"({linha.get('familia')} / {linha.get('complexidade')})",
            })
            continue

        itens = [i for i in itens if b.roteiro.get(i["processo"], {}).get("seq", 999) < 900]
        itens.sort(key=lambda i: b.roteiro.get(i["processo"], {}).get("seq", 999))

        anterior = None
        for i in itens:
            rot = b.roteiro.get(i["processo"], {})
            at = {
                "id": f"{proj}|{conj}|{i['processo']}",
                "projeto": proj, "cliente": linha.get("cliente", ""), "conjunto": conj,
                "tipo_conjunto": linha.get("tipo", ""),
                "processo": i["processo"], "etapa": i["etapa"] or rot.get("etapa", ""),
                "setor": rot.get("setor", ""), "unidade": i["unidade"],
                "valor": i["valor"], "consome": rot.get("consome", True),
                "fonte_horas": i["fonte"],
                "prioridade": int(num(linha.get("prioridade"), 9)),
                "liberacao": data_br(linha.get("data_liberacao")),
                "entrega": data_br(linha.get("data_entrega")),
                "predecessora": anterior, "obs": i["obs"], "seq": rot.get("seq", 999),
            }
            atividades.append(at)
            anterior = at["id"]

    return atividades, pendencias


# ------------------------------------------------------------------ agendamento
def agendar(b, atividades, inicio_planejamento):
    por_id = {a["id"]: a for a in atividades}
    feitas, resultado, gargalos = set(), [], []
    pendentes = list(atividades)
    cal_padrao = b.calendarios.get("COMERCIAL")

    while pendentes:
        prontas = [a for a in pendentes if not a["predecessora"] or a["predecessora"] in feitas]
        if not prontas:
            break
        prontas.sort(key=lambda a: (a["prioridade"], a["entrega"] or date(2099, 1, 1), a["seq"]))
        at = prontas[0]

        base_dt = datetime.combine(at["liberacao"] or inicio_planejamento, time(0, 0))
        if base_dt < datetime.combine(inicio_planejamento, time(0, 0)):
            base_dt = datetime.combine(inicio_planejamento, time(0, 0))
        if at["predecessora"]:
            base_dt = max(base_dt, por_id[at["predecessora"]]["_fim"])

        escolhido = None
        if not at["consome"]:
            # servico externo: consome prazo, nao consome capacidade interna
            blocos = dias_corridos_uteis(at["valor"], base_dt, cal_padrao)
            escolhido = {"recurso": "TERCEIROS", "maquina": "", "blocos": blocos,
                         "setor": at["setor"], "origem_recurso": "CADASTRADO"}
        else:
            candidatos = b.habilidades.get(at["processo"], [])
            melhor = None
            for c in candidatos:
                rec = b.recursos.get(c["recurso"])
                if not rec or rec["tipo"] == "CONTINGENCIAL":
                    continue  # contingencial nao entra na capacidade base
                maq = b.maquinas.get(c["maquina"] or rec["maquina"] or "")
                ag_maq = maq["agenda"] if maq else None
                if at["unidade"] == "dias":
                    blocos = alocar_dias(at["valor"], base_dt, rec["calendario"], rec["agenda"], ag_maq)
                else:
                    blocos = alocar_horas(at["valor"], base_dt, rec["calendario"], rec["agenda"], ag_maq)
                if not blocos:
                    continue
                if melhor is None or blocos[-1][1] < melhor["blocos"][-1][1]:
                    melhor = {"recurso": rec["nome"], "maquina": maq["nome"] if maq else "",
                              "blocos": blocos, "setor": rec["setor"],
                              "origem_recurso": c["origem"], "obj_rec": rec, "obj_maq": maq}
            escolhido = melhor

        if escolhido is None:
            resultado.append({**at, "_erro": "Sem recurso habilitado com capacidade disponivel"})
            pendentes.remove(at)
            feitas.add(at["id"])
            at["_fim"] = base_dt
            continue

        if escolhido.get("obj_rec"):
            escolhido["obj_rec"]["agenda"].reservar(escolhido["blocos"])
        if escolhido.get("obj_maq"):
            escolhido["obj_maq"]["agenda"].reservar(escolhido["blocos"])

        ini, fim = escolhido["blocos"][0][0], escolhido["blocos"][-1][1]
        espera_h = 0.0
        if ini > base_dt and escolhido.get("obj_rec"):
            cal = escolhido["obj_rec"]["calendario"]
            d, e = base_dt.date(), 0.0
            while d < ini.date():
                for a_, b_ in cal.intervalos(d):
                    ini_ = max(a_, base_dt)
                    if b_ > ini_:
                        e += (b_ - ini_).total_seconds() / 3600
                d += timedelta(days=1)
            espera_h = round(e, 1)
            if espera_h > 0.5:
                gargalos.append({"processo": at["processo"], "recurso": escolhido["recurso"],
                                 "maquina": escolhido["maquina"], "setor": escolhido["setor"],
                                 "projeto": at["projeto"], "conjunto": at["conjunto"],
                                 "espera_h": espera_h})

        at["_fim"] = fim
        horas_cal = sum((b_ - a_).total_seconds() for a_, b_ in escolhido["blocos"]) / 3600
        dias_uteis = len({b_.date() for a_, b_ in escolhido["blocos"]})
        resultado.append({**at,
                          "recurso": escolhido["recurso"], "maquina": escolhido["maquina"],
                          "setor": escolhido["setor"], "origem_recurso": escolhido["origem_recurso"],
                          "inicio": ini.isoformat(timespec="minutes"),
                          "termino": fim.isoformat(timespec="minutes"),
                          "inicio_data": ini.date().isoformat(), "fim_data": fim.date().isoformat(),
                          "duracao_dias": dias_uteis,
                          "horas_calendario": round(horas_cal, 2),
                          "espera_h": espera_h,
                          "blocos": [[a_.isoformat(timespec="minutes"), b_.isoformat(timespec="minutes")]
                                     for a_, b_ in escolhido["blocos"]]})
        pendentes.remove(at)
        feitas.add(at["id"])

    return resultado, gargalos


def gerar(inicio_planejamento=None):
    b = Base()
    inicio = inicio_planejamento or date.today()
    atividades, pendencias = montar_atividades(b)
    linhas, gargalos = agendar(b, atividades, inicio)

    # --------- resumo por projeto (visao macro)
    projetos = {}
    for l in linhas:
        p = projetos.setdefault(l["projeto"], {
            "projeto": l["projeto"], "cliente": l.get("cliente", ""),
            "prioridade": l["prioridade"], "entrega": l["entrega"].isoformat() if l["entrega"] else None,
            "inicio": None, "fim": None, "conjuntos": set(), "atividades": 0, "espera_h": 0.0})
        p["conjuntos"].add(l["conjunto"])
        p["atividades"] += 1
        p["espera_h"] += l.get("espera_h", 0) or 0
        if l.get("inicio_data"):
            p["inicio"] = min(p["inicio"] or l["inicio_data"], l["inicio_data"])
            p["fim"] = max(p["fim"] or l["fim_data"], l["fim_data"])
    for p in projetos.values():
        p["conjuntos"] = len(p["conjuntos"])
        p["espera_h"] = round(p["espera_h"], 1)
        p["atraso_dias"] = None
        if p["entrega"] and p["fim"]:
            p["atraso_dias"] = (date.fromisoformat(p["fim"]) - date.fromisoformat(p["entrega"])).days

    # --------- capacidade consumida por recurso e por maquina
    carga = []
    for r in b.recursos.values():
        h = r["agenda"].horas_ocupadas()
        if h > 0 or r["tipo"] != "CONTINGENCIAL":
            carga.append({"tipo": "PESSOA", "nome": r["nome"], "setor": r["setor"],
                          "capacidade_mes": r["capacidade_mes"], "horas_alocadas": round(h, 1),
                          "regime": r["tipo"]})
    for m in b.maquinas.values():
        h = m["agenda"].horas_ocupadas()
        carga.append({"tipo": "MAQUINA", "nome": m["nome"], "setor": m["setor"],
                      "capacidade_mes": m["cap_mes"], "horas_alocadas": round(h, 1), "regime": "MAQUINA"})

    # --------- gargalos consolidados
    garg = {}
    for g in gargalos:
        k = (g["recurso"], g["processo"])
        x = garg.setdefault(k, {"recurso": g["recurso"], "maquina": g["maquina"], "processo": g["processo"],
                                "setor": g["setor"], "espera_h": 0.0, "ocorrencias": 0, "projetos": set()})
        x["espera_h"] += g["espera_h"]
        x["ocorrencias"] += 1
        x["projetos"].add(g["projeto"])
    lista_garg = []
    for x in garg.values():
        x["projetos"] = sorted(x["projetos"])
        x["espera_h"] = round(x["espera_h"], 1)
        lista_garg.append(x)
    lista_garg.sort(key=lambda x: -x["espera_h"])

    # --------- comparativo matriz de complexidade x orcamento (orcado x planejado)
    comparativo = []
    for linha in b.carteira:
        proj, conj = linha["projeto"], linha["conjunto"]
        familia, complex_ = norm(linha.get("familia")), norm(linha.get("complexidade"))
        if (familia, complex_) not in b.matriz:
            continue
        if (linha.get("tipo") or "").upper() != "FERRAMENTAL":
            continue  # a matriz cadastrada e por ferramental; caixa de macho nao tem linha propria
        do_conj = [l for l in linhas if l["projeto"] == proj and l["conjunto"] == conj]
        if not do_conj:
            continue
        # agrupa o que foi agendado por etapa macro
        real = {}
        for l in do_conj:
            et = b.roteiro.get(l["processo"], {}).get("etapa", l["etapa"])
            k = (et, l["unidade"])
            real[k] = real.get(k, 0) + l["valor"]
        for m in b.matriz[(familia, complex_)]:
            et = b.roteiro.get(m["processo"], {}).get("etapa", m["etapa"])
            k = (et, m["unidade"])
            orcado = real.get(k)
            comparativo.append({
                "projeto": proj, "conjunto": conj, "complexidade": linha.get("complexidade"),
                "etapa": m["etapa"], "unidade": m["unidade"],
                "matriz": m["valor"], "orcamento": orcado,
                "desvio": None if orcado is None else round(orcado - m["valor"], 2),
            })

    # --------- pendencias derivadas do que foi (ou nao) agendado
    for linha in b.carteira:
        if (linha.get("tipo") or "").upper() != "FERRAMENTAL":
            continue
        tem_projeto = any(l["projeto"] == linha["projeto"] and l["conjunto"] == linha["conjunto"]
                          and l["processo"] == "PROJETOS" for l in linhas)
        if not tem_projeto:
            pendencias.append({
                "tipo": "SEM ETAPA DE PROJETO", "projeto": linha["projeto"], "conjunto": linha["conjunto"],
                "processo": "PROJETOS",
                "detalhe": "O orcamento nao traz horas de PROJETOS (marcado *** Sem Engenharia ***). "
                           "O cronograma nao inclui etapa de engenharia para este conjunto - confirmar se "
                           "e para usar a matriz de complexidade ou se o projeto ja existe."})
    if any((l.get("tipo") or "").upper() == "CAIXA_MACHO" for l in b.carteira):
        pendencias.append({
            "tipo": "MATRIZ SEM CAIXA DE MACHO", "projeto": "(todos)", "conjunto": "(caixas de macho)",
            "processo": "-",
            "detalhe": "A matriz de complexidade informa QUANTAS caixas de macho cada complexidade tem, mas nao "
                       "traz horas proprias por etapa para a caixa de macho. Hoje as caixas so entram no "
                       "cronograma quando ha horas de orcamento. Definir uma matriz de caixa de macho."})
    pendencias.append({
        "tipo": "PRAZO DE MATERIA-PRIMA", "projeto": "(todos)", "conjunto": "(todos)", "processo": "COMPRAS",
        "detalhe": "Os orcamentos listam materia-prima (aluminio fundido, vents/peneiras) mas nao ha prazo de "
                   "compra/entrega cadastrado. O cronograma comeca a usinagem sem esperar material."})

    # --------- premissas declaradas
    premissas = []
    for p, lst in b.habilidades.items():
        for c in lst:
            if (c["origem"] or "").upper() == "PREMISSA":
                premissas.append({"onde": f"habilidades.csv - {p}", "item": c["recurso"], "detalhe": c["obs"]})
    for r in b.carteira:
        if (r.get("origem_datas") or "").upper() == "PREMISSA":
            premissas.append({"onde": "carteira.csv - datas", "item": f"{r['projeto']} / {r['conjunto']}",
                              "detalhe": r.get("observacao") or "Datas de liberacao/entrega assumidas"})
    for k, rot in b.roteiro.items():
        if (rot["origem"] or "").upper() == "PREMISSA":
            premissas.append({"onde": "roteiro_processos.csv", "item": rot["processo"], "detalhe": rot["obs"]})
    premissas.append({"onde": "roteiro_processos.csv", "item": "FUNDICAO EXT",
                      "detalhe": "Prazo de fundicao externa contado em dias uteis - confirmar se o fornecedor "
                                 "trabalha em dias corridos"})
    premissas.append({"onde": "motor", "item": "Sobreposicao entre etapas",
                      "detalhe": "Todas as etapas estao encadeadas fim-a-inicio (sobreposicao 0%). Para liberar "
                                 "CAM ou usinagem antes do fim do projeto, ajustar a coluna sobreposicao_pct"})

    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "inicio_planejamento": inicio.isoformat(),
        "hoje": date.today().isoformat(),
        "linhas": linhas,
        "projetos": sorted(projetos.values(), key=lambda p: (p["prioridade"], p["projeto"])),
        "carga": carga,
        "gargalos": lista_garg,
        "pendencias": pendencias,
        "premissas": premissas,
        "comparativo": comparativo,
    }


if __name__ == "__main__":
    import json
    d = gerar()
    print(json.dumps({k: v for k, v in d.items() if k != "linhas"}, ensure_ascii=False, indent=2, default=str)[:4000])
    print(f"\n{len(d['linhas'])} atividades")
