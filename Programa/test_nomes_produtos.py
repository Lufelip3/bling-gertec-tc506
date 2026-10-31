# Dados de produtos fictícios, exclusivos dos testes; não usar em produção.
import csv
from pathlib import Path
import tempfile
import unittest
from nomes_produtos import combinar_nomes,ResolverNomes
from sincronizacao import Base,preparar_pais,detalhe
from conversor import converter
from adotar_base import adotar

NOME_PAI='Produto fictício com variações'
NOME_FILHO='Quantidade:500ml'
EAN='9999999999918'


def produto(ident,nome,gtin='',**extra):
    p={'id':ident,'codigo':str(ident),'nome':nome,'preco':'10.00','gtin':gtin,'situacao':'A'}
    p.update(extra);return p


class TesteNomes(unittest.TestCase):
    def test_combinacao_sem_duplicar(self):
        nome=NOME_PAI+' '+NOME_FILHO
        self.assertEqual(combinar_nomes(NOME_PAI,NOME_FILHO),nome)
        self.assertEqual(combinar_nomes(NOME_PAI,nome),nome)
        self.assertEqual(combinar_nomes(NOME_PAI,'<b>'+nome+'</b>'),nome)
        self.assertEqual(combinar_nomes('  Nome &amp; Pai ','<b>Cor:</b> Azul'),'Nome & Pai Cor: Azul')
    def test_existente_csv_e_rename_pai(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);b.aplicar(produto(1,NOME_PAI),[]);b.aplicar(produto(2,NOME_FILHO,EAN),[])
            b.db.execute("UPDATE produtos SET codigo_pai='1' WHERE id='2'");b.vincular_pais_csv()
            self.assertEqual(b.payload([]).decode(),f'{EAN}|{NOME_PAI} {NOME_FILHO}|10,00')
            b.aplicar(produto(1,'Pai renomeado'),[])
            self.assertIn('Pai renomeado Quantidade:500ml',b.payload([]).decode())
            self.assertEqual(b.db.execute("SELECT nome FROM produtos WHERE id='2'").fetchone()[0],NOME_FILHO)
            b.aplicar(produto(2,NOME_FILHO,EAN,preco='12.90'),[])
            self.assertIn('Pai renomeado Quantidade:500ml|12,90',b.payload([]).decode());b.close()
    def test_novo_filho_api_busca_pai_uma_vez(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);b.aplicar(produto(2,NOME_FILHO,EAN,variacao={'produtoPai':{'id':1}}),[])
            b.aplicar(produto(3,'Quantidade:250ml','9999999999925',idProdutoPai=1),[])
            class Cliente:
                def __init__(self):self.chamadas=[]
                def get(self,url):self.chamadas.append(url);return {'data':produto(1,NOME_PAI)}
            c=Cliente();preparar_pais(c,b,lambda t:None,[])
            self.assertEqual(c.chamadas,['/produtos/1'])
            self.assertIn(NOME_PAI+' '+NOME_FILHO,b.payload([]).decode());b.close()
    def test_pai_ja_no_nome_nao_duplica_na_base(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);b.aplicar(produto(1,NOME_PAI),[]);b.aplicar(produto(2,NOME_PAI+' '+NOME_FILHO,EAN,idProdutoPai=1),[])
            self.assertEqual(b.payload([]).decode().count(NOME_PAI),1);b.close()
    def test_vinculo_id_sobrevive_sku_pai_alterado(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);b.aplicar(produto(1,NOME_PAI),[]);b.aplicar(produto(2,NOME_FILHO,EAN),[])
            b.db.execute("UPDATE produtos SET codigo_pai='1' WHERE id='2'");b.vincular_pais_csv()
            b.aplicar(produto(1,'Pai novo',codigo='SKU-NOVO'),[])
            self.assertIn('Pai novo Quantidade:500ml',b.payload([]).decode());b.close()
    def test_pai_ausente_e_ciclo_nao_derrubam_base(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);b.aplicar(produto(2,NOME_FILHO,EAN),[]);b.db.execute("UPDATE produtos SET codigo_pai='inexistente' WHERE id='2'")
            erros=[];self.assertIn(NOME_FILHO,b.payload(erros).decode());self.assertTrue(erros)
            b.db.execute("UPDATE produtos SET codigo_pai='',id_pai='2' WHERE id='2'")
            erros=[];self.assertIn(NOME_FILHO,b.payload(erros).decode());self.assertIn('ciclo',erros[0]);b.close()
    def test_listagem_repassa_id_pai_ao_detalhe(self):
        class C:
            def get(self,url):return {'data':produto(2,NOME_FILHO,EAN)}
        self.assertEqual(detalhe(C(),{'id':2,'idProdutoPai':1})['idProdutoPai'],1)
    def test_conversor_csv_e_adocao(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);src=p/'produtos.csv';dst=p/'Banco.txt'
            h=['']*36
            for i,n in [(0,'ID'),(1,'Código'),(2,'Descrição'),(6,'Preço'),(9,'Situação'),(19,'GTIN/EAN'),(35,'Código Pai')]:h[i]=n
            with src.open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f,delimiter=';');w.writerow(h)
                for ident,nome,ean,pai in [(2,NOME_FILHO,EAN,'1'),(1,NOME_PAI,'','')]:
                    r=['']*36;r[0]=str(ident);r[1]=str(ident);r[2]=nome;r[6]='10,00';r[9]='Ativo';r[19]=ean;r[35]=pai;w.writerow(r)
            resultado=converter(src,dst)
            self.assertEqual(resultado['exportados'],1)
            self.assertEqual(dst.read_text(encoding='utf-8'),f'{EAN}|{NOME_PAI} {NOME_FILHO}|10,00')
            b=Base(p/'local');adotar(b,src,dst)
            self.assertEqual(b.payload([]),dst.read_bytes());b.close()

if __name__=='__main__':unittest.main()
