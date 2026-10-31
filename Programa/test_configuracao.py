"""Configuração isolada: somente arquivos temporários e clientes simulados."""
import ast
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import configuracao as c
import sincronizacao as s
import conversor
from test_sincronizacao import Fake, produto


class TesteConfiguracao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'LOCALAPPDATA': str(self.raiz), 'USERPROFILE': str(self.raiz)}, clear=True).start()
        self.ini = self.raiz / 'GertecBling' / 'config.ini'
        # Qualquer conexão acidental é erro; os clientes válidos são simulados.
        patch('socket.create_connection', side_effect=AssertionError('Rede proibida neste teste')).start()
        patch('http.client.HTTPSConnection.connect', side_effect=AssertionError('Rede proibida neste teste')).start()

    def escrever(self, texto):
        self.ini.parent.mkdir(parents=True, exist_ok=True)
        self.ini.write_text(texto, encoding='utf-8')

    def test_importar_sem_ler_config_ou_producao(self):
        with patch.object(Path, 'open', side_effect=AssertionError('Leitura na importação')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Leitura na importação')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('Leitura na importação')):
            importlib.reload(c)
            importlib.reload(conversor)
            importlib.reload(s)
        self.assertFalse(self.ini.exists())

    def test_config_ausente_mensagem_clara(self):
        for chave, obter in [('banco_rede', c.obter_banco_rede), ('banco_fixo', c.obter_banco_fixo)]:
            with self.subTest(chave=chave), self.assertRaises(RuntimeError) as erro:
                obter()
            self.assertIn('caminhos.' + chave, str(erro.exception))
            self.assertIn(str(self.ini), str(erro.exception))
        self.assertFalse(self.ini.parent.exists())

    def test_leitura_ini_temporario_sem_interpolacao(self):
        rede = self.raiz / 'com%percentual' / 'Banco.txt'
        fixo = self.raiz / 'manual' / 'Banco.txt'
        self.escrever('[caminhos]\nbanco_rede = ' + str(rede) + '\nbanco_fixo = ' + str(fixo))
        self.assertEqual(c.obter_banco_rede(), rede)
        self.assertEqual(c.obter_banco_fixo(), fixo)

    def test_parametro_prevalece_sem_ler_arquivo(self):
        self.escrever('INI propositalmente inválido')
        os.environ['GERTEC_BANCO_REDE'] = str(self.raiz / 'ambiente-rede.txt')
        os.environ['GERTEC_BANCO_FIXO'] = str(self.raiz / 'ambiente-fixo.txt')
        with patch.object(Path, 'open', side_effect=AssertionError('INI não deveria ser lido')):
            self.assertEqual(c.obter_banco_rede(self.raiz / 'explicito.txt'), self.raiz / 'explicito.txt')
            self.assertEqual(c.obter_banco_fixo(self.raiz / 'manual.txt'), self.raiz / 'manual.txt')

    def test_ambiente_prevalece_sobre_ini(self):
        self.escrever('[caminhos]\nbanco_rede = ignorado\nbanco_fixo = ignorado')
        for chave, obter in [('GERTEC_BANCO_REDE', c.obter_banco_rede), ('GERTEC_BANCO_FIXO', c.obter_banco_fixo)]:
            destino = self.raiz / (chave + '.txt')
            os.environ[chave] = str(destino)
            with patch.object(Path, 'open', side_effect=AssertionError('INI não deveria ser lido')):
                self.assertEqual(obter(), destino)

    def test_ambiente_vazio_usa_ini(self):
        destino = self.raiz / 'ini.txt'
        self.escrever('[caminhos]\nbanco_rede = ' + str(destino))
        os.environ['GERTEC_BANCO_REDE'] = '   '
        self.assertEqual(c.obter_banco_rede(), destino)

    def test_ini_invalido_nao_expoe_conteudo(self):
        self.escrever('conteudo-ficticio-que-nao-deve-aparecer')
        with self.assertRaises(RuntimeError) as erro:
            c.obter_banco_rede()
        self.assertIn(str(self.ini), str(erro.exception))
        self.assertNotIn('conteudo-ficticio', str(erro.exception))

    def test_chave_ausente_e_parametro_vazio(self):
        self.escrever('[caminhos]\nbanco_fixo = ' + str(self.raiz / 'manual.txt'))
        with self.assertRaisesRegex(RuntimeError, 'caminhos.banco_rede'):
            c.obter_banco_rede()
        with self.assertRaisesRegex(RuntimeError, 'explícito vazio'):
            c.obter_banco_fixo('')

    def test_falta_config_nao_altera_periodo_pendente_ou_txt(self):
        local = self.raiz / 'estado'
        banco = self.raiz / 'Banco.txt'
        banco.write_bytes(b'conteudo anterior ficticio')
        base = s.Base(local)
        base.set('watermark', '2026-01-01T12:00:00-03:00')
        base.set('pendente', 'intencao ficticia preservada')
        base.db.commit();base.close()
        anterior = (local / 'produtos.sqlite3').read_bytes()
        cliente = Mock()
        with self.assertRaisesRegex(RuntimeError, 'caminhos.banco_rede'):
            s.sincronizar(cliente, local)
        self.assertEqual((local / 'produtos.sqlite3').read_bytes(), anterior)
        self.assertEqual(banco.read_bytes(), b'conteudo anterior ficticio')
        cliente.get.assert_not_called()

    def test_sincronizacao_com_parametro_dispensa_ini(self):
        banco = self.raiz / 'Banco.txt'
        resultado = s.sincronizar(Fake([produto(1)]), self.raiz / 'local', banco)
        self.assertTrue(banco.is_file())
        self.assertIn('1 produtos ativos', resultado)
        self.assertFalse(self.ini.exists())

    def test_agendado_registra_falha_sem_publicar(self):
        import bling
        import sincronizar_background as bg
        local = self.raiz / 'estado'
        base = s.Base(local);base.set('watermark', '2026-01-01T12:00:00-03:00');base.db.commit();base.close()
        antes = (local / 'produtos.sqlite3').read_bytes()
        with patch.object(bling, 'LOCAL', local), \
             patch.object(bling, 'carregar_config', return_value={'automatico': True, 'refresh_token': 'simulado'}), \
             patch.object(bling, 'Cliente', side_effect=AssertionError('Cliente não deveria iniciar')), \
             patch.object(bg, 'sincronizar', side_effect=AssertionError('Publicação não deveria iniciar')):
            self.assertEqual(bg.executar(), 1)
        log = (local / 'sincronizacao.log').read_text(encoding='utf-8')
        self.assertIn('caminhos.banco_rede', log)
        self.assertIn(str(self.ini), log)
        self.assertEqual((local / 'produtos.sqlite3').read_bytes(), antes)

    def test_instalador_inclui_modulo_sem_executar_instalacao(self):
        arvore = ast.parse(Path(__file__).with_name('sincronizar_background.py').read_text(encoding='utf-8'))
        listas = [n.iter for n in ast.walk(arvore) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == 'nome']
        nomes = [ast.literal_eval(n) for n in listas if isinstance(n, ast.Tuple)]
        self.assertTrue(any('configuracao.py' in grupo for grupo in nomes))

    def test_wrapper_cliente_repassa_parametro_sem_resolver_na_importacao(self):
        import bling
        cliente = bling.Cliente({})
        with patch.object(s, 'sincronizar', return_value='simulado') as sincronizar:
            self.assertEqual(cliente.sincronizar(self.raiz / 'destino.txt'), 'simulado')
            self.assertEqual(sincronizar.call_args[0][2], self.raiz / 'destino.txt')
            cliente.sincronizar()
            self.assertIsNone(sincronizar.call_args[0][2])


if __name__ == '__main__':
    unittest.main()
