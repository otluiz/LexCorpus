#!/usr/bin/env python3
"""Consumidor de TESTE para os eventos do LexCorpus.

Cria uma fila, vincula ao exchange `lexcorpus.events` e imprime cada evento
`concurso.disponivel` / `concurso.atualizado` que chegar. Serve para:
  1. validar visualmente que o LexCorpus está publicando corretamente;
  2. servir de exemplo mínimo de como o LexLearn consumiria os eventos.

USO:
    # 1. suba o RabbitMQ (docker run ... rabbitmq:3-management)
    # 2. rode ESTE consumidor num terminal (ele fica escutando):
    python consumer_teste.py
    # 3. noutro terminal, rode o crawl com a fila ligada:
    #    scrapy crawl cebraspe -a slug=prf_21 -s RABBIT_ENABLED=True
    # 4. veja o evento aparecer aqui.

IMPORTANTE: rode o consumidor ANTES do crawl. Um exchange sem fila vinculada
descarta as mensagens — por isso a fila precisa existir e estar vinculada antes
de o LexCorpus publicar.
"""
import json
import os
import sys

import pika

RABBIT_URL = os.environ.get("RABBIT_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE = "lexcorpus.events"
QUEUE = "teste.lexlearn"           # fila de teste (o LexLearn usaria a sua própria)
ROUTING = "concurso.#"             # '#' captura concurso.disponivel E concurso.atualizado


def on_message(channel, method, properties, body):
    print("\n" + "=" * 70)
    print(f"EVENTO RECEBIDO  (routing key: {method.routing_key})")
    print("=" * 70)
    try:
        evento = json.loads(body)
        print(f"  event:        {evento.get('event')}")
        print(f"  event_id:     {evento.get('event_id')}")
        print(f"  banca:        {evento.get('banca')}")
        print(f"  concurso:     {evento.get('concurso')}")
        print(f"  pasta_uri:    {evento.get('pasta_uri')}")
        arquivos = evento.get("arquivos", [])
        print(f"  arquivos:     {len(arquivos)}")
        papeis = {}
        for a in arquivos:
            papeis[a["papel"]] = papeis.get(a["papel"], 0) + 1
        print(f"  papéis:       {papeis}")
        rot = evento.get("extra", {}).get("rotulos", {})
        if rot:
            print(f"  rótulos crus: banca={rot.get('banca')} concurso={rot.get('concurso')}")
    except json.JSONDecodeError:
        print("  [corpo não é JSON válido]")
        print(body[:200])
    # ack: confirma o processamento (remove a mensagem da fila)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main():
    params = pika.URLParameters(RABBIT_URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()

    # declara o exchange (idempotente; precisa bater com o do produtor)
    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    # declara a fila e vincula ao exchange
    ch.queue_declare(queue=QUEUE, durable=True)
    ch.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING)

    print(f"Escutando '{EXCHANGE}' (routing '{ROUTING}') na fila '{QUEUE}'.")
    print("Rode o crawl noutro terminal. Ctrl+C para sair.\n")

    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=QUEUE, on_message_callback=on_message)
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        print("\nEncerrando consumidor.")
        ch.stop_consuming()
    conn.close()


if __name__ == "__main__":
    main()
