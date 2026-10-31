import csv
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import sincronizacao as s


def ean(n):
    corpo=f'{n:012d}'
    dv=(10-sum(int(x)*(3 if i%2==0 else 1) for i,x in enumerate(reversed(corpo))))%10
    return corpo+str(dv)


def produto(n,**mudancas):
    p={'id':n,'codigo':f'SKU{n}','nome':f'Produto {n}','preco':'10.00','gtin':ean(n),'situacao':'A','tipo':'P','formato':'S'}
    p.update(mudancas);return p


class Fake:
    def __init__(self,produtos):
        self.config={'client_id':'teste'};self.catalogo={str(p['id']):p for p in produtos}
        self.alterados=[];self.novos=[];self.chamadas=[];self.falha=None
    def get(self,url):
        self.chamadas.append(url)
        if self.falha and self.falha(url):raise RuntimeError('Falha simulada')
        if url.startswith('/estoques/saldos?'):
            ids=parse_qs(urlsplit(url).query)['idsProdutos[]']
            return {'data':[{'produto':{'id':i},'saldoVirtualTotal':self.catalogo[i].get('saldo_teste',0)} for i in ids]}
        if '?' not in url:return {'data':self.catalogo[url.rsplit('/',1)[1]]}
        q=parse_qs(urlsplit(url).query);criterio=q['criterio'][0]
        if criterio=='2':
            if 'dataAlteracaoInicial' in q:ids=self.alterados
            elif 'dataInclusaoInicial' in q:ids=self.novos
            else:ids=list(self.catalogo)
            ps=[dict(self.catalogo[i]) for i in ids if self.catalogo[i]['situacao']=='A']
        else:
            ps=[dict(p) for p in self.catalogo.values() if p['situacao']==('I' if criterio=='3' else 'E')]
        for p in ps:p.pop('gtin',None)
        inicio=(int(q['pagina'][0])-1)*100
        return {'data':ps[inicio:inicio+100]}


class Teste(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.local=self.p/'local';self.banco=self.p/'Banco.txt'
        self.cliente=Fake([produto(1),produto(2)])
        self.tempo=s.agora();self.clock=patch.object(s,'agora',side_effect=lambda:self.tempo);self.clock.start()
    def tearDown(self):self.clock.stop();self.tmp.cleanup()
    def sync(self,**kwargs):return s.sincronizar(self.cliente,self.local,self.banco,**kwargs)
    def estado(self,chave):
        b=s.Base(self.local)
        try:return b.get(chave)
        finally:b.close()
    def test_inicial_e_sem_mudancas(self):
        self.sync();primeiro=self.banco.read_bytes();self.assertEqual(len(primeiro.splitlines()),2)
        self.cliente.chamadas=[];self.tempo+=timedelta(minutes=15);self.sync()
        self.assertEqual(primeiro,self.banco.read_bytes())
        self.assertFalse(any('?' not in u for u in self.cliente.chamadas))
        self.assertTrue(all('data' in u or u.startswith('/estoques/saldos?') for u in self.cliente.chamadas))
    def test_inclusao_nome_preco_ean_inativacao(self):
        self.sync();self.tempo+=timedelta(minutes=15)
        self.cliente.catalogo['1']=produto(1,nome='Descrição nova',preco='22.90',gtin=ean(100))
        self.cliente.catalogo['2']['situacao']='I'
        self.cliente.catalogo['3']=produto(3)
        self.cliente.alterados=['1','3'];self.cliente.novos=['3']
        self.sync();texto=self.banco.read_text(encoding='utf-8')
        self.assertIn(f'{ean(100)}|Descrição nova|22,90',texto)
        self.assertNotIn(ean(1)+'|',texto);self.assertNotIn(ean(2)+'|',texto);self.assertIn(ean(3)+'|',texto)
        self.assertEqual(sum(u=='/produtos/3' for u in self.cliente.chamadas),1)
    def test_invalido_preserva_produto_inteiro(self):
        self.sync();antes=self.banco.read_bytes();self.tempo+=timedelta(minutes=15)
        self.cliente.catalogo['1']=produto(1,nome='Não aplicar',preco='preço inválido')
        self.cliente.alterados=['1'];self.sync();self.assertEqual(antes,self.banco.read_bytes())
        self.assertIn('SKU1',(self.local/'produtos - erros.log').read_text(encoding='utf-8'))
    def test_falha_na_api_nao_avanca(self):
        self.sync();wm=self.estado('watermark');antes=self.banco.read_bytes();self.tempo+=timedelta(minutes=15)
        self.cliente.falha=lambda u:'dataInclusao' in u
        with self.assertRaises(RuntimeError):self.sync()
        self.assertEqual(wm,self.estado('watermark'));self.assertEqual(antes,self.banco.read_bytes())
    def test_falha_gravacao_e_recuperacao(self):
        self.sync();wm=self.estado('watermark');antes=self.banco.read_bytes();self.tempo+=timedelta(minutes=15)
        self.cliente.catalogo['1']=produto(1,preco='25');self.cliente.alterados=['1']
        gravar=s.gravar
        def falhar(path,dados):
            if Path(path)==self.banco:raise OSError('Rede indisponível')
            return gravar(path,dados)
        with patch.object(s,'gravar',side_effect=falhar):
            with self.assertRaises(OSError):self.sync()
        self.assertEqual(wm,self.estado('watermark'));self.assertTrue(self.estado('pendente'));self.assertEqual(antes,self.banco.read_bytes())
        self.sync();self.assertIsNone(self.estado('pendente'));self.assertIn('|25,00',self.banco.read_text())
        self.assertNotEqual(wm,self.estado('watermark'))
    def test_recupera_se_txt_publicado_antes_confirmacao(self):
        self.sync();self.tempo+=timedelta(minutes=15);self.cliente.catalogo['1']=produto(1,preco='17');self.cliente.alterados=['1']
        publicar=s.publicar_pendente
        def simular(base,banco,permitir_reducao=False):
            p=json.loads(base.get('pendente'));s.gravar(banco,p['texto']);raise OSError('Interrupção após publicação')
        with patch.object(s,'publicar_pendente',side_effect=simular):
            with self.assertRaises(OSError):self.sync()
        self.assertTrue(self.estado('pendente'));self.sync();self.assertIsNone(self.estado('pendente'))
    def test_reducao_exige_revisao(self):
        self.cliente=Fake([produto(i) for i in range(1,11)]);self.sync();antes=self.banco.read_bytes()
        self.tempo+=timedelta(minutes=15)
        for p in self.cliente.catalogo.values():
            if p['id']>1:p['situacao']='I'
        with self.assertRaisesRegex(RuntimeError,'Redução'):self.sync()
        self.assertEqual(antes,self.banco.read_bytes());self.assertTrue(self.estado('pendente'))
        self.sync(permitir_reducao=True);self.assertEqual(len(self.banco.read_bytes().splitlines()),1)
    def test_inativos_nunca_publicados(self):
        self.cliente.catalogo['3']=produto(3,situacao='I');self.sync()
        self.assertNotIn(ean(3).encode(),self.banco.read_bytes())
        for u in self.cliente.chamadas:
            if u.startswith('/produtos?'):self.assertIn(parse_qs(urlsplit(u).query)['criterio'][0],['2','3','4'])
    def test_retomada_carga_inicial(self):
        self.cliente=Fake([produto(i) for i in range(1,26)])
        self.cliente.falha=lambda u:u=='/produtos/23'
        with self.assertRaises(RuntimeError):self.sync()
        b=s.Base(self.local);salvos=b.db.execute('SELECT COUNT(*) FROM produtos').fetchone()[0];b.close()
        self.assertEqual(salvos,20);self.assertFalse(self.banco.exists());self.assertIsNone(self.estado('watermark'))
        self.cliente.falha=None;self.cliente.chamadas=[];self.sync()
        self.assertEqual(len(self.banco.read_bytes().splitlines()),25)
        self.assertNotIn('/produtos/1',self.cliente.chamadas)
    def test_csv_semente_ativos_e_ids(self):
        arquivo=self.p/'produtos.csv'
        with arquivo.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f,delimiter=';');w.writerow(['ID','Código','Descrição','Preço','GTIN/EAN','Situação'])
            w.writerow([1,'SKU1','Antigo','9',ean(1),'Ativo']);w.writerow([2,'SKU2','Inativo','9',ean(2),'Inativo'])
        b=s.Base(self.local,'teste');b.importar_csv(arquivo);b.close();self.sync()
        self.assertNotIn('/produtos/1',self.cliente.chamadas)
        self.assertIn('/produtos/2',self.cliente.chamadas)
        self.assertIn('|Produto 1|10,00',self.banco.read_text(encoding='utf-8'))
    def test_duplicado_mantem_primeiro_cadastro_da_base(self):
        self.cliente.catalogo['3']=produto(3,gtin=ean(1));self.sync()
        self.assertEqual(self.banco.read_bytes().count(ean(1).encode()),1);self.assertIn(ean(2).encode(),self.banco.read_bytes())
        log=(self.local/'produtos - erros.log').read_text(encoding='utf-8');self.assertIn('SKU3',log);self.assertIn('mantido o primeiro',log)
    def test_lock_e_gtin(self):
        with s.trava(self.local):
            with self.assertRaises(RuntimeError):
                with s.trava(self.local):pass
        self.assertEqual(s.validar(produto(1))[2],ean(1))
        self.assertEqual(s.validar(produto(1,gtin='00123'))[2],'00123')
        with self.assertRaises(ValueError):s.validar(produto(1,gtin='SEM GTIN'))
    def test_txt_externo_nao_sobrescrito_em_retomada(self):
        self.sync();self.tempo+=timedelta(minutes=15);self.cliente.catalogo['1']=produto(1,preco=13);self.cliente.alterados=['1']
        with patch.object(s,'publicar_pendente',side_effect=OSError('falha')):
            with self.assertRaises(OSError):self.sync()
        self.banco.write_text('alteracao externa',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'fora do sincronizador'):self.sync()
        self.assertEqual(self.banco.read_text(),'alteracao externa')

if __name__=='__main__':unittest.main(verbosity=2)
