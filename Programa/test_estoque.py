"""Estoque fictício: consultas simuladas e arquivos temporários."""
from pathlib import Path
from datetime import timedelta
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import sincronizacao as s
from test_sincronizacao import Fake, produto


class Estoque(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = self.root / 'local'
        self.txt = self.root / 'Banco.txt'
        self.cliente = Fake([produto(1, saldo_teste=3)])
        self.tempo = s.agora()
        self.clock = patch.object(s, 'agora', side_effect=lambda: self.tempo)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def sync(self):
        return s.sincronizar(self.cliente, self.local, self.txt)

    def test_somente_estoque_sem_alteracao_cadastro(self):
        self.sync()
        self.assertTrue(self.txt.read_text().endswith('|3UN'))
        self.tempo += timedelta(minutes=15)
        self.cliente.catalogo['1']['saldo_teste'] = 0
        self.cliente.chamadas.clear()
        self.sync()
        self.assertTrue(self.txt.read_text().endswith('|0UN'))
        self.assertFalse(any(u.startswith('/produtos/1') for u in self.cliente.chamadas))

    def test_saldo_virtual_nao_soma_depositos_nem_desconta_reserva(self):
        base = s.Base(self.local)
        try:
            base.aplicar(produto(1), [])
            class Cliente:
                def get(self, url):
                    return {'data':[{'produto':{'id':1}, 'saldoFisicoTotal':9, 'saldoVirtualTotal':4,
                                     'depositos':[{'saldoVirtual':50}]}]}
            s.atualizar_estoques(Cliente(), base, lambda t:None)
            self.assertTrue(base.payload([], incluir_estoque=True).endswith(b'|4UN'))
        finally:
            base.close()

    def test_formatacao_fracionada_negativa_zero(self):
        for valor, esperado in [('2.500','2,5'),('-2','-2'),('-0.0','0'),(5,'5')]:
            self.assertEqual(s.formatar_saldo(valor), esperado)
        for valor in [None, True, 'NaN', 'Infinity', 'abc']:
            with self.assertRaises(ValueError): s.formatar_saldo(valor)

    def test_lotes_100_e_ordem_independente(self):
        base = s.Base(self.local)
        try:
            for i in range(1, 206): base.aplicar(produto(i), [])
            lotes = []
            class Cliente:
                def get(self, url):
                    ids = parse_qs(urlsplit(url).query)['idsProdutos[]']
                    lotes.append(len(ids))
                    return {'data':[{'produto':{'id':i},'saldoVirtualTotal':2} for i in reversed(ids)]}
            self.assertEqual(s.atualizar_estoques(Cliente(),base,lambda t:None),205)
            self.assertEqual(lotes,[100,100,5])
        finally: base.close()

    def test_falha_preserva_txt_saldo_e_periodo(self):
        self.sync()
        anterior = self.txt.read_bytes()
        base = s.Base(self.local)
        watermark = base.get('watermark'); base.close()
        self.tempo += timedelta(minutes=15)
        self.cliente.catalogo['1']['saldo_teste'] = 7
        self.cliente.falha = lambda url: url.startswith('/estoques/')
        with self.assertRaisesRegex(RuntimeError,'Controle de Estoque'): self.sync()
        self.assertEqual(self.txt.read_bytes(),anterior)
        base = s.Base(self.local)
        try:
            self.assertEqual(base.get('watermark'),watermark)
            self.assertEqual(base.db.execute('SELECT saldo_venda FROM produtos').fetchone()[0],'3')
        finally: base.close()

    def test_respostas_incompletas_invalidas_duplicadas(self):
        base = s.Base(self.local)
        try:
            base.aplicar(produto(1),[])
            ok = {'produto':{'id':1},'saldoVirtualTotal':2}
            for data in [[],[ok,ok],[{'produto':{'id':9},'saldoVirtualTotal':1}],
                         [{'produto':{'id':1}}],None]:
                with patch.object(self.cliente,'get',return_value={'data':data}):
                    with self.assertRaises(RuntimeError): s.atualizar_estoques(self.cliente,base,lambda t:None)
                self.assertIsNone(base.db.execute('SELECT saldo_venda FROM produtos').fetchone()[0])
        finally: base.close()

    def test_migracao_sem_saldo_nao_inventa_zero(self):
        import sqlite3
        self.local.mkdir()
        with sqlite3.connect(self.local/'produtos.sqlite3') as db:
            db.execute("CREATE TABLE produtos(id TEXT PRIMARY KEY, sku TEXT, nome TEXT, preco TEXT, ean TEXT, ativo INTEGER, valido INTEGER, detalhe INTEGER, origem TEXT, assinatura TEXT, atualizado TEXT)")
        db.close()
        base = s.Base(self.local)
        try:
            base.aplicar(produto(1),[])
            with self.assertRaisesRegex(RuntimeError,'ainda não confirmado'): base.payload([],incluir_estoque=True)
        finally: base.close()

    def test_conversor_preserva_quarto_campo(self):
        import csv
        from conversor import converter
        campos = [''] * 20
        for i,v in {1:'Código',2:'Descrição',6:'Preço',19:'GTIN/EAN'}.items(): campos[i]=v
        linha = [''] * 20
        for i,v in {1:'SKU-FICTICIO',2:'Produto fictício',6:'12,00',19:'9999999999918'}.items(): linha[i]=v
        entrada=self.root/'entrada.csv'
        with entrada.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f,delimiter=';');w.writerow(campos);w.writerow(linha)
        self.txt.write_text('9999999999918|Produto fictício|10,00|7UN',encoding='utf-8')
        converter(entrada,self.txt,atualizar=True)
        self.assertIn('|12,00|7UN',self.txt.read_text(encoding='utf-8'))
