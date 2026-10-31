"""Execução sem interface para o Agendador de Tarefas."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
import traceback
import subprocess
import json
import shutil
import time
import bling
from sincronizacao import trava, sincronizar
from configuracao import obter_banco_rede

NOME_TAREFA = 'Gertec Bling - Sincronizacao incremental'


def registrar(texto):
    bling.LOCAL.mkdir(parents=True,exist_ok=True)
    with (bling.LOCAL/'sincronizacao.log').open('a',encoding='utf-8') as f:
        f.write(datetime.now().isoformat(timespec='seconds')+' '+texto+'\n')


def executar():
    try:
        with trava(bling.LOCAL):
            config=bling.carregar_config()
            if not config.get('automatico'):
                return 0
            if not config.get('refresh_token'):
                raise RuntimeError('Conecte ao Bling no painel antes de ativar a sincronização.')
            banco = obter_banco_rede()
            registrar('Início da sincronização automática (somente ativos).')
            ultima=[0]
            def progresso(texto):
                if time.monotonic()-ultima[0]>=15:
                    registrar(texto);ultima[0]=time.monotonic()
            cliente=bling.Cliente(config,progresso)
            resultado=sincronizar(cliente,bling.LOCAL,banco,progresso)
            registrar(resultado)
        return 0
    except RuntimeError as e:
        registrar('Aviso: '+str(e));return 1
    except Exception as e:
        registrar(f'Falha: {type(e).__name__}: {e}');return 1


def instalar_tarefa():
    with trava(bling.LOCAL):
        config=bling.carregar_config()
        if not config.get('refresh_token'):
            raise RuntimeError('Conecte ao Bling primeiro.')
        minutos=int(config.get('intervalo',15))
        if not 1<=minutos<=1440:raise ValueError('Intervalo deve ser de 1 a 1440 minutos.')
        # Logon interativo: mesma identidade que possui o compartilhamento e as chaves DPAPI.
        executavel=Path(sys.executable)
        pythonw=executavel.with_name('pythonw.exe')
        if pythonw.exists():executavel=pythonw
        # Executável local: o processo e seus logs iniciam mesmo se o compartilhamento estiver fora.
        pasta_codigo=bling.LOCAL/'programa';pasta_codigo.mkdir(parents=True,exist_ok=True)
        origem=Path(__file__).resolve().parent
        for nome in ('bling.py','sincronizacao.py','sincronizar_background.py','conversor.py','nomes_produtos.py','conflitos_ean.py','configuracao.py'):
            if (origem/nome).resolve()!=(pasta_codigo/nome).resolve():
                shutil.copy2(origem/nome,pasta_codigo/nome)
        script=pasta_codigo/'sincronizar_background.py' 
        def ps(s):return "'"+str(s).replace("'","''")+"'"
        comando=(
            "$ErrorActionPreference='Stop'; "
            "$identidade=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name; "
            "$acao=New-ScheduledTaskAction -Execute "+ps(executavel)+" -Argument "+ps('"'+str(script)+'"')+"; "
            "$inicio=New-ScheduledTaskTrigger -AtLogOn -User $identidade; "
            "$periodico=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes "+str(minutos)+"); "
            "$principal=New-ScheduledTaskPrincipal -UserId $identidade -LogonType Interactive -RunLevel Limited; "
            "$opcoes=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12); "
            "Register-ScheduledTask -TaskName "+ps(NOME_TAREFA)+" -Action $acao -Trigger @($inicio,$periodico) -Principal $principal -Settings $opcoes -Force | Out-Null"
        )
        r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-Command',comando],capture_output=True,text=True,creationflags=0x08000000)
        if r.returncode:
            raise RuntimeError('Não foi possível registrar a tarefa. '+r.stderr.strip())
        config['automatico']=True
        config['agendado']=True
        bling.salvar_config(config)
    return f'Automação instalada: a cada {minutos} minutos e ao entrar no Windows. O painel pode ser fechado.'


if __name__=='__main__':
    sys.exit(executar())
