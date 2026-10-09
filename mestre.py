"""
CRONOGRAMA MESTRE - MoldeTerm
=============================
Monta a estrutura de quatro niveis:

    HM  ->  ITEM  ->  SETOR  ->  grade hora a hora

De onde vem cada coisa:
  - HM, SETOR, janela de datas, prioridade  -> pedidos/cronograma_mestre.xlsx
  - ITEM e HORAS por processo               -> pedidos/horas_itens.csv (gerado por
                                               importar_hms.py a partir das planilhas
                                               de HM da pasta pedidos/hms)
  - fatores aplicados nas horas             -> base/parametros.csv
  - capacidade e turno de cada setor        -> base/capacidade_setor.csv + calendarios

A grade hora a hora e calculada: as horas do setor sao espalhadas nos dias uteis da
janela (sem feriados) e, dentro do dia, nas horas do turno daquele setor. O valor de
cada celula e quantos recursos do setor ficam ocupados naquela hora.

Nada e estimado: setor sem horas na planilha da HM fica marcado como tal.
"""

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from motor import BASE, Calendario, data_br, ler, norm, num

RAIZ = Path(__file__).resolve().parent
PEDIDOS = RAIZ / "pedidos"
ARQ_MESTRE = PEDIDOS / "cronograma_mestre.xlsx"
ARQ_ITENS = PEDIDOS / "horas_itens.csv"
ARQ_CABECALHO = PEDIDOS / "hms_cabecalho.csv"

NAO_DIST = "(nao distribuido por item)"

# Setores que a MoldeTerm nao executa dentro de casa: as horas ficam registradas para
# nao se perderem, mas nao consomem capacidade nem entram na grade hora a hora.
SETORES_EXTERNOS = {"TERCEIROS"}


# ------------------------------------------------------------------ cadastros
def carregar_calendarios():
    feriados = {d for d in (data_br(r.get("data")) for r in ler(BASE / "feriados.csv")) if d}
    cals = {}
    for r in ler(BASE / "calendarios.csv"):
        cals[norm(r["calendario"])] = Calendario(
            r["calendario"], r["inicio"], r["fim"],
            r.get("intervalo_inicio"), r.get("intervalo_fim"), feriados)
    return cals, feriados


def carregar_parametros():
    p = {}
    for r in ler(BASE / "parametros.csv"):
        p[norm(r["parametro"]).lower().replace(" ", "_")] = {
            "valor": num(r.get("valor"), 0) or 0, "origem": r.get("origem", ""),
            "obs": r.get("observacao", "")}
    return p


def carregar_capacidade():
    cap = {}
    for r in ler(BASE / "capacidade_setor.csv"):
        cap[norm(r["chave"])] = {
            "setor": r["setor"], "chave": norm(r["chave"]),
            "h_dia": num(r.get("h_dia"), 0) or 0,
            "calendarios": [norm(c) for c in (r.get("calendarios") or "").split("+") if c.strip()],
            "composicao": r.get("composicao", ""), "obs": r.get("observacao", "")}
    return cap


def chave_capacidade(setor, recurso):
    if "LAGUN" in norm(recurso):
        return "USINAGEM LAGUN"
    return norm(setor)


# ------------------------------------------------------------------ leitura
def ler_mestre():
    if not ARQ_MESTRE.exists():
        return None
    from openpyxl import load_workbook
    wb = load_workbook(ARQ_MESTRE, data_only=True)
    linhas = []
    if "Cronograma Project" in wb.sheetnames:
        dados = [list(r) for r in wb["Cronograma Project"].iter_rows(values_only=True)]
        cab = [norm(c) for c in dados[0]]
        idx = {}
        for campo, nomes in {"hm": ["PROJETO", "HM"], "setor": ["SETOR"], "recurso": ["RECURSO"],
                             "horas": ["HORAS"], "inicio": ["INICIO"], "fim": ["FIM"],
                             "prioridade": ["PRIORIDADE"], "obs": ["OBSERVACAO"]}.items():
            for n in nomes:
                if n in cab:
                    idx[campo] = cab.index(n)
                    break
        for l in dados[1:]:
            if not l or idx.get("hm") is None or not l[idx["hm"]]:
                continue
            g = lambda c: (l[idx[c]] if c in idx and idx[c] < len(l) else None)
            linhas.append({"hm": str(g("hm")).strip(), "setor": str(g("setor") or "").strip(),
                           "recurso": str(g("recurso") or "").strip(), "horas": num(g("horas"), 0) or 0,
                           "inicio": data_br(g("inicio")), "fim": data_br(g("fim")),
                           "prioridade": str(g("prioridade") or "").strip(),
                           "obs": str(g("obs") or "").strip()})

    def aba(nome):
        if nome not in wb.sheetnames:
            return []
        saida = []
        for l in wb[nome].iter_rows(values_only=True):
            v = [("" if c is None else (c.strftime("%d/%m/%Y") if isinstance(c, (datetime, date))
                                        else (f"{c:g}" if isinstance(c, float) else str(c)))) for c in l]
            if any(x.strip() for x in v):
                saida.append(v)
        return saida

    return {"linhas": linhas, "resumo": aba("Resumo"), "premissas": aba("Premissas")}


def ler_horas_itens():
    """{(HM, SETOR): [{item, servico, processos:{proc:h}, horas_base}]}"""
    if not ARQ_ITENS.exists():
        return {}
    fora = {}
    texto = ARQ_ITENS.read_text(encoding="utf-8-sig")
    for r in csv.DictReader(texto.splitlines(), delimiter=";"):
        if not r.get("hm"):
            continue
        k = (norm(r["hm"]), norm(r["setor"]))
        lst = fora.setdefault(k, [])
        rot = f"{r['servico']} - {r['item']}"
        it = next((x for x in lst if x["rotulo"] == rot), None)
        if not it:
            it = {"rotulo": rot, "item": r["item"], "servico": r["servico"],
                  "pn": r.get("pn", ""), "processos": {}, "horas_base": 0.0}
            lst.append(it)
        h = num(r["horas_base"], 0) or 0
        it["processos"][r["processo"]] = round(it["processos"].get(r["processo"], 0) + h, 2)
        it["horas_base"] = round(it["horas_base"] + h, 2)
    return fora


# ------------------------------------------------------------------ distribuicao
def dias_uteis(cal, ini, fim):
    d, saida = ini, []
    while d <= fim:
        if cal.util(d):
            saida.append(d)
        d += timedelta(days=1)
    return saida


def janela_do_dia(cals, dia):
    brutos = []
    for c in cals:
        brutos.extend(c.intervalos(dia))
    brutos.sort()
    unidos = []
    for a, b in brutos:
        if unidos and a <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], b))
        else:
            unidos.append((a, b))
    return unidos


def distribuir(horas, ini, fim, cap, cals):
    if not ini or not fim or horas <= 0 or not cals:
        return [], [], 0, 0, False
    ini, fim = min(ini, fim), max(ini, fim)
    dus = dias_uteis(cals[0], ini, fim)
    if not dus:
        return [], [], 0, 0, False
    h_dia = horas / len(dus)
    excede = cap["h_dia"] > 0 and h_dia > cap["h_dia"] + 0.01
    por_dia, grade = [], []
    for d in dus:
        janela = janela_do_dia(cals, d)
        minutos = sum((b - a).total_seconds() / 60 for a, b in janela) or 1
        intens = h_dia / (minutos / 60)
        celulas = {}
        for a, b in janela:
            cursor = a
            while cursor < b:
                prox = (cursor + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                prox = min(prox if prox > cursor else cursor + timedelta(hours=1), b)
                celulas[cursor.hour] = round(celulas.get(cursor.hour, 0)
                                             + intens * (prox - cursor).total_seconds() / 3600, 3)
                cursor = prox
        por_dia.append({"data": d.isoformat(), "horas": round(h_dia, 4)})
        grade.append({"data": d.isoformat(), "celulas": celulas,
                      "janela": [[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in janela]})
    return por_dia, grade, round(h_dia, 2), len(dus), excede


# ------------------------------------------------------------------ montagem
def gerar():
    cals, feriados = carregar_calendarios()
    cap_setor = carregar_capacidade()
    par = carregar_parametros()
    horas_itens = ler_horas_itens()
    mestre = ler_mestre()
    pendencias, premissas, comparativo = [], [], []

    if not mestre:
        return {"erro": f"Nao encontrei {ARQ_MESTRE.name} na pasta pedidos."}

    f_cal = 1 + (par.get("calibracao_pct", {}).get("valor", 0) or 0) / 100
    f_marg = 1 + (par.get("margem_seguranca_pct", {}).get("valor", 0) or 0) / 100

    # agrupa as linhas do cronograma por HM + setor
    por_hm_setor, hms = {}, {}
    for l in mestre["linhas"]:
        h = hms.setdefault(l["hm"], {"hm": l["hm"], "prioridade": l["prioridade"], "inicio": None,
                                     "fim": None, "entrega": None, "obs": [], "setores": set()})
        if norm(l["setor"]) == "ENTREGA" or l["horas"] == 0:
            h["entrega"] = l["fim"].isoformat() if l["fim"] else None
            if l["obs"]:
                h["obs"].append(l["obs"])
            continue
        h["setores"].add(l["setor"])
        if l["inicio"]:
            h["inicio"] = min(h["inicio"] or l["inicio"].isoformat(), l["inicio"].isoformat())
        if l["fim"]:
            h["fim"] = max(h["fim"] or l["fim"].isoformat(), l["fim"].isoformat())
        por_hm_setor.setdefault((l["hm"], norm(l["setor"])), []).append(l)

    atividades = []
    for (hm, setor_n), linhas_setor in por_hm_setor.items():
        principal = max(linhas_setor, key=lambda x: x["horas"])
        itens = horas_itens.get((norm(hm), setor_n), [])

        for l in linhas_setor:
            chave = chave_capacidade(l["setor"], l["recurso"])
            cap = cap_setor.get(chave)
            if not cap:
                pendencias.append({"tipo": "SETOR SEM CAPACIDADE", "hm": hm, "item": "-", "setor": l["setor"],
                                   "detalhe": f"Setor '{l['setor']}' (recurso '{l['recurso']}') sem capacidade "
                                              f"diaria em base/capacidade_setor.csv."})
                cap = {"setor": l["setor"], "chave": chave, "h_dia": 0,
                       "calendarios": ["COMERCIAL"], "composicao": "", "obs": ""}
            cal_objs = [cals[c] for c in cap["calendarios"] if c in cals] or [cals["COMERCIAL"]]

            if itens and l is principal:
                fatias = [{"item": i["item"], "servico": i["servico"], "rotulo": i["rotulo"],
                           "processos": i["processos"], "base": i["horas_base"],
                           "horas": round(i["horas_base"] * f_cal * f_marg, 2),
                           "fonte": "PLANILHA HM"} for i in itens]
            else:
                motivo = ("Horas do cronograma mestre - a planilha da HM nao tem horas para este setor"
                          if not itens else
                          f"Linha '{l['recurso']}' do cronograma - a distribuicao por item foi aplicada na "
                          f"linha principal do setor")
                fatias = [{"item": NAO_DIST, "servico": "", "rotulo": NAO_DIST, "processos": {},
                           "base": None, "horas": round(l["horas"] * f_marg, 2),
                           "fonte": "CRONOGRAMA", "motivo": motivo}]

            for f in fatias:
                if f["horas"] <= 0.001:
                    continue
                por_dia, grade, h_dia, n_dias, excede = distribuir(
                    f["horas"], l["inicio"], l["fim"], cap, cal_objs)
                atividades.append({
                    "id": f"{hm}|{f['rotulo']}|{l['setor']}|{l['recurso']}",
                    "hm": hm, "item": f["rotulo"], "item_nome": f["item"], "servico": f["servico"],
                    "setor": l["setor"], "recurso": l["recurso"], "chave_cap": chave,
                    "processos": f["processos"], "horas_base": f["base"], "horas": f["horas"],
                    "horas_cronograma": l["horas"], "fonte_horas": f["fonte"],
                    "motivo": f.get("motivo", ""),
                    "inicio": l["inicio"].isoformat() if l["inicio"] else None,
                    "fim": l["fim"].isoformat() if l["fim"] else None,
                    "prioridade": l["prioridade"], "obs": l["obs"],
                    "cap_dia": cap["h_dia"], "composicao": cap["composicao"],
                    "calendarios": cap["calendarios"], "h_dia": h_dia, "dias_uteis": n_dias,
                    "excede_capacidade": excede, "por_dia": por_dia, "grade": grade})
                if excede:
                    pendencias.append({
                        "tipo": "JANELA CURTA", "hm": hm, "item": f["rotulo"], "setor": l["setor"],
                        "detalhe": f"{f['horas']:g} h em {n_dias} dias uteis dao {h_dia:g} h/dia, acima das "
                                   f"{cap['h_dia']:g} h/dia do setor {cap['setor']}."})

        # comparativo planilha x cronograma, por setor
        if itens:
            base = round(sum(i["horas_base"] for i in itens), 2)
            cron = round(sum(x["horas"] for x in linhas_setor), 2)
            comparativo.append({
                "hm": hm, "setor": linhas_setor[0]["setor"], "base": base,
                "calibrada": round(base * f_cal, 2), "com_margem": round(base * f_cal * f_marg, 2),
                "cronograma": cron, "desvio": round(base * f_cal - cron, 2), "itens": len(itens)})

    # setores que existem na planilha da HM mas nao tem janela no cronograma
    terceiros = []
    for (hm_n, setor_n), itens in horas_itens.items():
        if setor_n in SETORES_EXTERNOS:
            for i in itens:
                terceiros.append({"hm": hm_n, "item": i["rotulo"], "item_nome": i["item"],
                                  "servico": i["servico"], "setor": setor_n,
                                  "processos": i["processos"], "horas_base": i["horas_base"]})
            continue
        achou = any(norm(k[0]) == hm_n and k[1] == setor_n for k in por_hm_setor)
        if not achou:
            h = round(sum(i["horas_base"] for i in itens), 2)
            pendencias.append({
                "tipo": "SETOR SEM JANELA", "hm": hm_n, "item": "-", "setor": setor_n,
                "detalhe": f"A planilha da HM tem {h:g} h base de {setor_n} mas o cronograma mestre nao tem "
                           f"linha desse setor para esta HM - essas horas ficaram sem data."})
    terceiros.sort(key=lambda t: (t["hm"], t["item"]))
    for (hm, setor_n), linhas_setor in por_hm_setor.items():
        if not horas_itens.get((norm(hm), setor_n)) and horas_itens:
            if any(norm(k[0]) == norm(hm) for k in horas_itens):
                pendencias.append({
                    "tipo": "SETOR SEM ITENS", "hm": hm, "item": "-", "setor": linhas_setor[0]["setor"],
                    "detalhe": "A planilha desta HM nao traz horas para este setor - o cronograma segue com as "
                               "horas da planilha mestre, sem quebra por item."})

    for h in hms.values():
        h["setores"] = sorted(h["setores"])
        h["obs"] = " | ".join(h["obs"])
        h["horas"] = round(sum(a["horas"] for a in atividades if a["hm"] == h["hm"]), 1)
        h["itens"] = len({a["item"] for a in atividades if a["hm"] == h["hm"] and a["item"] != NAO_DIST})
        h["atraso_dias"] = None
        if h["entrega"] and h["fim"]:
            h["atraso_dias"] = (date.fromisoformat(h["fim"]) - date.fromisoformat(h["entrega"])).days

    # ---- carga por setor + pico diario
    carga, dia_setor = {}, {}
    for a in atividades:
        c = carga.setdefault(a["chave_cap"], {"setor": a["setor"], "chave": a["chave_cap"],
                                              "composicao": a["composicao"], "cap_dia": a["cap_dia"],
                                              "horas": 0.0, "hms": set(), "pico_dia": 0.0, "pico_data": None})
        c["horas"] += a["horas"]
        c["hms"].add(a["hm"])
        for d in a["por_dia"]:
            k = (a["chave_cap"], d["data"])
            dia_setor[k] = dia_setor.get(k, 0.0) + d["horas"]
    for (ch, dt), v in dia_setor.items():
        if ch in carga and v > carga[ch]["pico_dia"]:
            carga[ch]["pico_dia"], carga[ch]["pico_data"] = v, dt
    lista_carga = []
    for c in carga.values():
        c["hms"] = sorted(c["hms"])
        c["horas"] = round(c["horas"], 1)
        c["pico_dia"] = round(c["pico_dia"], 2)
        c["ocupacao_pico"] = round(c["pico_dia"] / c["cap_dia"] * 100, 1) if c["cap_dia"] else None
        lista_carga.append(c)
    lista_carga.sort(key=lambda c: -c["horas"])

    # ---- curva de carga diaria por setor (para a aba de capacidade)
    curva = {}
    for (ch, dt), v in sorted(dia_setor.items(), key=lambda x: x[0][1]):
        curva.setdefault(ch, []).append({"data": dt, "horas": round(v, 2)})

    # ---- pendencias e premissas
    pendencias.append({
        "tipo": "FERIADOS DIVERGENTES", "hm": "(todos)", "item": "-", "setor": "(todos)",
        "detalhe": "A aba Resumo do cronograma lista 3 feriados (07/09, 12/10, 02/11) e a aba Premissas lista 5 "
                   "(mais 15/11 e 20/11). O programa esta usando os 5 - confirmar."})
    for r in ler(ARQ_CABECALHO):
        hm_n = norm(r.get("hm"))
        if not hm_n or any(k[0] == hm_n for k in horas_itens):
            continue
        pendencias.append({
            "tipo": "PLANILHA SEM HORAS", "hm": r.get("hm", hm_n), "item": "-", "setor": "(todos)",
            "detalhe": f"A planilha {r.get('arquivo') or hm_n} foi importada mas nao trouxe nenhuma hora - a aba "
                       f"Orcamento Hora esta em branco. Enquanto nao vier preenchida, esta HM segue no cronograma "
                       f"so com as horas da planilha mestre, sem quebra por item."})

    premissas += [
        {"onde": "mestre.py", "item": "Distribuicao no tempo",
         "detalhe": "As horas de cada setor sao espalhadas por igual nos dias uteis entre o inicio e o fim da "
                    "planilha mestre. Se o consumo real for concentrado no comeco ou no fim, informar o perfil."},
        {"onde": "mestre.py", "item": "Grade hora a hora",
         "detalhe": "Cada celula mostra quantos recursos do setor ficam ocupados naquela hora (passa de 1 porque "
                    "o setor tem varias pessoas/maquinas). Nao e a agenda de uma pessoa especifica."},
        {"onde": "mestre.py", "item": "Setor com mais de uma linha",
         "detalhe": "Quando o setor tem duas linhas no cronograma (Usinagem CNC e Lagun, por exemplo), a quebra "
                    "por item e aplicada na linha de maior carga; a outra segue com as horas do cronograma."},
        {"onde": "base/de_para_processos.csv", "item": "Servicos de terceiros",
         "detalhe": "A eletroerosao e servico externo (confirmado por Jonas em 14/09/2026): as horas ficam "
                    "registradas na aba Pendencias, mas nao consomem capacidade nem entram na grade hora a hora."},
    ]

    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "hoje": date.today().isoformat(),
        "arquivo": ARQ_MESTRE.name,
        "fator_calibracao": round(f_cal, 4), "fator_margem": round(f_marg, 4),
        "fator_total": round(f_cal * f_marg, 4),
        "parametros": [{"parametro": k, **v} for k, v in par.items()],
        "hms": sorted(hms.values(), key=lambda h: h["inicio"] or ""),
        "atividades": atividades,
        "carga_setor": lista_carga, "curva_carga": curva,
        "comparativo": sorted(comparativo, key=lambda c: (c["hm"], c["setor"])),
        "resumo": mestre["resumo"], "premissas_planilha": mestre["premissas"],
        "feriados": sorted(f.isoformat() for f in feriados),
        "terceiros": terceiros,
        "pendencias": pendencias, "premissas": premissas,
    }


if __name__ == "__main__":
    d = gerar()
    print(f"fator: calibracao {d['fator_calibracao']} x margem {d['fator_margem']} = {d['fator_total']}")
    print(f"{len(d['hms'])} HMs | {len(d['atividades'])} atividades | {len(d['pendencias'])} pendencias\n")
    for h in d["hms"]:
        print(f"  {h['hm']:<8} {h['inicio']} -> {h['fim']}  entrega {h['entrega']}  "
              f"{h['horas']:>8} h  {h['itens']} itens")
    print("\nCarga por setor:")
    for c in d["carga_setor"]:
        print(f"  {c['setor']:<14} {c['horas']:>8} h   pico {c['pico_dia']:>7} h/dia de {c['cap_dia']:g} "
              f"({c['ocupacao_pico']}%) em {c['pico_data']}")
