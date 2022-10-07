import asyncio
from collections import deque
from datetime import datetime, timedelta
import os
import re
import zmq
import threading
from time import sleep
from pyrogram import Client, filters, compose
import logging

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
pyro = logging.getLogger("pyrogram")
pyro.setLevel(logging.CRITICAL)

client = Client(
    'telegram',
    os.getenv('API_ID'),
    os.getenv('API_HASH')
    )
bot = Client(
    "bot",
    os.getenv('API_ID'),
    os.getenv('API_HASH'),
    bot_token=os.getenv("BOT_TOKEN")
    )
client_list = [client, bot]
logging.debug("Cliente e Bot pyrogram instanciado")

class Listenner:
    def __init__(self, host:str="tcp://localhost:5555") -> None:
        # Get zmq context
        try:
            self.context = zmq.Context()

            # Create REQ Socket
            self.reqSocket = self.context.socket(zmq.REQ)
            self.reqSocket.connect(host)
        except Exception as e:
            logging.error("Erro na conexão com o Metatarder 4")
        
    def send(self, data:str)->str:
        try:
            self.reqSocket.send_string(data)
            msg = self.reqSocket.recv_string()
            logging.info("Mensagem recebida do Metatrader 4")
            return (msg)
        except zmq.Again as e:
            logging.warning("Waiting for PUSH from MetaTrader 4..")
        


TIMEFRAME = {
    "M1":1,
    'M15':15,
    "M30":30,
    "H1":60,
    "H4":240,
    "D1":1440,
}           

MINUTETOFRAME = {
    1:"M1",
    15:'M15',
    30:'M30',
    60:'H1',
    240:'H4',
    1440:'D1'
}


class Position:
    def __init__(
        self, 
        timeframe:str,
        active:str,
        lot:float,
        time:str,
        type:str
    ) -> None:
        
        self.type = 0 if type == 'CALL' else 1
        self.timeframe = TIMEFRAME[timeframe]
        self.time = datetime.strptime(f'{datetime.now().date()} {time}',
                                      '%Y-%m-%d %H:%M')
        self.lot = lot
        self.active = active
        self.called = False
        self.closed = False
        self.maringale = False
        self.ticket = 0
        self.profit = 0
        self.endtime = self.time + timedelta(minutes=self.timeframe)
        

positions:deque[Position] = deque( maxlen=30)
closed_positions:deque[Position] = deque( maxlen=30)
opened_positions:deque[Position] = deque( maxlen=30)

logging.debug("Deques de posições criadas")

thread_run = True
socket = Listenner()

logging.info("Metatrader 4 Connectado")

initial_lot = 0.01
martingale_step = 0.01
use_martingale=True
max_martingale = 2
time_diference=1

def send_position(pos:Position):
    global socket
    try:
        trade = f'TRADE|OPEN|{pos.type}|{pos.active}|0|0|0|{pos.lot}|'
        ticket = socket.send(trade)
        ticket = ticket.split('|')[0]
        pos.ticket = ticket
        if ticket != '-1':
            pos.called = True
            logging.info(trade)
        return True
    except Exception as e:
        logging.error(e)
        return False    

def close_position(pos:Position):
    global socket
    try:
        trade = f'TRADE|CLOSE|{pos.type}|{pos.active}|0|0|0|{pos.lot}|{pos.ticket}'
        ticket = socket.send(trade)
        profit = ticket.split('|')[0]
        pos.profit = profit
        if profit != 'N':
            pos.closed = True
            logging.info(trade)
        return True
    except Exception as e:
        logging.error(e)
        return False  

def open_trades():
    logging.debug("Iniciando Monitoramento de abertura de trades")
    while True:
        
        if not thread_run:
            break
        
        if time_diference < 0:
        
            for position in positions:
                if datetime.now().date() == position.time.date():
                    now = (datetime.now() - timedelta(hours=time_diference))
                    if now.hour == position.time.hour and \
                        now.minute >= position.time.minute and \
                            not position.called:
                                
                            send_position(position)
                            if position.ticket != '-1':
                                opened_positions.append(position)
        else:
            for position in positions:
                if datetime.now().date() == position.time.date():
                    now = (datetime.now() + timedelta(hours=time_diference))
                    if now.hour == position.time.hour and \
                        now.minute >= position.time.minute and \
                            not position.called:
                                
                            send_position(position)
                            if position.ticket != '-1':
                                opened_positions.append(position)
        sleep(1)

def close_trades():
    logging.debug("Iniciando Monitoramento de fechamento de trades")
    while True:
        
        if not thread_run:
            break
        if time_diference < 0:
            for position in opened_positions:
                if datetime.now().date() == position.endtime.date():
                    now = datetime.now() - timedelta(hours=time_diference)
                    if now.hour == position.endtime.hour and \
                        now.minute >= position.endtime.minute and \
                            not position.closed:
                                
                            close_position(position)
                            if position.profit != 'N':
                                closed_positions.append(position)
        else:
            for position in opened_positions:
                if datetime.now().date() == position.endtime.date():
                    now = datetime.now() + timedelta(hours=time_diference)
                    if now.hour == position.endtime.hour and \
                        now.minute >= position.endtime.minute and \
                            not position.closed:
                                
                            close_position(position)
                            if position.profit != 'N':
                                closed_positions.append(position)
        sleep(1)

def open_martingales():
    logging.debug("Iniciando Monitoramento de criação de Martingales")
    global max_martingale, use_martingale
    martingale_count = 0
    while True:
        
        if not thread_run:
            break
        if closed_positions:
            last = closed_positions[-1]
            if use_martingale:
                if not last.maringale:
                    logging.debug("Executar Martingale")
                    if float(last.profit) < 0:
                        if martingale_count < max_martingale:
                            logging.debug("Possível martingale")
                            new_pos = Position(
                                MINUTETOFRAME[last.timeframe],
                                last.active,
                                round(last.lot+martingale_step,2),
                                str(last.endtime.time())[:5],
                                last.type
                            )
                            positions.append(new_pos)
                            
                            
                            last.maringale = True
                            martingale_count += 1
                            logging.debug("Contador de Martingale em %s", martingale_count)
                        else:
                            last.maringale = True
                            martingale_count = 0
                    else:
                        last.maringale = True
                        martingale_count = 0
        sleep(1)

@client.on_message(filters.text)
async def process_signal(client, message):
    if message.chat.id == int(f'-100{os.getenv("GROUP_ID")}'):
        logging.info("Mensagem Recebida: \n %s", message.text)
        if re.findall(f'{os.getenv("SIGNAL")}',message.text):
            
            timeframe = re.findall(r'M{1}[1-9]{1,2}|D{1}[1-9]{1,2}',message.text)
            timeframe = timeframe[0] if len(timeframe) == 1 else timeframe[1]
            actives = re.findall(r'[A-Z]{3}/[A-Z]{3}',message.text)
            times = re.findall(r'[0-9]{2}:[0-9]{2}:{,1}[0-9]{,2}',message.text)
            types = re.findall(r'PUT|CALL', message.text)
            if len(actives) == len(times) == len(types):
                logging.info(f'Adicionando {len(actives)} sinais')
                for idx, active in enumerate(actives):
                    active = active.replace('/','')
                    pos = Position(
                        timeframe,
                        active,
                        initial_lot,
                        times[idx],
                        types[idx]
                    )
                    positions.append(pos)
            
@bot.on_message(filters.command('set_step') & filters.private)
async def process_config(client, message):
    global martingale_step
    lot = re.findall(r'[0-9]{1}.{,1}[0-9]{,2}', message.text)
    if lot:
        lot = lot[0]
        martingale_step = round(float(lot),2)
        await bot.send_message(message.chat.id, f"Lot do Martingale configurado para {lot}")
        logging.info(f"Martingale Lot atual {martingale_step}")
    else:
        await message.reply_text("Você precisa enviar um número com o lot")
        
@bot.on_message(filters.command('set_lot') & filters.private)
async def process_config(client, message):
    global initial_lot
    lot = re.findall(r'[0-9]{1}.{,1}[0-9]{,2}', message.text)
    if lot:
        lot = lot[0]
        initial_lot = round(float(lot),2)
        await bot.send_message(message.chat.id, f"Lot das operações configurado para {lot}")
        logging.info(f"Lot atual {initial_lot}")
    else:
        await message.reply_text("Você precisa enviar um número com o lot")

@bot.on_message(filters.command('set_martingale') & filters.private)
async def process_config(client, message):
    global max_martingale
    max = re.findall(r'[0-9]{1}', message.text)
    if max:
        max = max[0]
        max_martingale = round(float(max),2)
        await bot.send_message(message.chat.id, f"Martingale maximo configurado para {max}")
        logging.info(f"Martingale atual {max_martingale}")
    else:
        await message.reply_text("Você precisa enviar um número inteiro ex. 2")

@bot.on_message(filters.command('use_martingale') & filters.private)
async def process_config(client, message):
    global use_martingale
    if use_martingale:
        use_martingale = False
        await message.reply_text("Uso de Martingale Desligado")
        logging.info("Uso de Martingale Desligado")
    else:
        use_martingale = True
        await message.reply_text("Uso de Martingale Ligado")
        logging.info("Uso de Martingale Ligado")
    

@bot.on_message(filters.command('set_time') & filters.private)
async def process_config(client, message):
    global time_diference
    diference = re.findall(r'-{,1}[0-9]{1,2}', message.text)
    if diference:
        diference = diference[0]
        time_diference = int(diference)
        await bot.send_message(message.chat.id, f"Diferenca de hora das corretora é {diference} da nossa")
        logging.info(f"Tempo atual {time_diference}")
    else:
        msg = "Caso não tenha diferenca envie 0\n"
        msg += "Caso a corretora tenha 3 horas de diferença do fuso horário envie 3\n"
        msg += "Caso a corretora tenha -3 horas de diferença do fuso horário envie -3"
        await message.reply_text(msg)

@bot.on_message(filters.command('start') & filters.private)
async def process_config(client, message):
    msg = 'Olá!, abaixo terá toda a lista de configurações para as operações\n\n'
    msg += '-> /set_time (número) Define a diferenca de horário coma  corretora\n'
    msg+= '-> /set_step (ex: 0.1) Define o lot aumentado em cada martingale \n'
    msg += '-> /set_lot (ex: 0.1) Define o lot de cada operação\n'
    msg += '-> /set_martingale (ex: 3) Define o máximo de martingale executado\n'
    msg += '-> /use_martingale Define o uso de martingale\n\n'
    
    await message.reply_text(msg)

if __name__ == "__main__":
    open_all = threading.Thread(target=open_trades, daemon=True)
    close_all = threading.Thread(target=close_trades, daemon=True)
    martingale_all = threading.Thread(target=open_martingales, daemon=True)
    
    
    #run
    open_all.start()
    close_all.start()
    martingale_all.start()
    try:
        #uvloop.install()
        logging.info("Iniciando bot no Telegram")
        asyncio.run(compose(client_list))
    except KeyboardInterrupt:
        # stop all threads
        thread_run = False
        open_all.join()
        close_all.join()
        martingale_all.join()
    except ValueError:
        #corroutine
        thread_run = False
        open_all.join()
        close_all.join()
        martingale_all.join()
    