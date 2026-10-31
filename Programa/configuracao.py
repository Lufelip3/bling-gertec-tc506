"""Caminhos da instalação; nenhum arquivo ou ambiente é lido na importação."""
import configparser
import os
from pathlib import Path


_VARIAVEIS = {'banco_rede': 'GERTEC_BANCO_REDE', 'banco_fixo': 'GERTEC_BANCO_FIXO'}


def arquivo_config():
    local = os.environ.get('LOCALAPPDATA')
    pasta = Path(local) if local else Path.home() / 'AppData' / 'Local'
    return pasta / 'GertecBling' / 'config.ini'


def obter_caminho(chave, explicito=None):
    """Precedência: parâmetro, variável de ambiente, seção caminhos do INI."""
    if chave not in _VARIAVEIS:
        raise ValueError('Chave de caminho desconhecida.')
    if explicito is not None:
        valor = str(explicito).strip()
        if not valor:
            raise RuntimeError('Caminho explícito vazio para ' + chave + '.')
        return Path(valor)
    valor = os.environ.get(_VARIAVEIS[chave], '').strip()
    if valor:
        return Path(valor)
    arquivo = arquivo_config()
    config = configparser.ConfigParser(interpolation=None)
    try:
        with arquivo.open('r', encoding='utf-8-sig') as entrada:
            config.read_file(entrada)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, configparser.Error):
        raise RuntimeError('Não foi possível ler config.ini em ' + str(arquivo) +
                           '. Confira o formato e a permissão; chave necessária: caminhos.' + chave + '.') from None
    valor = config.get('caminhos', chave, fallback='').strip()
    if not valor:
        raise RuntimeError('Configuração ausente: caminhos.' + chave + '. Informe em ' +
                           str(arquivo) + ' ou na variável ' + _VARIAVEIS[chave] + '.')
    return Path(valor)


def obter_banco_rede(explicito=None):
    return obter_caminho('banco_rede', explicito)


def obter_banco_fixo(explicito=None):
    return obter_caminho('banco_fixo', explicito)
