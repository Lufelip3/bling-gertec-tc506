"""Adota CSV completo + TXT aprovado, sem consultar a API nem publicar automaticamente."""
import csv
import io
import json
from pathlib import Path
from datetime import datetime
from nomes_produtos import resolver_csv
from sincronizacao import validar, valor_texto, agora, FUSO, hash_bytes, gravar


def adotar(base, caminho_csv, caminho_txt):
    caminho_csv, caminho_txt = Path(caminho_csv), Path(caminho_txt)
    if base.get('pendente'):
        raise RuntimeError('Existe publicação pendente. Resolva-a antes de adotar uma base manual.')
    dados_csv=caminho_csv.read_bytes();dados_txt=caminho_txt.read_bytes()
    try:texto=dados_csv.decode('utf-8-sig')
    except UnicodeDecodeError:texto=dados_csv.decode('cp1252')
    leitor=csv.DictReader(io.StringIO(texto,newline=''),delimiter=';',strict=True)
    exigidos={'ID','Código','Descrição','Preço','GTIN/EAN','Situação'}
    if not exigidos.issubset(leitor.fieldnames or []):raise ValueError('CSV não possui os campos necessários.')
    entradas=list(leitor);resolver=resolver_csv(entradas)
    registros=[];ids=set();ean_publicados={};linhas=[];erros=[]
    for ordem,r in enumerate(entradas,1):
        ident=valor_texto(r.get('ID'));sku=valor_texto(r.get('Código'))
        if None in r or not ident.isdigit() or ident in ids:
            raise ValueError(f'CSV inconsistente no registro {ordem+1}: ID ausente/repetido ou colunas inválidas.')
        ids.add(ident)
        ativo=valor_texto(r.get('Situação')).lower() in ('ativo','a')
        p={'id':ident,'codigo':sku,'nome':r.get('Descrição'),'preco':r.get('Preço'),'gtin':r.get('GTIN/EAN'),'situacao':'A' if ativo else 'I'}
        pendencia='';valido=0
        try:
            nome,preco,ean=validar(p);nome_publicado=resolver.nome(str(ordem-1));valido=1
        except ValueError as e:
            nome=valor_texto(p['nome']);preco=valor_texto(p['preco']);ean=valor_texto(p['gtin']);pendencia=str(e)
            if ativo:erros.append(base.erro(p,pendencia))
        if ativo and valido:
            if ean not in ean_publicados:
                ean_publicados[ean]=ident;linhas.append(f'{ean}|{nome_publicado}|{preco}')
            else:erros.append(base.erro(p,f'GTIN/EAN repetido {ean}; mantido o primeiro ID {ean_publicados[ean]} do CSV'))
        registros.append((ident,sku,nome,preco,ean,int(ativo),valido,pendencia,ordem,
                          valor_texto(r.get('Código Pai')),valor_texto(r.get('Produto Variação')),json.dumps(p,ensure_ascii=False)))
    esperado='\r\n'.join(linhas).encode('utf-8')
    txt_normalizado='\r\n'.join(dados_txt.decode('utf-8-sig').splitlines()).encode('utf-8')
    if not registros or not linhas:raise ValueError('Base vazia ou sem produtos publicáveis; adoção cancelada.')
    if esperado!=txt_normalizado:
        raise ValueError('O TXT aprovado não corresponde ao CSV usando a mesma seleção de registros. Nenhuma alteração realizada.')
    antes=base.get('watermark')
    referencias=[datetime.fromtimestamp(caminho_csv.stat().st_mtime,FUSO),datetime.fromtimestamp(caminho_txt.stat().st_mtime,FUSO),agora()]
    if antes:referencias.append(datetime.fromisoformat(antes))
    retomar=min(referencias)
    with base.db:
        base.db.execute('UPDATE produtos SET ativo=0')
        for ident,sku,nome,preco,ean,ativo,valido,pendencia,ordem,pai,variacao,raw in registros:
            # Preserva a assinatura já obtida da API para evitar uma nova leitura completa de detalhes.
            base.db.execute('''INSERT INTO produtos(id,sku,nome,preco,ean,ativo,valido,detalhe,origem,atualizado,pendencia,
                ordem_csv,codigo_pai,variacao,dados_recebidos) VALUES (?,?,?,?,?,?,?,1,'base_csv',?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET sku=excluded.sku,nome=excluded.nome,preco=excluded.preco,ean=excluded.ean,
                ativo=excluded.ativo,valido=excluded.valido,detalhe=1,origem='base_csv',atualizado=excluded.atualizado,
                pendencia=excluded.pendencia,ordem_csv=excluded.ordem_csv,codigo_pai=excluded.codigo_pai,
                variacao=excluded.variacao,dados_recebidos=excluded.dados_recebidos''',
                (ident,sku,nome,preco,ean,ativo,valido,agora().isoformat(),pendencia,ordem,pai,variacao,raw))
        base.vincular_pais_csv()
        preview=base.payload([])
        if preview!=esperado:raise ValueError('A base interna não reproduziu o TXT aprovado; alterações revertidas.')
        base.set('watermark',retomar.isoformat())
        base.set('base_adotada',str(caminho_txt))
        base.set('base_adotada_hash',hash_bytes(dados_txt))
        base.set('base_adotada_csv_hash',hash_bytes(dados_csv))
        base.set('base_adotada_em',agora().isoformat())
        base.set('ultimo_resumo',f'{len(linhas)} produtos publicados da base CSV aprovada; {len(erros)} registros ignorados.')
    relatorio=f'Base CSV adotada em {agora().isoformat()}\r\nCSV: {caminho_csv}\r\nTXT aprovado: {caminho_txt}\r\n{len(registros)} cadastros; {len(linhas)} produtos no TXT; {len(erros)} ignorados.\r\n\r\n'+'\r\n'.join(erros)
    gravar(base.local/'adocao CSV - erros.log',relatorio)
    return {'cadastros':len(registros),'publicados':len(linhas),'ignorados':len(erros),'hash':hash_bytes(dados_txt),'cursor':retomar.isoformat()}
