# Dados de produtos fictícios, exclusivos dos testes; não usar em produção.
from pathlib import Path
import tempfile
import json
from datetime import timedelta
import unittest
from sincronizacao import Base,agora
from conflitos_ean import confirmar,relatorio

EAN='9999999999932'

def produto(ident,gtin=EAN,**extras):
    p={'id':ident,'codigo':str(ident),'nome':'Pai' if ident==1 else 'Quantidade:500ml','preco':'10.00','gtin':gtin,'situacao':'A'}
    if ident==2:p['idProdutoPai']=1
    p.update(extras);return p

class Cliente:
    def __init__(self,produtos):self.itens={str(p['id']):p for p in produtos};self.chamadas=[]
    def get(self,url):
        self.chamadas.append(url)
        r=self.itens[url.rsplit('/',1)[1]]
        if isinstance(r,Exception):raise r
        return {'data':r}

class TesteConflitos(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.b=Base(self.tmp.name)
    def tearDown(self):self.b.close();self.tmp.cleanup()
    def criar_colisao(self):
        self.b.aplicar(produto(1),[]);self.b.aplicar(produto(2),[])
    def test_corrige_ean_removido_do_pai(self):
        self.criar_colisao();c=Cliente([produto(1,gtin=''),produto(2)])
        eventos=confirmar(c,self.b,lambda t:None,[])
        self.assertEqual(self.b.payload([]).decode(),EAN+'|Pai Quantidade:500ml|10,00')
        self.assertEqual(self.b.db.execute("SELECT ean,valido FROM produtos WHERE id='1'").fetchone()[:],('',0))
        self.assertIn('conflito resolvido',' '.join(eventos));self.assertIn('Nenhuma duplicidade',relatorio(self.b))
    def test_duplicidade_real_confirmada_e_cache_sem_reconsultar(self):
        self.criar_colisao();c=Cliente([produto(1),produto(2)])
        confirmar(c,self.b,lambda t:None,[]);self.assertEqual(len(c.chamadas),2)
        txt=relatorio(self.b);self.assertIn('SKU: 1',txt);self.assertIn('SKU: 2',txt);self.assertIn(EAN,txt)
        c.chamadas=[];confirmar(c,self.b,lambda t:None,[]);self.assertEqual(c.chamadas,[])
    def test_reaproveita_consulta_da_rodada(self):
        self.criar_colisao();c=Cliente([produto(1),produto(2)])
        confirmar(c,self.b,lambda t:None,[],consultados={'2'})
        self.assertEqual(c.chamadas,['/produtos/1'])
    def test_ean_vazio_nao_preserva_vinculo_antigo(self):
        self.b.aplicar(produto(1),[]);self.b.aplicar(produto(1,gtin=''),[])
        r=self.b.db.execute("SELECT ean,valido,nome,preco FROM produtos WHERE id='1'").fetchone()
        self.assertEqual(r[:],('',0,'Pai','10,00'))
    def test_erro_preco_com_mesmo_ean_preserva_versao(self):
        self.b.aplicar(produto(1),[]);self.b.aplicar(produto(1,preco='inválido'),[])
        r=self.b.db.execute("SELECT ean,valido,preco FROM produtos WHERE id='1'").fetchone();self.assertEqual(r[:],(EAN,1,'10,00'))
    def test_erro_api_nao_confirma(self):
        self.criar_colisao();c=Cliente([produto(1),produto(2)])
        c.itens['1']=RuntimeError('Sem internet')
        with self.assertRaises(RuntimeError):confirmar(c,self.b,lambda t:None,[])
        self.assertEqual(self.b.db.execute("SELECT COUNT(*) FROM conflitos_ean WHERE estado='confirmado'").fetchone()[0],0)
    def test_resposta_sem_campo_gtin_nao_confirma(self):
        self.criar_colisao();p=produto(1);p.pop('gtin');c=Cliente([p,produto(2)])
        with self.assertRaisesRegex(RuntimeError,'não informou'):confirmar(c,self.b,lambda t:None,[])
    def test_mudanca_invalida_cache_de_confirmacao(self):
        self.criar_colisao();c=Cliente([produto(1),produto(2)])
        confirmar(c,self.b,lambda t:None,[])
        self.b.aplicar(produto(2,preco='199'),[]);c.itens['2']=produto(2,preco='199');c.chamadas=[]
        confirmar(c,self.b,lambda t:None,[],consultados={'2'});self.assertEqual(c.chamadas,['/produtos/1'])
    def test_confirmacao_expira_e_resolucao_some_do_relatorio(self):
        self.criar_colisao();c=Cliente([produto(1),produto(2)])
        confirmar(c,self.b,lambda t:None,[])
        self.b.db.execute('UPDATE conflitos_ean SET conferido_em=?',((agora()-timedelta(days=2)).isoformat(),));c.chamadas=[]
        confirmar(c,self.b,lambda t:None,[]);self.assertEqual(len(c.chamadas),2)
        self.b.aplicar(produto(1,gtin=''),[]);c.chamadas=[]
        confirmar(c,self.b,lambda t:None,[]);self.assertEqual(c.chamadas,[]);self.assertIn('Nenhuma duplicidade',relatorio(self.b))
    def test_ean_12_13_mesmo_conflito(self):
        self.b.aplicar(produto(1,gtin='123456789012'),[]);self.b.aplicar(produto(2,gtin='0123456789012'),[])
        c=Cliente([produto(1,gtin='123456789012'),produto(2,gtin='0123456789012')])
        confirmar(c,self.b,lambda t:None,[]);self.assertIn('EAN: 0123456789012',relatorio(self.b))

if __name__=='__main__':unittest.main()
