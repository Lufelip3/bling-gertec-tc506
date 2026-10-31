"""Conversor local de produtos Bling para o banco TXT Gertec."""
import argparse
import csv
import io
import os
from pathlib import Path
from decimal import Decimal, InvalidOperation
import tempfile
from datetime import datetime
from nomes_produtos import resolver_csv


from configuracao import obter_banco_fixo


def normalizar_ean(valor):
    """Publica UPC de 12 dígitos como EAN de 13, conforme leitura do TC506."""
    codigo = str(valor).strip() if valor is not None else ''
    if len(codigo) == 12 and codigo.isascii() and codigo.isdigit():
        return '0' + codigo
    return codigo


def converter(entrada, saida, atualizar=False):
    entrada, saida = Path(entrada), Path(saida)
    if entrada.resolve() == saida.resolve():
        raise ValueError("Escolha uma saída diferente do CSV original.")
    log = (saida.parent / "Automacao" / "Relatorios" / (saida.stem + " - erros.log")) if atualizar else saida.with_name(saida.stem + " - erros.log")
    if atualizar:
        log.parent.mkdir(parents=True, exist_ok=True)
    if log.resolve() == entrada.resolve():
        raise ValueError("O caminho do log coincide com o CSV original.")
    dados = entrada.read_bytes()
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("cp1252")
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=";", strict=True)
    cabecalho = next(leitor, [])
    esperadas = {1: "Código", 2: "Descrição", 6: "Preço", 19: "GTIN/EAN"}
    for indice, nome in esperadas.items():
        if len(cabecalho) <= indice or cabecalho[indice].strip() != nome:
            raise ValueError(f"Coluna {indice + 1}: esperado '{nome}'. Use a exportação de produtos do Bling.")
    registros=list(leitor)
    resolver=resolver_csv([dict(zip(cabecalho,r)) for r in registros])
    linhas, erros, codigos = [], [], {}
    referencias = {}
    for numero, registro in enumerate(registros, 2):
        if not registro or not any(c.strip() for c in registro):
            continue
        sku = registro[1].strip() if len(registro) > 1 else ""
        sku = sku.replace("\r", " ").replace("\n", " ").replace("\t", " ") or "(SKU não informado)"
        try:
            if len(registro) != len(cabecalho):
                raise ValueError("quantidade de colunas diferente do cabeçalho")
            descricao, valor, ean = registro[2].strip(), registro[6].strip(), registro[19].strip()
            if not descricao:
                raise ValueError("descrição vazia")
            descricao = resolver.nome(str(numero-2))
            if not descricao:
                raise ValueError("descrição vazia")
            if any(c in descricao for c in "|\r\n\t"):
                raise ValueError("descrição contém |, quebra de linha ou tabulação; corrija o cadastro")
            if not ean or not ean.isascii() or not ean.isdigit():
                raise ValueError("GTIN/EAN ausente ou não numérico")
            ean = normalizar_ean(ean)
            if ean in codigos:
                raise ValueError(f"GTIN/EAN repetido (também no registro {codigos[ean]})")
            if not valor:
                raise ValueError("preço vazio")
            # O Bling exporta vírgula decimal; aceita também ponto decimal.
            if "," in valor:
                valor = valor.replace(".", "").replace(",", ".")
            try:
                preco = Decimal(valor)
            except InvalidOperation:
                raise ValueError("preço inválido")
            if not preco.is_finite() or preco < 0:
                raise ValueError("preço inválido ou negativo")
            if preco != preco.quantize(Decimal("0.01")):
                raise ValueError("preço tem mais de duas casas decimais")
            codigos[ean] = numero
            referencias[ean] = (numero, sku)
            linhas.append(f"{ean}|{descricao}|" + format(preco, ".2f").replace(".", ","))
        except (ValueError, InvalidOperation) as erro:
            erros.append(f"Registro {numero} | SKU: {sku} | Motivo: {erro}")
    alterados = iguais = nao_encontrados = 0
    original = None
    if atualizar:
        original = saida.read_bytes()
        base = original.decode("utf-8-sig").splitlines()
        indice = {}
        for numero_base, linha in enumerate(base, 1):
            if not linha.strip():
                continue
            campos = linha.split("|")
            if len(campos) not in (3, 4) or not campos[0].strip():
                raise ValueError(f"Banco inválido na linha {numero_base}. Nenhuma atualização realizada.")
            ean_base = normalizar_ean(campos[0])
            if ean_base in indice:
                raise ValueError(f"Código de barras repetido no banco: {ean_base}. Nenhuma atualização realizada.")
            indice[ean_base] = numero_base - 1
        for linha in linhas:
            ean, descricao, preco = linha.split("|")
            if ean not in indice:
                numero, sku = referencias[ean]
                erros.append(f"Registro {numero} | SKU: {sku} | Motivo: GTIN/EAN {ean} não encontrado no banco; produto não incluído")
                nao_encontrados += 1
                continue
            i = indice[ean]
            campos = base[i].split("|")
            if campos[2] == preco and campos[0] == ean:
                iguais += 1
            else:
                campos[0] = ean
                campos[2] = preco
                base[i] = "|".join(campos)
                alterados += 1
        linhas = base
    resumo = (f"Preços atualizados: {alterados} | Já iguais: {iguais} | Ignorados: {len(erros)}"
              if atualizar else f"Exportados: {len(linhas)} | Ignorados: {len(erros)}")
    relatorio = (
        f"Conversão de produtos — {datetime.now():%d/%m/%Y %H:%M:%S}\r\n"
        f"CSV: {entrada}\r\nBanco: {saida}\r\n"
        f"{resumo}\r\n\r\n"
        + ("\r\n".join(erros) if erros else "Nenhum erro encontrado.")
    )
    gravar_atomico(log, relatorio)
    if not atualizar or alterados:
        if atualizar:
            if saida.read_bytes() != original:
                raise ValueError("O banco mudou durante a leitura. Execute a atualização novamente.")
            pasta_backup = saida.parent / "Automacao" / "Backups"
            pasta_backup.mkdir(parents=True, exist_ok=True)
            gravar_atomico(pasta_backup / (saida.name + ".bak"), original)
        gravar_atomico(saida, "\r\n".join(linhas))
    return {"exportados": len(linhas), "ignorados": len(erros), "log": log, "atualizados": alterados, "iguais": iguais}


def gravar_atomico(saida, texto):
    # Grava por inteiro antes de substituir o destino, preservando o banco em caso de falha.
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(dir=saida.parent, delete=False, suffix=".tmp") as arquivo:
            temporario = Path(arquivo.name)
            arquivo.write(texto if isinstance(texto, bytes) else texto.encode("utf-8"))
        os.replace(temporario, saida)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()


def janela():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    raiz = tk.Tk()
    raiz.title("Conversor de preços — Gertec TC506")
    raiz.geometry("800x460")
    raiz.minsize(700, 360)
    painel = ttk.Frame(raiz, padding=24)
    painel.pack(fill="both", expand=True)
    ttk.Label(painel, text="CSV do Bling → Banco de dados.txt", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(painel, text="Colunas C, G e T • Produtos incompletos são ignorados", padding=(0, 10)).pack(anchor="w")
    caminho = tk.StringVar()
    ttk.Entry(painel, textvariable=caminho).pack(fill="x", pady=8)
    def selecionar():
        nome = filedialog.askopenfilename(title="Selecionar CSV de produtos", filetypes=[("Produtos CSV", "*.csv")])
        if nome:
            caminho.set(nome)
    ttk.Button(painel, text="Selecionar CSV…", command=selecionar).pack(anchor="w")
    status = tk.StringVar(value="Saída: GTIN/EAN | Descrição | Preço")
    def executar():
        if not caminho.get():
            messagebox.showwarning("Selecione o CSV", "Selecione o arquivo de produtos primeiro.")
            return
        destino = filedialog.asksaveasfilename(title="Salvar banco de dados", initialdir=str(Path(caminho.get()).parent), initialfile="Banco de dados.txt", defaultextension=".txt", filetypes=[("Banco TXT", "*.txt")])
        if not destino:
            return
        try:
            resultado = converter(caminho.get(), destino)
        except Exception as erro:
            messagebox.showerror("Não foi possível converter", str(erro))
            status.set("Conversão não concluída. Confira os dados e tente novamente.")
        else:
            resumo = f"{resultado['exportados']} exportado(s), {resultado['ignorados']} ignorado(s)."
            status.set(resumo)
            mensagem = f"{resumo}\n\nBanco: {destino}\n\nLog de erros: {resultado['log']}"
            if not resultado['exportados']:
                mensagem += "\n\nO banco está vazio: nenhum produto válido encontrado."
            messagebox.showinfo("Conversão concluída", mensagem)
    ttk.Button(painel, text="Gerar banco de dados…", command=executar).pack(anchor="w", pady=12)
    def atualizar_precos():
        if not caminho.get():
            messagebox.showwarning("Selecione o CSV", "Selecione o arquivo de produtos primeiro.")
            return
        try:
            destino = obter_banco_fixo()
            resultado = converter(caminho.get(), destino, atualizar=True)
        except Exception as erro:
            status.set("Atualização não concluída.")
            messagebox.showerror("Não foi possível atualizar", str(erro))
        else:
            resumo = (f"{resultado['atualizados']} preço(s) atualizado(s), "
                      f"{resultado['iguais']} já iguais, {resultado['ignorados']} ignorado(s).")
            status.set(resumo)
            messagebox.showinfo("Atualização concluída", f"{resumo}\n\nBanco: {destino}\n\nLog: {resultado['log']}")
    ttk.Button(painel, text="Atualizar preços", command=atualizar_precos).pack(anchor="w", pady=(0, 8))
    try:
        banco_rotulo = str(obter_banco_fixo())
    except RuntimeError as erro:
        banco_rotulo = str(erro)
    ttk.Label(painel, text=banco_rotulo, wraplength=720).pack(anchor="w", pady=(0, 8))
    ttk.Label(painel, textvariable=status).pack(anchor="w")
    def abrir_bling():
        import subprocess
        import sys
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("Bling automatico.pyw"))])
    ttk.Button(painel, text="Bling automático…", command=abrir_bling).pack(anchor="w", pady=8)
    raiz.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?")
    parser.add_argument("-o", "--saida", default="Banco de dados.txt")
    argumentos = parser.parse_args()
    if argumentos.csv:
        resultado = converter(argumentos.csv, argumentos.saida)
        print(f"{resultado['exportados']} exportado(s), {resultado['ignorados']} ignorado(s). Log: {resultado['log']}")
    else:
        janela()
