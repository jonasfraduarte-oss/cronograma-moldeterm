"""
APONTAMENTOS DO ERP - horas realizadas
======================================
Le a planilha de apontamentos exportada do ERP (pasta dados/) e casa cada
lancamento com uma linha do cronograma: HM + item + setor.

Aceita .xlsx ou .csv. Reconhece os nomes das colunas sozinho; o que nao
reconhecer voce ensina em mapeamento.json.

O que ele devolve para cada linha do cronograma:
  realizado      total de horas apontadas
  ap_inicio/fim  primeiro e ultimo dia apontado (a barra vermelha do Gantt)
  grade_real     horas por dia e por hora (a grade vermelha)
  apont          a lista dos lancamentos

E devolve tambem o diagnostico: colunas reconhecidas, processos que nao
estao no de-para, e lancamentos que nao casaram com nenhuma linha.
"""

import csv
import io
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PASTA_DADOS = RAIZ / "dados"
BASE = RAIZ / "base"
ARQ_MAPEAMENTO = RAIZ / "mapeamento.json"
ARQ_CONFIG = RAIZ / "config.json"


def ler_config():
    """config.json guarda a pasta onde voce salva o export do ERP."""
    if ARQ_CONFIG.exists():
        try:
            return json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def gravar_config(cfg):
    ARQ_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def pasta_configurada():
    p = (ler_config().get("pasta_apontamentos") or "").strip()
    return Path(p) if p else None

ALIASES = {
    "data": ["data", "dia", "data apontamento", "data do apontamento", "dt", "data inicio", "data lancamento"],
    "hora_inicio": ["hora inicio", "hora inicial", "inicio", "hora", "hr inicio", "entrada"],
    "hora_fim": ["hora fim", "hora final", "fim", "hr fim", "saida", "termino"],
    "hm": ["hm", "h m", "os", "ordem", "ordem de servico", "projeto", "pedido", "op", "obra"],
    "item": ["item", "conjunto", "ferramental", "componente", "peca", "descricao", "descricao do item",
             "codigo item", "cod item"],
    "processo": ["processo", "operacao", "atividade", "servico", "etapa", "centro de trabalho", "setor"],
    "recurso": ["recurso", "maquina", "equipamento", "posto", "centro"],
    "operador": ["operador", "colaborador", "funcionario", "responsavel", "matricula", "nome"],
    "horas": ["horas", "horas realizadas", "hs", "tempo", "tempo realizado", "qtd horas", "duracao",
              "horas apontadas"],
}


def norm(t):
    if t is None:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def chave(t):
    """so letras e numeros, para comparar nomes de coluna"""
    return re.sub(r"[^A-Z0-9]+", " ", norm(t)).strip()


def numero(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    t = re.sub(r"[^0-9.\-]", "", t)
    try:
        return float(t)
    except ValueError:
        return 0.0


def para_data(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    t = str(v).strip().split(" ")[0].split("T")[0]
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            continue
    return None


def para_hora(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return f"{v.hour:02d}:{v.minute:02d}"
    t = str(v).strip()
    if " " in t and ":" in t.split(" ")[-1]:
        t = t.split(" ")[-1]
    m = re.match(r"^(\d{1,2})[:h.](\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    if re.match(r"^\d{1,2}$", t):
        return f"{int(t):02d}:00"
    try:
        f = float(t.replace(",", "."))
        if 0 <= f < 1:
            mi = round(f * 24 * 60)
            return f"{mi // 60:02d}:{mi % 60:02d}"
    except ValueError:
        pass
    return None


def carregar_aliases():
    a = {k: list(v) for k, v in ALIASES.items()}
    if ARQ_MAPEAMENTO.exists():
        try:
            extra = json.loads(ARQ_MAPEAMENTO.read_text(encoding="utf-8"))
            campos = extra.get("apontamentos", {})
            if isinstance(campos, dict):
                for campo, nomes in campos.items():
                    if isinstance(nomes, str):
                        nomes = [nomes]
                    a.setdefault(campo, [])
                    a[campo] = list(nomes) + a[campo]
        except Exception:
            pass
    return a


def carregar_de_para():
    mapa = {}
    arq = BASE / "de_para_erp_setor.csv"
    if arq.exists():
        texto = arq.read_text(encoding="utf-8-sig")
        for r in csv.DictReader(texto.splitlines(), delimiter=";"):
            mapa[norm(r["processo_erp"])] = {"setor": (r["setor_cronograma"] or "").strip(),
                                             "origem": r.get("origem", "")}
    return mapa


# ------------------------------------------------------------------ leitura
def achar_arquivo():
    """Procura primeiro na pasta que voce configurou na tela; se nao achar nada
    la, procura na pasta dados. Sempre pega o arquivo mais recente."""
    pastas, erro = [], None
    cfg = pasta_configurada()
    if cfg:
        if cfg.is_dir():
            pastas.append(cfg)
        else:
            erro = f"A pasta configurada nao existe ou nao esta acessivel: {cfg}"
    pastas.append(PASTA_DADOS)

    for pasta in pastas:
        if not pasta.is_dir():
            continue
        candidatos = []
        for p in pasta.iterdir():
            if not p.is_file() or p.suffix.lower() not in (".xlsx", ".csv", ".txt"):
                continue
            if norm(p.stem).startswith("MODELO") or p.name.startswith("~$"):
                continue
            if "APONTAMENT" in norm(p.stem) or "REALIZAD" in norm(p.stem):
                candidatos.append(p)
        if candidatos:
            return max(candidatos, key=lambda p: p.stat().st_mtime), erro
    return None, erro


EXTENSOES_OK = (".xlsx", ".csv", ".txt")


def salvar_upload(nome_original, conteudo):
    """Salva um arquivo de apontamentos enviado manualmente (fase de teste: botao de
    importar ou arrastar-e-soltar) na mesma pasta que achar_arquivo() escaneia primeiro,
    com um nome que ele reconhece. Depois disso o fluxo e o mesmo de sempre."""
    nome = Path(nome_original or "").name
    ext = Path(nome).suffix.lower()
    if ext not in EXTENSOES_OK:
        raise ValueError(f"Extensao nao aceita: {ext or '(nenhuma)'}. Use .xlsx, .csv ou .txt.")
    stem = Path(nome).stem or "apontamentos"
    if "APONTAMENT" not in norm(stem) and "REALIZAD" not in norm(stem):
        nome = f"apontamentos_{stem}{ext}"
    cfg = pasta_configurada()
    pasta = cfg if (cfg and cfg.is_dir()) else PASTA_DADOS
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome
    destino.write_bytes(conteudo)
    return destino


def ler_linhas(caminho):
    if caminho.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        ws = load_workbook(caminho, data_only=True).active
        linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        dados = caminho.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                texto = dados.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            texto = dados.decode("latin-1", errors="replace")
        am = texto[:4000]
        sep = ";" if am.count(";") >= am.count(",") else ","
        if am.count("\t") > am.count(sep):
            sep = "\t"
        linhas = list(csv.reader(io.StringIO(texto), delimiter=sep))
    return [l for l in linhas if any(c is not None and str(c).strip() for c in l)]


def achar_cabecalho(linhas, aliases):
    """A planilha do ERP costuma ter titulo antes do cabecalho. Acha a linha
    que mais parece cabecalho nas 15 primeiras."""
    alvos = {chave(n) for nomes in aliases.values() for n in nomes}
    melhor, pontos_melhor = 0, -1
    for i, l in enumerate(linhas[:15]):
        pontos = sum(1 for c in l if c is not None and chave(c) in alvos)
        if pontos > pontos_melhor:
            melhor, pontos_melhor = i, pontos
    return melhor, pontos_melhor


def mapear_colunas(cab, aliases):
    cabk = [chave(c) for c in cab]
    idx, usados = {}, set()
    for passada in (0, 1):
        for campo, nomes in aliases.items():
            if campo in idx:
                continue
            for nome in nomes:
                alvo = chave(nome)
                for i, c in enumerate(cabk):
                    if i in usados or not c:
                        continue
                    if (c == alvo) if passada == 0 else (alvo in c or c in alvo):
                        idx[campo] = i
                        usados.add(i)
                        break
                if campo in idx:
                    break
    return idx


def ler():
    """Devolve (lancamentos, diagnostico) ou ([], diag) se nao houver arquivo."""
    arq, erro_pasta = achar_arquivo()
    cfg = pasta_configurada()
    base_diag = {"pasta_configurada": str(cfg) if cfg else "", "erro_pasta": erro_pasta or ""}
    if not arq:
        onde = f"na pasta {cfg} nem " if cfg else ""
        return [], {**base_diag, "arquivo": None,
                    "mensagem": f"Nenhuma planilha de apontamentos {onde}na pasta dados. "
                                f"O arquivo precisa ter um nome que comece com 'apontamentos' "
                                f"(.xlsx ou .csv)."}
    aliases = carregar_aliases()
    de_para = carregar_de_para()
    try:
        linhas = ler_linhas(arq)
    except Exception as e:
        return [], {**base_diag, "arquivo": arq.name, "caminho": str(arq),
                    "mensagem": f"Nao consegui abrir o arquivo: {e}"}
    if not linhas:
        return [], {**base_diag, "arquivo": arq.name, "caminho": str(arq),
                    "mensagem": "A planilha esta vazia."}

    i_cab, pontos = achar_cabecalho(linhas, aliases)
    cab, corpo = linhas[i_cab], linhas[i_cab + 1:]
    idx = mapear_colunas(cab, aliases)

    lancs, ignoradas, processos_desconhecidos = [], 0, {}
    for l in corpo:
        g = lambda c: (l[idx[c]] if c in idx and idx[c] < len(l) else None)
        d = para_data(g("data"))
        if not d:
            ignoradas += 1
            continue
        hi, hf = para_hora(g("hora_inicio")), para_hora(g("hora_fim"))
        h = numero(g("horas"))
        if h <= 0 and hi and hf:
            a = int(hi[:2]) * 60 + int(hi[3:])
            b = int(hf[:2]) * 60 + int(hf[3:])
            if b > a:
                h = round((b - a) / 60, 2)
        if h <= 0:
            ignoradas += 1
            continue
        proc = str(g("processo") or "").strip()
        dp = de_para.get(norm(proc))
        setor = dp["setor"] if dp else ""
        if proc and not dp:
            processos_desconhecidos[proc] = processos_desconhecidos.get(proc, 0) + 1
        if setor == "-":
            ignoradas += 1
            continue
        lancs.append({
            "data": d, "hi": hi, "hf": hf, "horas": round(h, 2),
            "hm_erp": str(g("hm") or "").strip(), "item_erp": str(g("item") or "").strip(),
            "processo": proc, "setor_erp": setor,
            "recurso": str(g("recurso") or "").strip(),
            "operador": str(g("operador") or "").strip(),
        })

    from datetime import datetime as _dt
    diag = {
        **base_diag,
        "arquivo": arq.name, "caminho": str(arq),
        "atualizado_em": _dt.fromtimestamp(arq.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
        "exemplo": "EXEMPLO" in norm(arq.stem),
        "linha_cabecalho": i_cab + 1, "linhas_lidas": len(corpo),
        "lancamentos": len(lancs), "ignoradas": ignoradas,
        "colunas_arquivo": [str(c) for c in cab if c is not None and str(c).strip()],
        "colunas_reconhecidas": {k: str(cab[i]) for k, i in idx.items()},
        "colunas_nao_encontradas": [c for c in aliases if c not in idx],
        "processos_desconhecidos": [{"processo": p, "linhas": n}
                                    for p, n in sorted(processos_desconhecidos.items(), key=lambda x: -x[1])],
        "horas_total": round(sum(x["horas"] for x in lancs), 2),
    }
    return lancs, diag


# ------------------------------------------------------------------ casamento
def num_hm(t):
    m = re.search(r"(\d{3})\b", str(t or ""))
    return m.group(1) if m else None


def casar(lancs, atividades):
    """Anexa realizado/grade_real/apont em cada atividade. Devolve os orfaos."""
    for a in atividades:
        a["realizado"] = 0.0
        a["apont"] = []
        a["ap_inicio"] = None
        a["ap_fim"] = None
        a["grade_real"] = []

    # indices
    por_hm = {}
    for a in atividades:
        por_hm.setdefault(num_hm(a["hm"]) or norm(a["hm"]), []).append(a)

    orfaos = []
    for r in lancs:
        chave_hm = num_hm(r["hm_erp"]) or norm(r["hm_erp"])
        candidatas = por_hm.get(chave_hm, [])
        if not candidatas:
            orfaos.append({**r, "motivo": "HM nao encontrada no cronograma"})
            continue

        setor_alvo = norm(r["setor_erp"]) or norm(r["processo"])
        no_setor = [a for a in candidatas if norm(a["setor"]) == setor_alvo]
        if not no_setor:
            orfaos.append({**r, "motivo": f"setor '{r['setor_erp'] or r['processo']}' nao existe nesta HM"})
            continue

        item = norm(r["item_erp"])
        escolhida = None
        if item:
            escolhida = next((a for a in no_setor if norm(a["item_nome"] or a["item"]) == item), None)
            if not escolhida:
                # o ERP costuma trazer so o codigo (CX02B, MD01): procura dentro do nome
                iguais = [a for a in no_setor if item and item in norm(a["item"])]
                if len(iguais) >= 1:
                    escolhida = max(iguais, key=lambda a: a["horas"])
            if not escolhida:
                iguais = [a for a in no_setor if norm(a["item"]) and norm(a["item"]) in item]
                if iguais:
                    escolhida = max(iguais, key=lambda a: a["horas"])
        if not escolhida:
            # sem item ou item nao reconhecido: vai para a linha de maior carga do setor
            escolhida = max(no_setor, key=lambda a: a["horas"])
            if item:
                r = {**r, "aviso": f"item '{r['item_erp']}' nao bateu - lancado no setor"}

        escolhida["realizado"] = round(escolhida["realizado"] + r["horas"], 2)
        escolhida["apont"].append(r)

    # datas e grade do realizado
    for a in atividades:
        if not a["apont"]:
            continue
        a["apont"].sort(key=lambda x: (x["data"], x["hi"] or ""))
        a["ap_inicio"] = a["apont"][0]["data"]
        a["ap_fim"] = max(x["data"] for x in a["apont"])
        por_dia = {}
        for x in a["apont"]:
            cel = por_dia.setdefault(x["data"], {})
            if x["hi"]:
                hora, resta = int(x["hi"][:2]), x["horas"]
                while resta > 0.001 and hora < 24:
                    parte = min(1.0, resta)
                    cel[hora] = round(cel.get(hora, 0) + parte, 3)
                    resta -= parte
                    hora += 1
            else:
                cel["s"] = round(cel.get("s", 0) + x["horas"], 3)
        a["grade_real"] = [{"data": d, "celulas": c} for d, c in sorted(por_dia.items())]
    return orfaos
