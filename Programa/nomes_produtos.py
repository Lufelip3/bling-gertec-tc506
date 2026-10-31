"""Nomes de produtos pai/filho, compartilhados pelo CSV e pela automação."""
import html
from html.parser import HTMLParser
import re


class _TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes=[]
    def handle_data(self,data):self.partes.append(data)
    def handle_starttag(self,tag,attrs):
        if tag.lower() in ('br','p','div','li'):self.partes.append(' ')
    def handle_endtag(self,tag):
        if tag.lower() in ('p','div','li'):self.partes.append(' ')


def limpar_nome(valor):
    nome=html.unescape(str(valor or '')).strip()
    if re.search(r'</?[A-Za-z][^>]*>',nome):
        parser=_TextoHTML();parser.feed(nome);parser.close();nome=''.join(parser.partes)
    return ' '.join(nome.split())


def combinar_nomes(pai,filho):
    pai,filho=limpar_nome(pai),limpar_nome(filho)
    if not pai:return filho
    if not filho:return pai
    # Nome da variação já completo: não acrescentar o pai novamente.
    if filho.casefold().startswith(pai.casefold()):return filho
    return pai+' '+filho


def extrair_id_pai(produto):
    """None significa vínculo não informado; '' significa remoção explícita."""
    valor=produto.get('idProdutoPai') if 'idProdutoPai' in produto else None
    if valor is None:
        v=produto.get('variacao')
        if isinstance(v,dict) and isinstance(v.get('produtoPai'),dict):
            valor=v['produtoPai'].get('id')
    if valor is None and isinstance(produto.get('produtoPai'),dict):
        valor=produto['produtoPai'].get('id')
    if valor is None:return None
    texto=str(valor).strip()
    if texto in ('','0'):return ''
    if not texto.isdigit():raise ValueError('ID do produto pai inválido')
    return texto


class ResolverNomes:
    def __init__(self,produtos):
        self.itens={};self.por_sku={};self.cache={}
        for p in produtos:
            ident=str(p['id']);self.itens[ident]=dict(p,id=ident)
            sku=str(p.get('sku') or '').strip()
            if sku:self.por_sku.setdefault(sku,[]).append(ident)
    def nome(self,ident,trilha=()):
        ident=str(ident)
        if ident in self.cache:return self.cache[ident]
        if ident in trilha or len(trilha)>32:raise ValueError('ciclo ou profundidade excessiva no vínculo pai/filho')
        p=self.itens.get(ident)
        if not p:raise ValueError(f'produto pai ID {ident} não encontrado na base')
        proprio=limpar_nome(p.get('nome'))
        pai=str(p.get('id_pai') or '').strip()
        codigo_pai=str(p.get('codigo_pai') or '').strip()
        if not pai and codigo_pai:
            candidatos=self.por_sku.get(codigo_pai,[])
            if len(candidatos)!=1:raise ValueError(f'Código Pai {codigo_pai} ausente ou ambíguo')
            pai=candidatos[0]
        if pai:
            nome_pai=self.nome(pai,trilha+(ident,))
            if not nome_pai:raise ValueError(f'nome do pai ID {pai} está vazio')
            nome=combinar_nomes(nome_pai,proprio)
        else:nome=proprio
        if '|' in nome:raise ValueError('nome combinado contém o separador |')
        self.cache[ident]=nome
        return nome


def resolver_csv(registros):
    return ResolverNomes([{'id':str(i),'sku':str(r.get('Código') or '').strip(),
        'nome':r.get('Descrição') or '', 'codigo_pai':str(r.get('Código Pai') or '').strip()}
        for i,r in enumerate(registros)])
