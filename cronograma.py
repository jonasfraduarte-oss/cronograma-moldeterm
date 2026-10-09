#!/usr/bin/env python3
"""
CRONOGRAMA MOLDETERM - servidor local
=====================================
Monta o cronograma (HM -> item -> setor -> hora a hora) e serve a tela no
navegador. Le tambem os apontamentos do ERP, se houver planilha na pasta dados.

Uso:  python cronograma.py
      python cronograma.py --porta 8080 --sem-navegador
"""

import argparse
import json
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import apontamentos
import mestre
import motor
import planejar

RAIZ = Path(__file__).resolve().parent
PASTA_DADOS = RAIZ / "dados"
PASTA_WEB = RAIZ / "web"


def montar():
    d = mestre.gerar()
    if d.get("erro"):
        return d
    lancs, diag = apontamentos.ler()
    orfaos = apontamentos.casar(lancs, d["atividades"]) if lancs else []
    d["config"] = apontamentos.ler_config()
    d["apontamentos"] = {
        "diagnostico": diag,
        "orfaos": orfaos,
        "total": diag.get("horas_total", 0),
        "lancamentos": diag.get("lancamentos", 0),
        "casados": round(sum(a["realizado"] for a in d["atividades"]), 2),
        "horas_orfas": round(sum(o["horas"] for o in orfaos), 2),
    }
    return d


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _envia(self, corpo, tipo="application/json; charset=utf-8", status=200):
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        c = self.path.split("?")[0]
        if c in ("/", "/index.html"):
            arq = PASTA_WEB / "index.html"
            if not arq.exists():
                return self._envia("<h1>web/index.html nao encontrado</h1>", "text/html; charset=utf-8", 500)
            return self._envia(arq.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if c == "/modelo":
            arq = PASTA_DADOS / "MODELO_apontamentos.xlsx"
            if not arq.exists():
                return self._envia("modelo nao encontrado", "text/plain; charset=utf-8", 404)
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition",
                             'attachment; filename="MODELO_apontamentos.xlsx"')
            dados = arq.read_bytes()
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            return self.wfile.write(dados)
        if c == "/api/novas":
            try:
                return self._envia(json.dumps({"hms": planejar.hms_disponiveis()},
                                              ensure_ascii=False, default=str))
            except Exception as e:
                return self._envia(json.dumps({"erro": str(e)}, ensure_ascii=False), status=500)
        if c in ("/api/dados", "/api/simulacao"):
            try:
                d = montar() if c == "/api/dados" else motor.gerar()
                return self._envia(json.dumps(d, ensure_ascii=False, default=str))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._envia(json.dumps({"erro": str(e)}, ensure_ascii=False), status=500)
        return self._envia("nao encontrado", "text/plain; charset=utf-8", 404)

    def do_POST(self):
        rota = self.path.split("?")[0]
        if rota == "/api/upload-apontamentos":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                dados = self.rfile.read(n)
                nome = self.headers.get("X-Nome-Arquivo") or "apontamentos.xlsx"
                nome = urllib.parse.unquote(nome)
                destino = apontamentos.salvar_upload(nome, dados)
                return self._envia(json.dumps({"ok": True, "arquivo": destino.name,
                                               "caminho": str(destino)}, ensure_ascii=False))
            except Exception as e:
                return self._envia(json.dumps({"ok": False, "erro": str(e)}, ensure_ascii=False), status=400)

        if rota not in ("/api/pasta", "/api/planejar", "/api/gravar",
                        "/api/planejar-setor", "/api/gravar-setor"):
            return self._envia("nao encontrado", "text/plain; charset=utf-8", 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            corpo = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._envia(json.dumps({"ok": False, "erro": str(e)}, ensure_ascii=False), status=400)

        if rota == "/api/planejar":
            try:
                p = planejar.planejar(corpo.get("hm", ""), corpo.get("entrega") or None,
                                      corpo.get("prioridade") or "Alta",
                                      corpo.get("liberacao") or None)
                return self._envia(json.dumps(p, ensure_ascii=False, default=str))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._envia(json.dumps({"erro": str(e)}, ensure_ascii=False), status=500)

        if rota == "/api/gravar":
            try:
                p = planejar.planejar(corpo.get("hm", ""), corpo.get("entrega") or None,
                                      corpo.get("prioridade") or "Alta",
                                      corpo.get("liberacao") or None)
                if p.get("erro"):
                    return self._envia(json.dumps({"ok": False, "erro": p["erro"]}, ensure_ascii=False))
                return self._envia(json.dumps(planejar.gravar_no_mestre(p), ensure_ascii=False))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._envia(json.dumps({"ok": False, "erro": str(e)}, ensure_ascii=False), status=500)

        if rota in ("/api/planejar-setor", "/api/gravar-setor"):
            try:
                # a gravacao recalcula aqui em vez de confiar no que o navegador mandou
                p = planejar.planejar_setor_faltante(corpo.get("hm", ""), corpo.get("setor", ""))
                if rota == "/api/planejar-setor":
                    return self._envia(json.dumps(p, ensure_ascii=False, default=str))
                if p.get("erro"):
                    return self._envia(json.dumps({"ok": False, "erro": p["erro"]}, ensure_ascii=False))
                return self._envia(json.dumps(planejar.gravar_setor_no_mestre(p), ensure_ascii=False))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._envia(json.dumps({"ok": False, "erro": str(e)}, ensure_ascii=False), status=500)

        try:
            pasta = (corpo.get("pasta") or "").strip().strip('"')
            cfg = apontamentos.ler_config()
            cfg["pasta_apontamentos"] = pasta
            apontamentos.gravar_config(cfg)
            existe = bool(pasta) and Path(pasta).is_dir()
            return self._envia(json.dumps({
                "ok": True, "pasta": pasta, "existe": existe,
                "aviso": "" if (existe or not pasta) else
                         "Salvei o endereco, mas nao consegui enxergar essa pasta. "
                         "Confira se o caminho esta certo."}, ensure_ascii=False))
        except Exception as e:
            return self._envia(json.dumps({"ok": False, "erro": str(e)}, ensure_ascii=False), status=500)


def main():
    ap = argparse.ArgumentParser(description="Cronograma MoldeTerm")
    ap.add_argument("--porta", type=int, default=8777)
    ap.add_argument("--sem-navegador", action="store_true")
    args = ap.parse_args()

    PASTA_DADOS.mkdir(exist_ok=True)
    porta = args.porta
    for _ in range(20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
            break
        except OSError:
            porta += 1
    else:
        print("Nao consegui abrir nenhuma porta livre.")
        return 1

    url = f"http://127.0.0.1:{porta}/"
    print("=" * 60)
    print("  CRONOGRAMA MOLDETERM")
    print("=" * 60)
    print(f"  Cadastros : {RAIZ / 'base'}")
    print(f"  Carteira  : {RAIZ / 'pedidos'}")
    print(f"  Endereco  : {url}")
    print("  Encerrar  : feche esta janela ou Ctrl+C")
    print("=" * 60)
    try:
        d = montar()
        if d.get("erro"):
            print(f"  ! {d['erro']}")
        else:
            print(f"  {len(d['hms'])} HMs | {len(d['atividades'])} linhas | "
                  f"{len(d['pendencias'])} pendencias")
            ap_ = d["apontamentos"]["diagnostico"]
            if ap_.get("arquivo"):
                print(f"  Apontamentos: {ap_['arquivo']} | {ap_['lancamentos']} lancamentos | "
                      f"{ap_['horas_total']:g} h")
                if d["apontamentos"]["horas_orfas"]:
                    print(f"  ! {d['apontamentos']['horas_orfas']:g} h nao casaram - ver a aba Apontamentos")
            else:
                print(f"  {ap_.get('mensagem', '')}")
    except Exception as e:
        print(f"  ! erro ao gerar o cronograma: {e}")
    print()

    if not args.sem_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
