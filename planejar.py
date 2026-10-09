"""
PLANEJAR UMA HM NOVA - encaixe por capacidade que sobra
=======================================================
Voce informa a HM, a data de entrega e a prioridade. O programa calcula sozinho
a janela de cada setor, usando:

  - as horas da planilha da HM (x calibracao x margem, como no resto do programa);
  - a capacidade diaria de cada setor (base/capacidade_setor.csv);
  - o que JA esta comprometido dia a dia pelo cronograma mestre - as HMs que ja
    estao la nao se mexem, a nova ocupa o que sobra;
  - as regras de liberacao parcial cadastradas (base/roteiro_setores.csv):
    CAM abre com 40% do CAD, CNC com 40% do CAM, bancada com 50% da usinagem,
    digitalizacao com 60% da bancada.

Se nao couber ate a data de entrega, ele NAO forca a data: mostra qual setor
segurou e quantos dias faltaram.
"""

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import mestre
from motor import BASE, num

RAIZ = Path(__file__).resolve().parent
PEDIDOS = RAIZ / "pedidos"
HORIZONTE = 600


def norm(t):
    import re
    import unicodedata
    if t is None:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def carregar_roteiro():
    rot = []
    for r in mestre.ler(BASE / "roteiro_setores.csv"):
        rot.append({"seq": int(num(r.get("seq"), 999)), "setor": r["setor"],
                    "pred": (r.get("predecessora") or "").strip(),
                    "pct": num(r.get("abre_com_pct"), 0) or 0,
                    "origem": r.get("origem", ""), "obs": r.get("observacao", "")})
    rot.sort(key=lambda x: x["seq"])
    return rot


def estado_atual():
    """Le o cronograma de hoje e devolve o que ja esta comprometido por setor e por dia.

    Vem em dois recortes: por setor e por chave de capacidade - a Usinagem tem duas chaves
    (USINAGEM e USINAGEM LAGUN) com capacidades diarias diferentes.
    """
    d = mestre.gerar()
    comprometido, por_chave, recursos = {}, {}, {}
    for a in d["atividades"]:
        ch = norm(a["setor"])
        recursos.setdefault(ch, {}).setdefault(a["recurso"], 0)
        recursos[ch][a["recurso"]] += a["horas"]
        for dia in a["por_dia"]:
            comprometido.setdefault(ch, {})
            comprometido[ch][dia["data"]] = round(comprometido[ch].get(dia["data"], 0) + dia["horas"], 4)
            k = a["chave_cap"]
            por_chave.setdefault(k, {})
            por_chave[k][dia["data"]] = round(por_chave[k].get(dia["data"], 0) + dia["horas"], 4)
    recurso_padrao = {ch: max(v.items(), key=lambda x: x[1])[0] for ch, v in recursos.items()}
    return d, comprometido, por_chave, recurso_padrao


def data_que_libera(dias, total, pct):
    """Data em que a predecessora atinge a fatia liberada. `dias` tem de vir em ordem de data."""
    if not dias:
        return None
    alvo = total * pct / 100
    acc = 0.0
    for dia in dias:
        acc += dia["horas"]
        if acc >= alvo - 0.001:
            return date.fromisoformat(dia["data"])
    return date.fromisoformat(dias[-1]["data"])


def consumir_capacidade(horas, inicio, cap, cal, comprometido_dia, reservado_dia):
    """Consome dia a dia a capacidade que sobra do setor, sem mexer no que ja esta programado.

    Devolve (dias, restante, dias_esperando, dias_sem_folga) e vai marcando o que reservou
    em `reservado_dia`, para que varias etapas seguidas nao briguem pelo mesmo dia.
    """
    restante, dias, dia = horas, [], inicio
    espera, sem_folga = 0, 0
    for _ in range(HORIZONTE):
        if restante <= 0.001:
            break
        if cal.util(dia):
            iso = dia.isoformat()
            disp = cap["h_dia"] - comprometido_dia.get(iso, 0) - reservado_dia.get(iso, 0)
            if disp > 0.01:
                usa = min(disp, restante)
                dias.append({"data": iso, "horas": round(usa, 4), "livre_no_dia": round(disp, 2)})
                reservado_dia[iso] = round(reservado_dia.get(iso, 0) + usa, 4)
                restante -= usa
            else:
                sem_folga += 1
                if not dias:
                    espera += 1
        dia += timedelta(days=1)
    return dias, restante, espera, sem_folga


def curva_do_setor(d, hm, setor):
    """Curva dia a dia de um setor de uma HM que JA esta no cronograma, em ordem de data.

    O setor pode ter varias atividades na mesma janela (uma por item), por isso o merge.
    """
    hm_n, sn = norm(hm), norm(setor)
    por_data = {}
    for a in d["atividades"]:
        if norm(a["hm"]) != hm_n or norm(a["setor"]) != sn:
            continue
        for dia in a["por_dia"]:
            por_data[dia["data"]] = round(por_data.get(dia["data"], 0) + dia["horas"], 4)
    dias = [{"data": k, "horas": v} for k, v in sorted(por_data.items())]
    return dias, round(sum(x["horas"] for x in dias), 2)


def d_br(s):
    return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")


def horas_da_hm(d, hm, apenas_setor=None):
    """Horas ja com os fatores, por setor, vindas da planilha da HM."""
    itens = mestre.ler_horas_itens()
    f = d["fator_total"]
    por_setor = {}
    for (hm_n, setor_n), lista in itens.items():
        if hm_n != norm(hm):
            continue
        base = sum(i["horas_base"] for i in lista)
        por_setor[setor_n] = {"setor_norm": setor_n, "base": round(base, 2),
                              "horas": round(base * f, 2), "itens": len(lista)}
    return por_setor


def nome_setor_bonito(setor_norm, cap_setor):
    for c in cap_setor.values():
        if norm(c["setor"]) == setor_norm:
            return c["setor"]
    return setor_norm.title()


def nome_setor_no_cronograma(d, setor_norm, cap_setor):
    """O setor escrito como ele JA aparece na planilha mestre.

    O cadastro usa 'Digitalizacao' e a planilha 'Digitalização'; gravar a grafia do cadastro
    faria o mesmo setor virar dois no filtro e na arvore da tela.
    """
    for a in d["atividades"]:
        if norm(a["setor"]) == setor_norm:
            return a["setor"]
    return nome_setor_bonito(setor_norm, cap_setor)


def planejar(hm, entrega, prioridade="Alta", liberacao=None, so_dias_uteis=True):
    d, comprometido, _por_chave, recurso_padrao = estado_atual()
    cals, feriados = mestre.carregar_calendarios()
    cap_setor = mestre.carregar_capacidade()
    roteiro = carregar_roteiro()
    horas = horas_da_hm(d, hm)

    if not horas:
        return {"erro": f"Nao encontrei horas da {hm} em pedidos/horas_itens.csv. "
                        f"Coloque a planilha em pedidos/hms e rode o IMPORTAR_HMS."}

    liberacao = liberacao or date.today().isoformat()
    ini_lib = date.fromisoformat(liberacao)
    cal_padrao = cals["COMERCIAL"]

    novo = {}          # {setor_norm: {data: horas}}
    resultado = []
    avisos = []
    plan_por_setor = {}

    for passo in roteiro:
        sn = norm(passo["setor"])
        if sn not in horas:
            continue
        h = horas[sn]["horas"]
        cap = cap_setor.get(sn) or cap_setor.get(sn.replace("DIGITALIZACAO", "DIGITALIZACAO"))
        if not cap or cap["h_dia"] <= 0:
            avisos.append(f"Setor {passo['setor']} sem capacidade diaria cadastrada - ficou de fora.")
            continue
        cal = cals.get(cap["calendarios"][0], cal_padrao) if cap["calendarios"] else cal_padrao

        # ---- quando pode comecar
        inicio_min, regra = ini_lib, "data de liberacao"
        if passo["pred"]:
            pred = plan_por_setor.get(norm(passo["pred"]))
            if pred:
                inicio_min = data_que_libera(pred["dias"], pred["horas"], passo["pct"])
                regra = f"{passo['pct']:g}% de {passo['pred']} liberado"
            else:
                regra = f"{passo['pred']} nao entra nesta HM - comecou na liberacao"

        # ---- consome a capacidade que sobra
        dias, restante, espera, sem_folga = consumir_capacidade(
            h, max(inicio_min, ini_lib), cap, cal,
            comprometido.get(sn, {}), novo.setdefault(sn, {}))

        if restante > 0.01:
            avisos.append(f"{passo['setor']}: faltaram {restante:.1f} h - nao coube no horizonte de "
                          f"{HORIZONTE} dias.")
        if not dias:
            continue

        info = {
            "setor": passo["setor"], "setor_norm": sn,
            "recurso": recurso_padrao.get(sn, cap["composicao"] or passo["setor"]),
            "horas": round(h, 2), "base": horas[sn]["base"], "itens": horas[sn]["itens"],
            "inicio": dias[0]["data"], "fim": dias[-1]["data"], "dias": dias,
            "dias_uteis": len(dias), "h_dia": round(h / len(dias), 2),
            "cap_dia": cap["h_dia"], "regra": regra,
            "pode_comecar": inicio_min.isoformat(),
            "dias_esperando": espera, "dias_sem_folga": sem_folga,
            "ocupacao_media": round(h / len(dias) / cap["h_dia"] * 100, 1) if cap["h_dia"] else None,
        }
        plan_por_setor[sn] = info
        resultado.append(info)

    if not resultado:
        return {"erro": f"Nao consegui planejar a {hm}: nenhum setor com horas e capacidade."}

    fim = max(r["fim"] for r in resultado)
    inicio = min(r["inicio"] for r in resultado)
    critico = max(resultado, key=lambda r: r["fim"])
    entrega_d = date.fromisoformat(entrega) if entrega else None
    folga = (entrega_d - date.fromisoformat(fim)).days if entrega_d else None

    # quanto de espera cada setor causou, para explicar o prazo
    gargalos = [{"setor": r["setor"], "dias_esperando": r["dias_esperando"],
                 "dias_sem_folga": r["dias_sem_folga"], "ocupacao_media": r["ocupacao_media"],
                 "h_dia": r["h_dia"], "cap_dia": r["cap_dia"], "dias_uteis": r["dias_uteis"]}
                for r in sorted(resultado, key=lambda r: -((r["dias_esperando"] or 0) * 100
                                                           + (r["ocupacao_media"] or 0)))][:4]

    return {
        "hm": hm, "prioridade": prioridade, "liberacao": liberacao, "entrega": entrega,
        "inicio": inicio, "fim": fim, "folga_dias": folga,
        "cabe": (folga is None or folga >= 0),
        "setor_critico": critico["setor"], "horas_total": round(sum(r["horas"] for r in resultado), 1),
        "base_total": round(sum(r["base"] for r in resultado), 1),
        "fator": d["fator_total"], "fator_margem": d["fator_margem"],
        "fator_calibracao": d["fator_calibracao"],
        "setores": resultado, "gargalos": gargalos, "avisos": avisos,
        "roteiro_usado": [{"setor": p["setor"], "pred": p["pred"], "pct": p["pct"],
                           "origem": p["origem"], "obs": p["obs"]} for p in roteiro],
    }


# ------------------------------------------- setor que ficou sem janela numa HM ja programada
def planejar_setor_faltante(hm, setor):
    """Calcula a janela de um setor que tem horas na planilha da HM mas nao tem linha no
    cronograma mestre - o caso das pendencias 'SETOR SEM JANELA'.

    Diferente do planejar(), aqui a HM ja esta no cronograma: a predecessora tem janela fixa e
    a curva dia a dia dela vem das atividades que ja existem, nao de um calculo novo.
    """
    d, _comprometido, por_chave, recurso_padrao = estado_atual()
    cals, _feriados = mestre.carregar_calendarios()
    cap_setor = mestre.carregar_capacidade()
    hm_n, sn = norm(hm), norm(setor)
    hoje = date.today()

    hm_cron = next((h for h in d["hms"] if norm(h["hm"]) == hm_n), None)
    if not hm_cron:
        return {"erro": f"{hm} nao esta no cronograma mestre - use a aba Planejar HM nova."}
    if any(norm(s) == sn for s in hm_cron["setores"]):
        return {"erro": f"A {hm} ja tem janela de {setor} no cronograma mestre."}

    horas = horas_da_hm(d, hm).get(sn)
    if not horas:
        return {"erro": f"A planilha da {hm} nao tem horas de {setor}."}

    # o recurso sai das outras HMs: sem nenhuma linha desse setor no cronograma nao da para
    # saber o que gravar na coluna Recurso, e inventar um nome seria uma estimativa
    recurso = recurso_padrao.get(sn)
    if not recurso:
        return {"erro": f"Nenhuma HM do cronograma mestre tem linha de {setor}, entao nao sei que recurso "
                        f"gravar. Cadastre uma linha de {setor} na planilha mestre antes."}

    chave = mestre.chave_capacidade(setor, recurso)
    cap = cap_setor.get(chave)
    if not cap or cap["h_dia"] <= 0:
        return {"erro": f"Setor {setor} sem capacidade diaria em base/capacidade_setor.csv."}
    cal = cals.get(cap["calendarios"][0], cals["COMERCIAL"]) if cap["calendarios"] else cals["COMERCIAL"]

    # ---- quando pode comecar: regra de liberacao parcial em cima do que ja esta programado
    avisos = []
    passo = next((p for p in carregar_roteiro() if norm(p["setor"]) == sn), None)
    if passo and passo["pred"]:
        dias_pred, total_pred = curva_do_setor(d, hm, passo["pred"])
        if dias_pred:
            inicio_min = data_que_libera(dias_pred, total_pred, passo["pct"])
            regra = f"{passo['pct']:g}% de {passo['pred']} liberado"
        else:
            inicio_min, regra = hoje, f"{passo['pred']} nao tem janela nesta HM"
            avisos.append(f"A {hm} nao tem linha de {passo['pred']} no cronograma mestre, entao nao da para "
                          f"aplicar a regra '{passo['pct']:g}% de {passo['pred']} liberado'. O calculo comecou "
                          f"hoje ({hoje:%d/%m/%Y}) - confira essa data antes de gravar.")
    elif passo:
        inicio_min, regra = hoje, "primeira etapa do roteiro"
    else:
        inicio_min, regra = hoje, "setor fora do roteiro"
        avisos.append(f"O setor {setor} nao esta em base/roteiro_setores.csv, entao nao ha regra de liberacao "
                      f"parcial para aplicar. O calculo comecou hoje ({hoje:%d/%m/%Y}).")

    if inicio_min < hoje:
        avisos.append(f"A regra '{regra}' libera em {inicio_min:%d/%m/%Y}, data que ja passou - o calculo "
                      f"comecou hoje ({hoje:%d/%m/%Y}) para nao ocupar capacidade que ja foi gasta.")
    inicio = max(inicio_min, hoje)

    dias, restante, espera, sem_folga = consumir_capacidade(
        horas["horas"], inicio, cap, cal, por_chave.get(chave, {}), {})

    if restante > 0.01:
        avisos.append(f"Faltaram {restante:.1f} h - nao coube no horizonte de {HORIZONTE} dias.")
    if not dias:
        return {"erro": f"Nao sobrou nenhuma hora livre de {setor} no horizonte de {HORIZONTE} dias - "
                        f"o setor esta com a capacidade toda tomada por outras HMs."}

    h, fim = horas["horas"], dias[-1]["data"]
    entrega = hm_cron.get("entrega")
    folga = (date.fromisoformat(entrega) - date.fromisoformat(fim)).days if entrega else None
    return {
        "hm": hm_cron["hm"], "setor": nome_setor_no_cronograma(d, sn, cap_setor), "setor_norm": sn,
        "recurso": recurso, "chave_cap": chave, "prioridade": hm_cron.get("prioridade") or "",
        "base": horas["base"], "horas": round(h, 2), "itens": horas["itens"],
        "inicio": dias[0]["data"], "fim": fim, "dias": dias, "dias_uteis": len(dias),
        "h_dia": round(h / len(dias), 2), "cap_dia": cap["h_dia"],
        "ocupacao_media": round(h / len(dias) / cap["h_dia"] * 100, 1) if cap["h_dia"] else None,
        "regra": regra, "pode_comecar": inicio.isoformat(),
        "dias_esperando": espera, "dias_sem_folga": sem_folga,
        "entrega": entrega, "folga_dias": folga, "cabe": (folga is None or folga >= 0),
        "fator": d["fator_total"], "fator_calibracao": d["fator_calibracao"],
        "fator_margem": d["fator_margem"], "avisos": avisos,
    }


# ------------------------------------------------------------------ gravar
def hms_disponiveis():
    """HMs que tem planilha importada e ainda NAO estao no cronograma mestre."""
    d = mestre.gerar()
    no_cron = {norm(h["hm"]) for h in d["hms"]}
    cab = {norm(r["hm"]): r for r in mestre.ler(PEDIDOS / "hms_cabecalho.csv")}
    itens = mestre.ler_horas_itens()
    com_horas = {k[0] for k in itens}
    saida = []
    for hm_n in sorted(com_horas | set(cab)):
        c = cab.get(hm_n, {})
        base = round(sum(i["horas_base"] for (h, s), lst in itens.items() if h == hm_n for i in lst), 2)
        saida.append({
            "hm": c.get("hm") or hm_n, "cliente": c.get("cliente", ""), "pn": c.get("pn", ""),
            "projeto": c.get("projeto", ""), "arquivo": c.get("arquivo", ""),
            "entrega_sugerida": c.get("final_fornecedor") or c.get("final_tupy") or "",
            "entrega_tupy": c.get("final_tupy", ""), "entrega_fornecedor": c.get("final_fornecedor", ""),
            "liberacao_sugerida": c.get("inicial_fornecedor") or c.get("inicial_tupy") or "",
            "horas_base": base, "no_cronograma": hm_n in no_cron,
            "avisos": c.get("avisos", ""),
        })
    return saida


def gravar_no_mestre(plano):
    """Acrescenta as linhas da HM nova na aba Cronograma Project, com backup antes."""
    from openpyxl import load_workbook
    arq = PEDIDOS / "cronograma_mestre.xlsx"
    if not arq.exists():
        return {"ok": False, "erro": "cronograma_mestre.xlsx nao encontrado."}
    bkp = PEDIDOS / "backup"
    bkp.mkdir(exist_ok=True)
    destino = bkp / f"cronograma_mestre_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
    shutil.copy2(arq, destino)

    wb = load_workbook(arq)
    ws = wb["Cronograma Project"]
    ja = {norm(ws.cell(row=r, column=1).value) for r in range(2, ws.max_row + 1)}
    if norm(plano["hm"]) in ja:
        return {"ok": False, "erro": f"{plano['hm']} ja existe no cronograma mestre. "
                                    f"Apague as linhas dela antes de gravar de novo."}

    # a coluna Horas da planilha mestre guarda a hora CALIBRADA (x1,20), como nas
    # outras HMs; a margem de seguranca o programa aplica por cima na hora de exibir.
    fm = plano.get("fator_margem") or 1
    linha = ws.max_row + 1
    for s in plano["setores"]:
        obs = f"Planejado pelo programa em {datetime.now():%d/%m/%Y} - abre com {s['regra']}"
        if s["dias_esperando"]:
            obs += f"; esperou {s['dias_esperando']} dia(s) uteis por capacidade"
        for col, val in enumerate([plano["hm"], s["setor"], s["recurso"], round(s["horas"] / fm, 2),
                                   d_br(s["inicio"]), d_br(s["fim"]), plano["prioridade"], obs], start=1):
            ws.cell(row=linha, column=col, value=val)
        linha += 1
    entrega = plano["entrega"] or plano["fim"]
    obs_e = "Entrega" + ("" if plano["cabe"] else
                         f" - ATENCAO: o cronograma fecha em {d_br(plano['fim'])}, "
                         f"{abs(plano['folga_dias'])} dias depois do prazo")
    for col, val in enumerate([plano["hm"], "Entrega", f"Marco {plano['hm']}", 0,
                               d_br(entrega), d_br(entrega), "Marco", obs_e], start=1):
        ws.cell(row=linha, column=col, value=val)

    wb.save(arq)
    return {"ok": True, "backup": destino.name, "linhas": len(plano["setores"]) + 1}


def colunas_do_mestre(ws):
    """{campo: numero da coluna} lido do cabecalho da aba - os mesmos nomes que mestre.ler_mestre()
    aceita na leitura, para gravar na coluna certa mesmo se a ordem mudar."""
    cab = [norm(c) for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    nomes = {"hm": ["PROJETO", "HM"], "setor": ["SETOR"], "recurso": ["RECURSO"], "horas": ["HORAS"],
             "inicio": ["INICIO"], "fim": ["FIM"], "prioridade": ["PRIORIDADE"], "obs": ["OBSERVACAO"]}
    col = {}
    for campo, alternativas in nomes.items():
        for n in alternativas:
            if n in cab:
                col[campo] = cab.index(n) + 1
                break
    return col


def gravar_setor_no_mestre(plano):
    """Acrescenta UMA linha de setor numa HM que ja esta no cronograma, com backup antes."""
    from openpyxl import load_workbook
    arq = PEDIDOS / "cronograma_mestre.xlsx"
    if not arq.exists():
        return {"ok": False, "erro": "cronograma_mestre.xlsx nao encontrado."}

    try:
        wb = load_workbook(arq)
        ws = wb["Cronograma Project"]
        col = colunas_do_mestre(ws)
        faltando = [c for c in ("hm", "setor", "horas", "inicio", "fim") if c not in col]
        if faltando:
            return {"ok": False, "erro": f"A aba Cronograma Project nao tem as colunas: {', '.join(faltando)}."}

        # relido agora, e nao no momento do calculo: a planilha pode ter sido editada no Excel
        for r in range(2, ws.max_row + 1):
            if (norm(ws.cell(row=r, column=col["hm"]).value) == norm(plano["hm"])
                    and norm(ws.cell(row=r, column=col["setor"]).value) == plano["setor_norm"]):
                return {"ok": False, "erro": f"A {plano['hm']} ja tem linha de {plano['setor']} no cronograma "
                                             f"mestre - nada foi gravado."}

        bkp = PEDIDOS / "backup"
        bkp.mkdir(exist_ok=True)
        destino = bkp / f"cronograma_mestre_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
        shutil.copy2(arq, destino)

        obs = f"Planejado pelo programa em {datetime.now():%d/%m/%Y} - abre com {plano['regra']}"
        if plano["dias_esperando"]:
            obs += f"; esperou {plano['dias_esperando']} dia(s) uteis por capacidade"
        if not plano["cabe"]:
            obs += f"; ATENCAO: fecha {abs(plano['folga_dias'])} dia(s) depois da entrega"
        valores = {
            "hm": plano["hm"], "setor": plano["setor"], "recurso": plano["recurso"],
            # a coluna Horas guarda a hora CALIBRADA (x1,20); a margem entra so na exibicao
            "horas": round(plano["base"] * plano["fator_calibracao"], 2),
            "inicio": d_br(plano["inicio"]), "fim": d_br(plano["fim"]),
            "prioridade": plano["prioridade"], "obs": obs,
        }
        linha = ws.max_row + 1
        for campo, c in col.items():
            if campo in valores:
                ws.cell(row=linha, column=c, value=valores[campo])
        wb.save(arq)
    except PermissionError:
        return {"ok": False, "erro": "Nao consegui gravar: a planilha cronograma_mestre.xlsx esta aberta no "
                                     "Excel. Feche o arquivo e tente de novo."}
    return {"ok": True, "backup": destino.name, "linhas": 1,
            "detalhe": f"{plano['setor']} da {plano['hm']}: {d_br(plano['inicio'])} a {d_br(plano['fim'])}"}


if __name__ == "__main__":
    import json
    import sys
    hm = sys.argv[1] if len(sys.argv) > 1 else "HM 059"
    entrega = sys.argv[2] if len(sys.argv) > 2 else None
    p = planejar(hm, entrega)
    print(json.dumps({k: v for k, v in p.items() if k not in ("setores", "roteiro_usado")},
                     ensure_ascii=False, indent=2, default=str))
    for s in p.get("setores", []):
        print(f"  {s['setor']:<14}{s['horas']:>8.1f} h  {s['inicio']} -> {s['fim']}  "
              f"{s['dias_uteis']:>3} dias  {s['h_dia']:>6.2f} h/dia de {s['cap_dia']:g}  "
              f"({s['ocupacao_media']}%)  abre com {s['regra']}")
