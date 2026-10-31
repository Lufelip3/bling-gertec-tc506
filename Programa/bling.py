"""Sincronização local Bling → Gertec. Apenas bibliotecas padrão do Python."""
import base64
import http.client
import ssl
import socket
import csv
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import queue
import secrets
import tempfile
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal
from datetime import datetime
from conversor import converter, gravar_atomico

API = "https://api.bling.com.br/Api/v3"
RETORNO = "http://localhost:8765/callback"
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GertecBling"


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def proteger(dados, abrir=False):
    buffer = ctypes.create_string_buffer(dados)
    origem = Blob(len(dados), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destino = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    func = crypt.CryptUnprotectData if abrir else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(origem), None, None, None, None, 1, ctypes.byref(destino)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destino.pbData, destino.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(destino.pbData, ctypes.c_void_p))


def salvar_config(config):
    LOCAL.mkdir(parents=True, exist_ok=True)
    gravar_atomico(LOCAL / "conexao_v2.dat", proteger(json.dumps(config).encode("utf-8")))


def carregar_config():
    arquivo = LOCAL / "conexao_v2.dat"
    if not arquivo.exists():
        arquivo = LOCAL / "conexao.dat"
    return json.loads(proteger(arquivo.read_bytes(), True)) if arquivo.exists() else {}


class Cliente:
    def __init__(self, config, avisar=lambda texto: None):
        self.config = config
        self.avisar = avisar
        self.ultima = 0
        self.http = None

    def requisitar(self, url, dados=None, headers=None):
        partes=urllib.parse.urlsplit(url)
        if partes.scheme!='https' or partes.hostname!='api.bling.com.br':
            raise RuntimeError('Destino inesperado da API.')
        caminho=partes.path+('?' + partes.query if partes.query else '')
        for tentativa in range(4):
            time.sleep(max(0, .42-(time.monotonic()-self.ultima)))
            self.ultima=time.monotonic()
            try:
                if self.http is None:
                    self.http=http.client.HTTPSConnection(partes.hostname,timeout=40,context=ssl.create_default_context())
                cabecalhos={'Accept':'application/json','User-Agent':'GertecBling/2.0'}
                cabecalhos.update(headers or {})
                self.http.request('POST' if dados is not None else 'GET',caminho,body=dados,headers=cabecalhos)
                resposta=self.http.getresponse()
                corpo=resposta.read()
                if resposta.status<400:
                    return json.loads(corpo,parse_float=Decimal)
                try:limite=json.loads(corpo).get('error',{})
                except Exception:limite={}
                if resposta.status==429 and limite.get('period')=='day':
                    raise RuntimeError('Limite diário do Bling atingido. Tente amanhã.')
                if resposta.status in (429,500,502,503,504) and tentativa<3 and dados is None:
                    try:espera=min(120,max(2**(tentativa+1),float(resposta.getheader('Retry-After','0'))))
                    except ValueError:espera=2**(tentativa+1)
                    time.sleep(espera);continue
                mensagem={400:'Requisição recusada pelo Bling. Confira filtros/cadastro; período preservado.',
                          401:'Autorização vencida ou inválida. Conecte novamente ao Bling.',
                          403:'Acesso negado. Confira escopo Produtos e permissões do usuário.',
                          429:'Limite do Bling atingido. A próxima rodada tentará novamente.'}
                raise RuntimeError(mensagem.get(resposta.status,f'Falha no Bling (HTTP {resposta.status}). Período preservado.'))
            except (http.client.HTTPException,OSError) as erro:
                if self.http:self.http.close()
                self.http=None
                # Não repetir POST de token: o refresh token pode já ter sido rotacionado.
                if dados is None and tentativa<3:
                    time.sleep(2**(tentativa+1));continue
                raise RuntimeError('Falha de comunicação com o Bling. Banco e período anteriores preservados.') from None

    def token(self, parametros):
        cred = base64.b64encode((self.config['client_id'] + ':' + self.config['client_secret']).encode()).decode()
        dados = self.requisitar(API + "/oauth/token", urllib.parse.urlencode(parametros).encode(),
            {"Authorization": "Basic " + cred, "Content-Type": "application/x-www-form-urlencoded", "enable-jwt": "1"})
        if not dados.get("access_token") or not dados.get("refresh_token"):
            raise RuntimeError("O Bling não retornou os tokens esperados. Conecte novamente.")
        self.config.update(access_token=dados['access_token'], refresh_token=dados['refresh_token'],
                           expira=time.time() + int(dados.get('expires_in', 3600)))
        salvar_config(self.config)

    def conectar(self):
        estado = secrets.token_urlsafe(32)
        recebido = {}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Não registrar códigos OAuth em logs.
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                params = urllib.parse.parse_qs(url.query)
                valido = (url.path == '/callback' and
                          secrets.compare_digest(params.get('state', [''])[0], estado))
                if valido:
                    recebido.update(code=params.get('code', [''])[0], erro=params.get('error', [''])[0])
                conteudo = ("Autorizacao recebida. Volte ao programa Gertec." if valido
                            else "Retorno invalido. Continue a autorizacao na janela original.").encode()
                self.send_response(200 if valido else 400)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(conteudo)
        try:
            servidor = HTTPServer(('127.0.0.1', 8765), Handler)
        except OSError:
            raise RuntimeError("A porta 8765 está ocupada. Feche outra janela de conexão e tente novamente.") from None
        with servidor:
            servidor.timeout = 1
            webbrowser.open("https://www.bling.com.br/Api/v3/oauth/authorize?" + urllib.parse.urlencode(
                {"response_type": "code", "client_id": self.config['client_id'], "state": estado}))
            self.avisar("Autorize o acesso na janela do navegador (prazo: 5 minutos).")
            prazo = time.monotonic() + 300
            while not recebido and time.monotonic() < prazo:
                servidor.handle_request()
        if not recebido.get('code'):
            raise RuntimeError("Autorização cancelada ou tempo esgotado. Clique em Conectar novamente.")
        self.token({"grant_type": "authorization_code", "code": recebido['code']})

    def get(self, caminho):
        if not self.config.get('refresh_token'):
            raise RuntimeError("Clique primeiro em Conectar ao Bling.")
        if time.time() >= self.config.get('expira', 0) - 90:
            self.token({"grant_type": "refresh_token", "refresh_token": self.config['refresh_token']})
        return self.requisitar(API + caminho, headers={"Authorization": "Bearer " + self.config['access_token'], "enable-jwt": "1"})

    def sincronizar(self, banco=None, permitir_reducao=False):
        from sincronizacao import sincronizar
        return sincronizar(self, LOCAL, banco, self.avisar, permitir_reducao)


def janela_bling():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from sincronizacao import trava, Base, status_base
    from configuracao import obter_banco_rede
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex_handle = kernel.CreateMutexW(None, False, "Local\\GertecBlingSincronizador")
    repetido = ctypes.get_last_error() == 183
    raiz = tk.Tk()
    if not mutex_handle or repetido:
        raiz.withdraw()
        messagebox.showinfo("Bling automático", "O sincronizador já está aberto. Use a janela existente." if repetido else "Não foi possível iniciar o sincronizador.")
        if mutex_handle:
            kernel.CloseHandle(mutex_handle)
        raiz.destroy()
        return
    raiz.title('Bling → Gertec — Atualização automática')
    raiz.geometry('940x720')
    raiz.minsize(760,640)
    painel = ttk.Frame(raiz, padding=22)
    painel.pack(fill='both', expand=True)
    try:
        config = carregar_config()
    except Exception:
        config = {}
        messagebox.showwarning('Conexão', 'Não foi possível recuperar a conexão salva. Preencha as credenciais e conecte novamente.')
    ttk.Label(painel, text='Atualização automática de preços', font=('Segoe UI',17,'bold')).pack(anchor='w')
    estado_base=tk.StringVar(value='Verificando o andamento da tarefa…')
    ttk.Label(painel,textvariable=estado_base,wraplength=880,foreground='#14532d',font=('Segoe UI',10,'bold')).pack(anchor='w',pady=(4,8))
    ttk.Label(painel, text='Somente produtos ATIVOS • Nome, preço e EAN • Consultas incrementais com base local').pack(anchor='w', pady=8)
    id_var = tk.StringVar(value=config.get('client_id',''))
    secret_var = tk.StringVar(value=config.get('client_secret',''))
    for rotulo, var, mascara in [('Client ID',id_var,''), ('Client Secret',secret_var,'*')]:
        ttk.Label(painel,text=rotulo).pack(anchor='w')
        ttk.Entry(painel,textvariable=var,show=mascara).pack(fill='x',pady=(2,8))
    ttk.Label(painel,text='Redirecionamento cadastrado: ' + RETORNO).pack(anchor='w')
    try:
        banco_rotulo = str(obter_banco_rede())
    except RuntimeError as erro:
        banco_rotulo = str(erro)
    ttk.Label(painel,text='Banco: ' + banco_rotulo,wraplength=880).pack(anchor='w',pady=8)
    intervalo = tk.StringVar(value=str(config.get('intervalo',15)))
    automatico = tk.BooleanVar(value=config.get('automatico',False))
    faixa = ttk.Frame(painel);faixa.pack(fill='x',pady=8)
    ttk.Label(faixa,text='Intervalo (minutos):').pack(side='left')
    ttk.Spinbox(faixa,from_=1,to=1440,textvariable=intervalo,width=7).pack(side='left',padx=8)
    ttk.Checkbutton(faixa,text='Ativar consultas automáticas (painel ou tarefa)' ,variable=automatico).pack(side='left')
    status = tk.StringVar(value='Conexão salva disponível.' if config.get('refresh_token') else 'Preencha as credenciais e conecte ao Bling.')
    mensagens = queue.Queue()
    ocupado = False
    proxima = time.monotonic()
    botoes = []
    log = tk.Text(painel,height=5,wrap='word',state='disabled')
    def registrar(texto):
        status.set(texto)
        log.configure(state='normal');log.insert('end',datetime.now().strftime('%H:%M:%S ') + texto + '\n');log.see('end');log.configure(state='disabled')
        LOCAL.mkdir(parents=True,exist_ok=True)
        with (LOCAL/'sincronizacao.log').open('a',encoding='utf-8') as arquivo:
            arquivo.write(datetime.now().isoformat(timespec='seconds') + ' ' + texto + '\n')
    def iniciar(conectar=False, permitir_reducao=False, importar=None):
        nonlocal ocupado
        if ocupado:
            return
        try:
            minutos = int(intervalo.get())
            if not 1 <= minutos <= 1440:
                raise ValueError()
        except ValueError:
            messagebox.showerror('Intervalo','Informe entre 1 e 1440 minutos.');return
        ident, segredo = id_var.get().strip(), secret_var.get().strip()
        if not ident or not segredo:
            messagebox.showwarning('Credenciais','Preencha Client ID e Client Secret.');return
        nova = dict(config)
        if ident != nova.get('client_id') or segredo != nova.get('client_secret'):
            nova = {}
        auto_escolhido = automatico.get()
        nova.update(client_id=ident,client_secret=segredo,intervalo=minutos,automatico=auto_escolhido)
        ocupado = True
        for b in botoes:b.configure(state='disabled')
        registrar('Conectando…' if conectar else 'Consultando produtos no Bling…')
        def trabalho():
            try:
                with trava(LOCAL):
                    # Recarrega tokens: uma tarefa em segundo plano pode tê-los renovado.
                    atual = carregar_config()
                    if atual.get('client_id') == ident and atual.get('client_secret') == segredo:
                        atual.update(intervalo=minutos,automatico=auto_escolhido)
                        nova.clear();nova.update(atual)
                    salvar_config(nova)
                    cliente = Cliente(nova,lambda msg: mensagens.put(('progresso',msg)))
                    if conectar:
                        cliente.conectar()
                        texto = 'Conectado ao Bling. Clique em Atualizar agora para carregar os ativos.'
                    elif importar:
                        base = Base(LOCAL, ident)
                        try:
                            texto = base.importar_csv(importar)
                        finally:
                            base.close()
                    else:
                        texto = cliente.sincronizar(permitir_reducao=permitir_reducao)
                mensagens.put(('fim',(nova,texto)))
            except Exception as erro:
                # Mensagens controladas não incluem respostas ou tokens.
                texto = str(erro) if isinstance(erro,(RuntimeError,ValueError,OSError)) else f'Falha interna ({type(erro).__name__}). Banco anterior preservado.'
                mensagens.put(('erro',(nova,texto)))
        threading.Thread(target=trabalho,daemon=True).start()
    faixa_botoes = ttk.Frame(painel);faixa_botoes.pack(fill='x',pady=12)
    for texto,acao in [('Conectar ao Bling',lambda:iniciar(True)),('Atualizar agora',iniciar)]:
        b=ttk.Button(faixa_botoes,text=texto,command=acao);b.pack(side='left',padx=(0,10));botoes.append(b)
    ttk.Label(painel,textvariable=status,wraplength=880).pack(anchor='w',pady=8)
    log.pack(fill='both',expand=True)
    ttk.Label(painel,text='Sem tarefa instalada, mantenha o painel aberto. Após instalar a automação, pode fechá-lo. Primeira carga salva progresso; seguintes buscam mudanças. ',wraplength=760).pack(anchor='w',pady=8)
    extras = ttk.Frame(painel);extras.pack(fill='x',pady=6)
    def importar_csv():
        nome = filedialog.askopenfilename(title='Preparar carga inicial com CSV do Bling (opcional)',filetypes=[('CSV','*.csv')])
        if nome: iniciar(importar=nome)
    b=ttk.Button(extras,text='Importar CSV inicial (opcional)',command=importar_csv);b.pack(side='left',padx=(0,8));botoes.append(b)
    def liberar_reducao():
        if messagebox.askyesno('Publicar redução revisada','Use somente após conferir os produtos - erros.log. Publicar a redução superior a 20% que ficou pendente?'):
            iniciar(permitir_reducao=True)
    b=ttk.Button(extras,text='Publicar redução revisada',command=liberar_reducao);b.pack(side='left');botoes.append(b)
    def salvar_preferencias():
        nonlocal config
        minutos=int(intervalo.get())
        if not 1 <= minutos <= 1440:raise ValueError('Informe intervalo entre 1 e 1440 minutos.')
        with trava(LOCAL):
            atual=carregar_config()
            atual.update(intervalo=minutos,automatico=automatico.get())
            salvar_config(atual);config=atual
    def instalar():
        nonlocal config
        if ocupado:return
        try:
            salvar_preferencias()
            from sincronizar_background import instalar_tarefa
            texto=instalar_tarefa()
            config=carregar_config()
            automatico.set(True)
            registrar(texto)
        except Exception as e:messagebox.showerror('Automação',str(e))
    b=ttk.Button(extras,text='Instalar/atualizar automação',command=instalar);b.pack(side='left',padx=8);botoes.append(b)
    def salvar():
        if ocupado:
            messagebox.showinfo('Operação em andamento','Aguarde a operação terminar para salvar preferências.');return
        try:salvar_preferencias();registrar('Preferências salvas. Reinstale a tarefa se mudou o intervalo.')
        except Exception as e:messagebox.showerror('Preferências',str(e))
    ttk.Button(painel,text='Salvar preferências / Pausa automática',command=salvar).pack(anchor='w',pady=4)
    ultima_leitura=0
    def ciclo():
        nonlocal ocupado, proxima, config, ultima_leitura
        try:
            while True:
                tipo,dados=mensagens.get_nowait()
                if tipo=='progresso':
                    status.set(dados)
                else:
                    config,texto=dados
                    ocupado=False
                    for b in botoes:b.configure(state='normal')
                    registrar(('Erro: ' if tipo=='erro' else '')+texto)
                    try:minutos=max(1,int(intervalo.get()))
                    except ValueError:minutos=15
                    proxima=time.monotonic()+minutos*60
        except queue.Empty:
            pass
        if automatico.get() and not config.get('agendado') and not ocupado and config.get('refresh_token') and time.monotonic()>=proxima:
            iniciar()
        if time.monotonic()-ultima_leitura>15 and not ocupado:
            try:
                estado_base.set(status_base(LOCAL))
                arquivo=LOCAL/'sincronizacao.log'
                if arquivo.exists():
                    with arquivo.open('rb') as f:
                        f.seek(0,2);tamanho=f.tell();f.seek(max(0,tamanho-18000))
                        texto=f.read().decode('utf-8',errors='replace')
                    linhas=texto.splitlines()[-100:]
                    log.configure(state='normal');log.delete('1.0','end');log.insert('end','\n'.join(linhas));log.see('end');log.configure(state='disabled')
            except Exception:estado_base.set('Estado indisponível; sincronização pode estar em andamento.')
            ultima_leitura=time.monotonic()
        raiz.after(500,ciclo)
    def fechar():
        if ocupado:
            messagebox.showinfo('Operação em andamento','Aguarde a operação terminar antes de fechar.');return
        try:
            if automatico.get()!=config.get('automatico',False) or int(intervalo.get())!=int(config.get('intervalo',15)):
                salvar_preferencias()
        except Exception as e:
            messagebox.showerror('Preferências não salvas',str(e))
            return
        raiz.destroy()
    raiz.protocol('WM_DELETE_WINDOW',fechar)
    raiz.after(500,ciclo)
    try:
        raiz.mainloop()
    finally:
        kernel.CloseHandle(mutex_handle)


if __name__=='__main__':
    janela_bling()
