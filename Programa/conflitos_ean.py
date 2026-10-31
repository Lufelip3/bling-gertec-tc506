"""Confirma conflitos de EAN na API e guarda a confirmação separada do log geral."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime,timedelta
from conversor import normalizar_ean


def preparar_tabela(base):
    base.db.execute('''CREATE TABLE IF NOT EXISTS conflitos_ean(
        ean TEXT PRIMARY KEY, assinatura TEXT NOT NULL, estado TEXT NOT NULL,
        conferido_em TEXT NOT NULL, participantes TEXT NOT NULL)''')


def grupos(base):
    resultado=defaultdict(list)
    for r in base.db.execute('SELECT * FROM produtos WHERE ativo=1 AND valido=1 ORDER BY ordem_csv,id'):
        ean=normalizar_ean(r['ean'])
        if ean:resultado[ean].append(dict(r))
    return {k:v for k,v in resultado.items() if len(v)>1}


def fingerprint(itens):
    campos=[]
    for r in sorted(itens,key=lambda p:p['id']):
        try:recebido=json.loads(r['dados_recebidos'] or '{}')
        except ValueError:recebido={}
        campos.append([r['id'],r['sku'],r['nome'],r['preco'],normalizar_ean(r['ean']),r['ativo'],
                       'gtin' in recebido,normalizar_ean(recebido.get('gtin')),r['pendencia']])
    return hashlib.sha256(json.dumps(campos,ensure_ascii=False).encode()).hexdigest()


def guardar(base,ean,itens,estado,quando):
    participantes=[{k:r[k] for k in ('id','sku','nome','preco','ean','ativo','valido')} for r in itens]
    base.db.execute('INSERT OR REPLACE INTO conflitos_ean VALUES (?,?,?,?,?)',
                    (ean,fingerprint(itens),estado,quando.isoformat(),json.dumps(participantes,ensure_ascii=False)))


def confirmar(cliente,base,avisar,erros,consultados=()):
    from sincronizacao import detalhe,assinatura,agora
    preparar_tabela(base)
    recebidos=consultados
    eventos=[];consultados=set(map(str,consultados));quando=agora()
    for rodada in range(16):
        atuais=grupos(base);pendentes=[]
        for ean,itens in atuais.items():
            cache=base.db.execute('SELECT * FROM conflitos_ean WHERE ean=?',(ean,)).fetchone()
            recente=cache and (quando-datetime.fromisoformat(cache['conferido_em']))<timedelta(days=1)
            if not (recente and cache['estado']=='confirmado' and cache['assinatura']==fingerprint(itens)):
                pendentes.append((ean,itens))
        if not pendentes:break
        for ean,itens in pendentes:
            avisar(f'Conferindo conflito do EAN {ean} no Bling…')
            for r in itens:
                ident=r['id']
                if ident not in consultados:
                    d=detalhe(cliente,{'id':ident})
                    if 'gtin' not in d:
                        raise RuntimeError(f'Bling não informou o EAN do produto {ident}; conflito não confirmado. Banco preservado.')
                    base.aplicar(d,erros,assinatura=assinatura(d));consultados.add(ident)
                else:
                    atual=base.db.execute('SELECT dados_recebidos FROM produtos WHERE id=?',(ident,)).fetchone()
                    recebido=json.loads(atual[0] or '{}')
                    if 'gtin' not in recebido:
                        raise RuntimeError(f'Retorno incompleto do produto {ident}; conflito não confirmado.')
                novo=base.db.execute('SELECT * FROM produtos WHERE id=?',(ident,)).fetchone()
                if not novo['ativo'] or not novo['valido'] or normalizar_ean(novo['ean'])!=ean:
                    eventos.append(f"EAN {ean}: vínculo antigo liberado do ID {ident}, SKU {r['sku']}, conforme cadastro atual do Bling.")
            restantes=grupos(base).get(ean,[])
            if len(restantes)>1:
                # Só confirma com dados atuais dos envolvidos e EAN realmente presente na origem.
                for r in restantes:
                    if r['id'] not in consultados:break
                    raw=json.loads(r['dados_recebidos'] or '{}')
                    if normalizar_ean(raw.get('gtin'))!=ean:break
                else:
                    guardar(base,ean,restantes,'confirmado',quando)
                    eventos.append(f'EAN {ean}: duplicidade confirmada entre '+', '.join('SKU '+r['sku'] for r in restantes)+'.')
                    continue
                # Um terceiro cadastro passou a disputar o código; será consultado na próxima passagem.
                continue
            guardar(base,ean,[dict(base.db.execute('SELECT * FROM produtos WHERE id=?',(r['id'],)).fetchone()) for r in itens],'resolvido',quando)
            eventos.append(f'EAN {ean}: conflito resolvido; publicado somente o cadastro que ainda possui o código.')
    else:raise RuntimeError('Conflitos de EAN não estabilizaram; período preservado para nova tentativa.')
    atuais=grupos(base)
    for cache in base.db.execute("SELECT ean FROM conflitos_ean WHERE estado='confirmado'").fetchall():
        if cache['ean'] not in atuais:
            base.db.execute("UPDATE conflitos_ean SET estado='resolvido',conferido_em=? WHERE ean=?",(quando.isoformat(),cache['ean']))
            eventos.append(f"EAN {cache['ean']}: duplicidade anterior deixou de existir na base atual.")
    if isinstance(recebidos,set):recebidos.update(consultados)
    return eventos


def relatorio(base):
    preparar_tabela(base)
    saida=['Duplicidades de EAN confirmadas por consulta ao Bling',
           'A primeira ocorrência da base é mantida no TXT; os demais cadastros continuam registrados aqui.','']
    quantidade=0
    for c in base.db.execute("SELECT * FROM conflitos_ean WHERE estado='confirmado' ORDER BY ean"):
        quantidade+=1;saida.append(f"EAN: {c['ean']} | Confirmado em: {c['conferido_em']}")
        for r in json.loads(c['participantes']):
            saida.append(f"  ID Bling: {r['id']} | SKU: {r['sku']} | Nome: {r['nome']} | Preço: {r['preco']} | EAN: {r['ean']}")
        saida.append('')
    if not quantidade:saida.append('Nenhuma duplicidade confirmada pendente.')
    return '\r\n'.join(saida)
