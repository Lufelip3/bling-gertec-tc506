# Dados de produtos fictícios, exclusivos dos testes; não usar em produção.
import csv
from pathlib import Path
import tempfile
import unittest
from conversor import normalizar_ean, converter
from sincronizacao import Base,validar

class TesteEAN(unittest.TestCase):
    def test_zeros_e_tamanhos(self):
        self.assertEqual(normalizar_ean('123456789012'),'0123456789012')
        for codigo in ('0123456789012','9999999999994','12345678','01234567890123','123'):
            self.assertEqual(normalizar_ean(codigo),codigo)
        self.assertEqual(normalizar_ean(normalizar_ean('123456789012')),'0123456789012')
    def test_api_nao_remove_zero_ao_atualizar(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d);p={'id':1,'codigo':'SKU-FICTICIO-A','situacao':'A','nome':'Produto ficticio A','preco':'12.50','gtin':'123456789012'}
            b.aplicar(p,[]);b.db.commit()
            self.assertEqual(b.payload([]),b'0123456789012|Produto ficticio A|12,50')
            p['preco']='11.50';b.aplicar(p,[])
            self.assertEqual(b.payload([]),b'0123456789012|Produto ficticio A|11,50');b.close()
    def test_colisao_12_e_13(self):
        with tempfile.TemporaryDirectory() as d:
            b=Base(d)
            for ident,ean in [(1,'123456789012'),(2,'0123456789012')]:
                b.aplicar({'id':ident,'codigo':str(ident),'situacao':'A','nome':'Produto ficticio A','preco':'12.50','gtin':ean},[])
            erros=[];self.assertEqual(len(b.payload(erros).splitlines()),1);self.assertEqual(len(erros),1);b.close()
    def test_csv_e_atualizacao_base_antiga(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);src=p/'entrada.csv';dst=p/'Banco.txt'
            h=['']*20
            for i,n in [(1,'Código'),(2,'Descrição'),(6,'Preço'),(19,'GTIN/EAN')]:h[i]=n
            with src.open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f,delimiter=';');w.writerow(h)
                for sku,ean in [('SKU-FICTICIO-A','123456789012'),('OUTRO','0123456789012')]:
                    r=['']*20;r[1]=sku;r[2]='Produto ficticio A';r[6]='12,50';r[19]=ean;w.writerow(r)
            resultado=converter(src,dst);self.assertEqual(resultado['ignorados'],1)
            self.assertEqual(dst.read_bytes(),b'0123456789012|Produto ficticio A|12,50')
            dst.write_bytes(b'123456789012|Produto ficticio A|15,50')
            converter(src,dst,atualizar=True)
            self.assertEqual(dst.read_bytes(),b'0123456789012|Produto ficticio A|12,50')

if __name__=='__main__':unittest.main()
