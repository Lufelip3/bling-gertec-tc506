"""Base local e sincronização incremental de produtos ATIVOS do Bling."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from contextlib import contextmanager
import msvcrt
from conversor import normalizar_ean
from nomes_produtos import ResolverNomes, extrair_id_pai

FUSO = timezone(timedelta(hours=-3))
from configuracao import obter_banco_rede


def pasta_automacao(banco, categoria):
    pasta = Path(banco).parent / 'Automacao' / categoria
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def agora():
    return datetime.now(FUSO).replace(microsecond=0)


def data_api(data):
    return data.astimezone(FUSO).strftime('%Y-%m-%d %H:%M:%S')


@contextmanager
def trava(local):
    local = Path(local)
    local.mkdir(parents=True, exist_ok=True)
    with (local/'execucao.lock').open('a+b') as f:
        if (local/'execucao.lock').stat().st_size == 0:
            f.write(b'0');f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Outra sincronização ou conexão está em andamento. Aguarde terminar.') from None
        try:
            yield
        finally:
            f.seek(0);msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def gravar(path, conteudo):
    from conversor import gravar_atomico
    gravar_atomico(Path(path), conteudo)


def hash_bytes(dados):
    return hashlib.sha256(dados).hexdigest()


def valor_texto(valor):
    return '' if valor is None else str(valor).strip()


def validar(produto):
    nome = valor_texto(produto.get('nome'))
    ean = normalizar_ean(produto.get('gtin'))
    preco = valor_texto(produto.get('preco'))
    if not nome:
        raise ValueError('nome/descrição ausente')
    if any(c in nome for c in '|\r\n\t'):
        raise ValueError('nome contém separador, tabulação ou quebra de linha')
    if not ean or not ean.isascii() or not ean.isdigit():
        raise ValueError('GTIN/EAN ausente ou não numérico')
    if not preco:
        raise ValueError('preço ausente')
    try:
        if ',' in preco:
            preco = preco.replace('.','').replace(',','.')
        numero = Decimal(preco)
        if not numero.is_finite() or numero < 0:
            raise ValueError('preço inválido ou negativo')
        centavos = numero.quantize(Decimal('0.01'))
        if numero != centavos:
            raise ValueError('preço com mais de duas casas decimais')
    except InvalidOperation:
        raise ValueError('preço inválido') from None
    return nome, format(centavos,'.2f').replace('.',','), ean


class Base:
    def __init__(self, local, cliente_id=''):
        self.local = Path(local)
        self.local.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.local/'produtos.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS produtos(
                id TEXT PRIMARY KEY, sku TEXT NOT NULL DEFAULT '', nome TEXT NOT NULL DEFAULT '',
                preco TEXT NOT NULL DEFAULT '', ean TEXT NOT NULL DEFAULT '', ativo INTEGER NOT NULL DEFAULT 0,
                valido INTEGER NOT NULL DEFAULT 0, detalhe INTEGER NOT NULL DEFAULT 0,
                origem TEXT NOT NULL DEFAULT '', assinatura TEXT NOT NULL DEFAULT '',
                atualizado TEXT NOT NULL DEFAULT '');
        ''')
        if 'pendencia' not in {r[1] for r in self.db.execute('PRAGMA table_info(produtos)')}:
            self.db.execute("ALTER TABLE produtos ADD COLUMN pendencia TEXT NOT NULL DEFAULT ''");self.db.commit()
        colunas={r[1] for r in self.db.execute('PRAGMA table_info(produtos)')}
        for nome,tipo in [('ordem_csv','INTEGER NOT NULL DEFAULT 2147483647'),('codigo_pai',"TEXT NOT NULL DEFAULT ''"),('variacao',"TEXT NOT NULL DEFAULT ''"),('dados_recebidos',"TEXT NOT NULL DEFAULT ''"),('id_pai',"TEXT NOT NULL DEFAULT ''")]:
            if nome not in colunas:self.db.execute(f'ALTER TABLE produtos ADD COLUMN {nome} {tipo}')
        self.db.commit()
        self.db.execute('CREATE INDEX IF NOT EXISTS produtos_sku ON produtos(sku)');self.db.commit()
        anterior = self.get('cliente_id')
        if anterior and cliente_id and anterior != cliente_id:
            self.db.close()
            raise RuntimeError('A base local pertence a outro aplicativo Bling. Use a conexão original.')
        if cliente_id and not anterior:
            self.set('cliente_id',cliente_id);self.db.commit()

    def close(self):
        self.db.close()

    def get(self,chave,default=None):
        linha = self.db.execute('SELECT valor FROM meta WHERE chave=?',(chave,)).fetchone()
        return linha[0] if linha else default

    def set(self,chave,valor):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(chave,str(valor)))

    def erro(self, produto, motivo):
        sku = valor_texto(produto.get('codigo')).replace('\r',' ').replace('\n',' ') or '(SKU não informado)'
        return f"ID Bling: {produto.get('id','?')} | SKU: {sku} | Motivo: {motivo}"

    def aplicar(self, produto, erros, detalhe=True, assinatura=''):
        ident = valor_texto(produto.get('id'))
        if not ident or not ident.isdigit():
            raise RuntimeError('Produto sem ID válido no retorno do Bling. Publicação cancelada.')
        anterior = self.db.execute('SELECT * FROM produtos WHERE id=?',(ident,)).fetchone()
        recebido=json.dumps(produto,ensure_ascii=False,default=str)
        sku = valor_texto(produto.get('codigo'))
        self.db.execute('INSERT OR IGNORE INTO produtos(id,sku) VALUES (?,?)',(ident,sku))
        pai_recebido=extrair_id_pai(produto)
        if pai_recebido is not None:
            self.db.execute('UPDATE produtos SET id_pai=? WHERE id=?',(pai_recebido,ident))
            if not pai_recebido:self.db.execute("UPDATE produtos SET codigo_pai='' WHERE id=?",(ident,))
        if produto.get('situacao') != 'A':
            self.db.execute('INSERT OR IGNORE INTO produtos(id,sku) VALUES (?,?)',(ident,sku))
            self.db.execute('UPDATE produtos SET ativo=0, atualizado=?,dados_recebidos=? WHERE id=?',(agora().isoformat(),recebido,ident))
            return
        try:
            nome,preco,ean = validar(produto)
            valido=1
            pendencia=''
        except ValueError as e:
            pendencia=str(e)
            erros.append(self.erro(produto,pendencia))
            nome = anterior['nome'] if anterior else ''
            preco = anterior['preco'] if anterior else ''
            ean = anterior['ean'] if anterior else ''
            valido = anterior['valido'] if anterior else 0
            # Nome/preço inválidos com o mesmo EAN podem preservar a última versão.
            # EAN removido/trocado explicitamente não pode manter a posse do código antigo.
            if anterior and 'gtin' in produto and normalizar_ean(produto.get('gtin'))!=normalizar_ean(anterior['ean']):
                ean='';valido=0

        self.db.execute('''INSERT INTO produtos(id,sku,nome,preco,ean,ativo,valido,detalhe,origem,assinatura,atualizado)
            VALUES (?,?,?,?,?,1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            sku=excluded.sku,nome=excluded.nome,preco=excluded.preco,ean=excluded.ean,ativo=1,
            valido=excluded.valido,detalhe=excluded.detalhe,origem=excluded.origem,
            assinatura=excluded.assinatura,atualizado=excluded.atualizado''',
            (ident,sku,nome,preco,ean,valido,int(detalhe),'api' if detalhe else 'csv',assinatura,agora().isoformat()))
        self.db.execute('UPDATE produtos SET pendencia=?,dados_recebidos=? WHERE id=?',(pendencia,recebido,ident))

    def importar_csv(self, caminho):
        if self.get('watermark'):
            raise RuntimeError('Carga inicial já concluída. A importação CSV é apenas para preparar a primeira carga.')
        dados = Path(caminho).read_bytes()
        try:texto=dados.decode('utf-8-sig')
        except UnicodeDecodeError:texto=dados.decode('cp1252')
        leitor=csv.DictReader(io.StringIO(texto,newline=''),delimiter=';')
        campos={'ID','Código','Descrição','Preço','GTIN/EAN','Situação'}
        if not campos.issubset(leitor.fieldnames or []):
            raise ValueError('CSV precisa conter ID, Código, Descrição, Preço, GTIN/EAN e Situação.')
        erros=[];contagem=0;vistos=set()
        with self.db:
            for r in leitor:
                ident=valor_texto(r.get('ID'))
                if not ident.isdigit() or ident in vistos:
                    erros.append(f"SKU: {r.get('Código','?')} | ID ausente, inválido ou repetido no CSV");continue
                vistos.add(ident)
                if valor_texto(r.get('Situação')).lower() not in ('ativo','a'):continue
                self.aplicar({'id':ident,'codigo':r.get('Código'),'nome':r.get('Descrição'),
                    'preco':r.get('Preço'),'gtin':r.get('GTIN/EAN'),'situacao':'A'},erros,detalhe=False)
                self.db.execute('UPDATE produtos SET codigo_pai=?,variacao=? WHERE id=?',(valor_texto(r.get('Código Pai')),valor_texto(r.get('Produto Variação')),ident))
                contagem+=1
            self.set('csv_importado',str(caminho))
            self.set('csv_referencia',datetime.fromtimestamp(Path(caminho).stat().st_mtime,FUSO).isoformat())
        gravar(self.local/'importacao - erros.log','\r\n'.join(erros) or 'Nenhum erro.')
        return f'{contagem} produtos ativos importados para preparação; {len(erros)} pendências. O TXT não foi alterado.'

    def vincular_pais_csv(self):
        # Fixa o ID do pai encontrado no CSV para sobreviver a alterações futuras de SKU.
        por_sku={}
        for r in self.db.execute('SELECT id,sku FROM produtos'):
            por_sku.setdefault(r['sku'],[]).append(r['id'])
        for r in self.db.execute("SELECT id,codigo_pai FROM produtos WHERE codigo_pai!='' AND id_pai=''").fetchall():
            candidatos=por_sku.get(r['codigo_pai'],[])
            if len(candidatos)==1:
                self.db.execute('UPDATE produtos SET id_pai=? WHERE id=?',(candidatos[0],r['id']))

    def payload(self, erros):
        linhas=[];publicados={}
        todos=[dict(r) for r in self.db.execute('SELECT * FROM produtos ORDER BY ordem_csv,id')]
        nomes=[]
        for r in todos:
            nome=r['nome']
            # Pais sem EAN também fornecem nome; usa os dados recém-recebidos mesmo
            # quando o pai não é publicável por não ter seu próprio código de barras.
            if not r['valido'] and r['dados_recebidos']:
                try:nome=json.loads(r['dados_recebidos']).get('nome') or nome
                except (ValueError,TypeError):pass
            nomes.append(dict(r,nome=nome))
        resolver=ResolverNomes(nomes)
        for r in todos:
            if not r['ativo'] or not r['valido']:continue
            ean=normalizar_ean(r['ean'])
            if ean in publicados:
                erros.append(self.erro({'id':r['id'],'codigo':r['sku']},f"GTIN/EAN repetido {ean}; mantido o primeiro cadastro da base (ID {publicados[ean]})"))
                continue
            try:nome=resolver.nome(r['id'])
            except ValueError as e:
                erros.append(self.erro({'id':r['id'],'codigo':r['sku']},str(e)+'; mantido o nome próprio do cadastro'))
                nome=r['nome']
            publicados[ean]=r['id']
            linhas.append(f"{ean}|{nome}|{r['preco']}")
        return '\r\n'.join(linhas).encode('utf-8')


def assinatura(p):
    return json.dumps([p.get(k) for k in ('codigo','nome','preco','situacao','formato','tipo')],default=str,ensure_ascii=False)


def listar(cliente, filtros, avisar, rotulo):
    pagina=1;vistos=set()
    while True:
        params={'pagina':pagina,'limite':100,'criterio':2,'tipo':'T'};params.update(filtros)
        avisar(f'{rotulo}: página {pagina}, {len(vistos)} registros…')
        itens=cliente.get('/produtos?'+urlencode(params)).get('data')
        if not isinstance(itens,list):raise RuntimeError('Listagem inesperada do Bling. Período não avançado.')
        for p in itens:
            if not isinstance(p,dict) or not p.get('id'):raise RuntimeError('Listagem contém produto sem ID.')
            ident=str(p['id'])
            if ident in vistos:raise RuntimeError('Paginação retornou ID duplicado. Tente novamente; período preservado.')
            vistos.add(ident);yield p
        if len(itens)<100:break
        pagina+=1


def detalhe(cliente, p):
    ident=str(p['id'])
    if not ident.isdigit():raise RuntimeError('ID inválido no catálogo do Bling.')
    d=cliente.get('/produtos/'+ident).get('data')
    if not isinstance(d,dict) or str(d.get('id'))!=ident or d.get('situacao') not in ('A','I','E'):
        raise RuntimeError(f'Detalhes inesperados do produto {ident}. Período preservado.')
    if 'idProdutoPai' not in d and 'idProdutoPai' in p:
        d['idProdutoPai']=p['idProdutoPai']
    return d


def preparar_pais(cliente,base,avisar,erros):
    base.vincular_pais_csv()
    obtidos=set()
    for nivel in range(33):
        conhecidos={r[0] for r in base.db.execute('SELECT id FROM produtos')}
        faltantes={r[0] for r in base.db.execute("SELECT DISTINCT id_pai FROM produtos WHERE ativo=1 AND id_pai!=''") if r[0] not in conhecidos}
        if not faltantes:return
        for ident in sorted(faltantes):
            if ident in obtidos:raise RuntimeError('Vínculo de pai inconsistente; publicação preservada.')
            avisar(f'Obtendo nome do pai ID {ident} para nova variação…')
            base.aplicar(detalhe(cliente,{'id':ident}),erros)
            obtidos.add(ident)
    raise RuntimeError('Profundidade excessiva de vínculos pai/filho.')


def publicar_pendente(base,banco,permitir_reducao=False):
    pendente=base.get('pendente')
    if not pendente:return
    p=json.loads(pendente)
    if str(banco)!=p['banco']:raise RuntimeError('Existe publicação pendente para outro destino.')
    dados=p['texto'].encode('utf-8')
    if not dados.strip():raise RuntimeError('Publicação vazia bloqueada. Confira os produtos e o log de erros.')
    atual=Path(banco).read_bytes() if Path(banco).exists() else b''
    ja_publicado=hash_bytes(atual)==p['novo_hash']
    if not ja_publicado:
        if hash_bytes(atual)!=p['anterior_hash']:raise RuntimeError('O TXT mudou fora do sincronizador durante uma publicação pendente. Banco preservado.')
        antes=len([l for l in atual.splitlines() if l.strip()]);depois=len([l for l in dados.splitlines() if l.strip()])
        if depois==0:raise RuntimeError('Publicação vazia bloqueada. Confira os produtos e o log de erros.')
        if antes and depois<antes*.8 and not permitir_reducao:
            raise RuntimeError(f'Redução de {antes} para {depois} produtos bloqueada. Revise o log e use Publicar redução revisada se estiver correta.')
        if atual:
            pasta=pasta_automacao(banco,'Backups')/'backups_bling';pasta.mkdir(exist_ok=True)
            nome=agora().strftime('%Y%m%d-%H%M%S')+'-'+hash_bytes(atual)[:8]+'.txt';gravar(pasta/nome,atual)
        gravar(banco,dados)
    with base.db:
        base.set('watermark',p['watermark'])
        if p.get('reconciliacao'):base.set('reconciliacao',p['reconciliacao'])
        base.set('ultima_publicacao',agora().isoformat());base.set('ultimo_resumo',p['resumo'])
        base.db.execute("DELETE FROM meta WHERE chave='pendente'")
    pasta=pasta_automacao(banco,'Backups')/'backups_bling'
    if pasta.exists():
        arquivos=sorted(pasta.glob('????????-??????-????????.txt'),key=lambda x:x.name,reverse=True)
        for velho in arquivos[5:]:
            try:velho.unlink()
            except OSError:pass


def sincronizar(cliente,local,banco=None,avisar=lambda t:None,permitir_reducao=False):
    banco=obter_banco_rede(banco)
    if not banco.parent.is_dir():raise RuntimeError('Pasta compartilhada indisponível. Período preservado.')
    base=Base(local,cliente.config.get('client_id',''))
    try:
        if base.get('pendente'):
            publicar_pendente(base,banco,permitir_reducao)
            return 'Publicação pendente concluída. '+base.get('ultimo_resumo','')
        inicio=agora();fim=inicio-timedelta(minutes=2)
        watermark=base.get('watermark');inicial=not watermark
        erros=[];modificados=set();removidos=0
        reconciliar=inicial or not base.get('reconciliacao') or (inicio-datetime.fromisoformat(base.get('reconciliacao')))>timedelta(days=1)
        ativos=set()
        if inicial and not base.get('carga_inicio'):
            base.set('carga_inicio',inicio.isoformat());base.db.commit()
        try:
            if reconciliar:
                for p in listar(cliente,{},avisar,'Carga inicial de ativos' if inicial else 'Conferência diária de ativos'):
                    ident=str(p['id'])
                    if p.get('situacao')!='A':raise RuntimeError('O Bling retornou item não ativo com criterio=2. Publicação bloqueada.')
                    ativos.add(ident)
                    anterior=base.db.execute('SELECT * FROM produtos WHERE id=?',(ident,)).fetchone();sig=assinatura(p)
                    precisa=anterior is None or (not anterior['detalhe'] and anterior['origem']!='csv')
                    if anterior is not None and anterior['origem']=='csv':
                        if not anterior['valido']:precisa=True
                        else:
                            base.aplicar(dict(p,gtin=anterior['ean']),erros,detalhe=True,assinatura=sig);precisa=False
                    elif anterior is not None and (anterior['assinatura']!=sig or not anterior['ativo']):precisa=True
                    if precisa:
                        avisar(f'Obtendo cadastro ativo {len(ativos)} — SKU {p.get("codigo", "?")} (progresso salvo)…')
                        base.aplicar(detalhe(cliente,p),erros,assinatura=sig);modificados.add(ident)
                    if inicial and len(ativos)%20==0:base.db.commit()
                if not ativos:raise RuntimeError('Catálogo ativo vazio. Banco anterior preservado.')
                for r in base.db.execute('SELECT id FROM produtos WHERE ativo=1').fetchall():
                    if r['id'] not in ativos:
                        base.db.execute('UPDATE produtos SET ativo=0 WHERE id=?',(r['id'],));removidos+=1
            if not inicial:inicio_janela=datetime.fromisoformat(watermark)-timedelta(minutes=15)
            elif base.get('csv_importado'):
                referencia=datetime.fromisoformat(base.get('csv_referencia'))
                inicio_janela=min(referencia,datetime.fromisoformat(base.get('carga_inicio')))-timedelta(days=1)
            else:inicio_janela=datetime.fromisoformat(base.get('carga_inicio'))-timedelta(minutes=15)
            consultados_janela=set()
            ponto=inicio_janela
            while ponto<fim:
                limite=min(ponto+timedelta(days=30),fim)
                for prefixo,rotulo in [('dataAlteracao','Produtos ativos alterados'),('dataInclusao','Produtos ativos novos')]:
                    filtros={prefixo+'Inicial':data_api(ponto),prefixo+'Final':data_api(limite)}
                    for p in listar(cliente,filtros,avisar,rotulo):
                        ident=str(p['id'])
                        if p.get('situacao')!='A':raise RuntimeError('Filtro de ativos não respeitado pelo Bling.')
                        if ident in consultados_janela:continue
                        consultados_janela.add(ident)
                        base.aplicar(detalhe(cliente,p),erros,assinatura=assinatura(p));modificados.add(ident)
                # Retira inativações/exclusões recentes sem carregar detalhes nem publicar esses itens.
                for criterio in (3,4):
                    filtros={'criterio':criterio,'dataAlteracaoInicial':data_api(ponto),'dataAlteracaoFinal':data_api(limite)}
                    for p in listar(cliente,filtros,avisar,'Verificando inativações/exclusões'):
                        ident=str(p['id'])
                        if p.get('situacao')=='A':
                            raise RuntimeError('O filtro de inativos/excluídos retornou produto ativo. Período preservado.')
                        r=base.db.execute('UPDATE produtos SET ativo=0 WHERE id=? AND ativo=1',(ident,))
                        removidos+=r.rowcount
                ponto=limite
            from conflitos_ean import confirmar,relatorio as relatorio_conflitos
            eventos_conflitos=confirmar(cliente,base,avisar,erros,modificados)
            preparar_pais(cliente,base,avisar,erros)
            erros_ids={e.split(' |',1)[0] for e in erros}
            for r in base.db.execute('SELECT id,sku,pendencia FROM produtos WHERE ativo=1 AND (valido=0 OR length(pendencia)>0)'):
                if f"ID Bling: {r['id']}" not in erros_ids:
                    erros.append(base.erro({'id':r['id'],'codigo':r['sku']},r['pendencia'] or 'sem versão válida publicável; confira cadastro/GTIN no Bling'))
            dados=base.payload(erros);total=len(dados.splitlines())
            resumo=f'{total} produtos ativos no TXT; {len(modificados)} cadastros consultados; {removidos} inativados/removidos; {len(erros)} pendências.'
            relatorio=f'Sincronização Bling — {inicio.isoformat()}\r\nSomente ativos. Banco: {banco}\r\n{resumo}\r\n\r\n'+('\r\n'.join(erros) or 'Nenhum erro encontrado.')
            gravar(base.local/'produtos - erros.log',relatorio);gravar(pasta_automacao(banco,'Relatorios')/(banco.stem+' - erros.log'),relatorio)
            duplicidades=relatorio_conflitos(base)
            gravar(base.local/'duplicidades_confirmadas.txt',duplicidades)
            gravar(pasta_automacao(banco,'Relatorios')/'duplicidades_confirmadas.txt',duplicidades)
            if eventos_conflitos:
                with (base.local/'conflitos_ean - resolucoes.log').open('a',encoding='utf-8') as f:
                    for evento in eventos_conflitos:f.write(agora().isoformat()+' '+evento+'\n')
            original=banco.read_bytes() if banco.exists() else b'' 
            pendente={'banco':str(banco),'texto':dados.decode('utf-8'),'anterior_hash':hash_bytes(original),
                      'novo_hash':hash_bytes(dados),'watermark':fim.isoformat(),
                      'reconciliacao':inicio.isoformat() if reconciliar else None,'resumo':resumo}
            base.set('pendente',json.dumps(pendente,ensure_ascii=False));base.db.commit()
        except Exception:
            base.db.rollback();raise
        publicar_pendente(base,banco,permitir_reducao)
        return resumo
    finally:base.close()


def status_base(local):
    if not (Path(local)/'produtos.sqlite3').exists():return 'Carga inicial ainda não concluída.'
    base=Base(local)
    try:
        data=base.get('watermark');total=base.db.execute('SELECT COUNT(*) FROM produtos').fetchone()[0]
        if not data:return f'Carga inicial pendente; {total} cadastros já guardados para retomada.'
        idade=agora()-datetime.fromisoformat(data)
        alerta='ATENÇÃO: mais de 2 horas sem sincronizar. ' if idade>timedelta(hours=2) else ''
        return alerta+f'Último período concluído: {data}. '+('Publicação pendente. ' if base.get('pendente') else '')+base.get('ultimo_resumo','')
    finally:base.close()


def testar_filtros(cliente,ident):
    from urllib.parse import urlencode
    ident=str(ident).strip()
    if not ident.isdigit():raise ValueError('Informe um ID numérico do Bling.')
    p=detalhe(cliente,{'id':ident})
    if p['situacao']!='A':raise ValueError('Escolha um produto ativo para o teste.')
    params={'criterio':2,'limite':100,'idsProdutos[]':ident}
    dados=cliente.get('/produtos?'+urlencode(params)).get('data')
    if not isinstance(dados,list) or {str(x.get('id')) for x in dados}!={ident}:
        raise RuntimeError('O filtro por ID/ativos não retornou o produto esperado.')
    futuro=agora()+timedelta(days=3)
    for prefixo in ('dataAlteracao','dataInclusao'):
        parametros=dict(params)
        parametros.update({prefixo+'Inicial':data_api(futuro),prefixo+'Final':data_api(futuro+timedelta(days=1))})
        itens=cliente.get('/produtos?'+urlencode(parametros)).get('data')
        if itens!=[]:raise RuntimeError('O filtro '+prefixo+' não respeitou a janela futura. Não usar o incremental ainda.')
    return 'API confirmou filtros de ativos, ID e datas. Teste de alteração real de preço/nome/EAN ainda depende de uma mudança no Bling.'
