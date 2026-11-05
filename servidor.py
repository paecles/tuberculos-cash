import asyncio
import json
import random
import time
import os
import websockets

# [1] REGRINHAS DO JOGO <3

LARGURA, ALTURA = 1280, 700

# ITEMS QUE PODEM SER CONSEGUIDOS
items_aposta = {
    "espadas": {
        "faca de plástico": 3,
        "faca de brinquedo": 7,
        "faca real": 10,
        "faca de diamante": 50
    }
}

PESOS_APOSTA = [50, 20, 8, 1]

racas_possiveis = {
    "batata": {"vida": 100, "ataque": 10, "velocidade": 10},
    "cebola": {"vida": 150, "ataque": 10, "velocidade": 20},
    "cenoura": {"vida": 100, "ataque": 15, "velocidade": 10},
}

MAX_POR_RACA = 2
MAX_JOGADORES = len(racas_possiveis) * MAX_POR_RACA
APOSTAS_INICIAIS = 5

CORES = [
    (70, 140, 255),
    (255, 80, 80),
    (255, 255, 255)
]

POR_COR = 2

TEMPO_APOSTA = 30
PAUSA_RESULTADO = 2

TAM_JOGADOR = (40, 40)
ALCANCE_ATAQUE = 20
TOLERANCIA = 15
DURACAO_ATAQUE = 0.25
COOLDOWN_ATAQUE = 0.5

proximo_id = 0
clientes = {}
jogadores = {}

def transmitir(mensagem: dict):
    websockets.broadcast(
        clientes.keys(),
        json.dumps(mensagem)
    )

def dados_publicos():
    return {
        str(ide): {
            "x": j["x"],
            "y": j["y"],
            "raca": j["raca"],
            "vida": j["vida"],
            "sala": j["sala"],
            "cor": j["cor"],
            "arma": j["inventario"][0] if j["inventario"] else None,
            "olhando": j["olhando"]
        }
        for ide, j in jogadores.items()
    }

def enviar_estado():
    transmitir({
        "tipo": "estado",
        "jogadores": dados_publicos()
    })

def perfil(jogador_id):
    j = jogadores[jogador_id]

    return {
        "tipo": "meu_perfil",
        "id": jogador_id,
        "raca": j["raca"],
        "cor": j["cor"],
        "vida": j["vida"],
        "ataque": j["ataque"],
        "velocidade": j["velocidade"],
        "inventario": j["inventario"],
        "apostas_restantes": j["apostas_restantes"],
        "sala": j["sala"],
        "tempo_restante": max(
            0,
            j["fim_aposta"] - time.monotonic()
        ),
    }

def escolher_raca():
    com_vaga = []

    for raca in racas_possiveis:
        usados = sum(
            1
            for j in jogadores.values()
            if j["raca"] == raca
        )

        if usados < MAX_POR_RACA:
            com_vaga.append(raca)

    return random.choice(com_vaga)

def escolher_cor():
    for c in CORES:
        usados = sum(
            1
            for j in jogadores.values()
            if j["cor"] == c
        )

        if usados < POR_COR:
            return c

    return (128, 128, 128)

async def mandar_para_combate(websocket, jogador_id):
    j = jogadores.get(jogador_id)

    if j is None or j["sala"] != "sala de aposta":
        return

    j["sala"] = "sala de combate"

    await websocket.send(
        json.dumps(perfil(jogador_id))
    )

    enviar_estado()

async def tempo_de_aposta(websocket, jogador_id):
    await asyncio.sleep(TEMPO_APOSTA)

    try:
        await mandar_para_combate(
            websocket,
            jogador_id
        )
    except websockets.exceptions.ConnectionClosed:
        pass

async def apostar(websocket, jogador_id):
    j = jogadores[jogador_id]

    if j["sala"] != "sala de aposta":
        await websocket.send(
            json.dumps({
                "tipo": "erro",
                "mensagem": "Você já saiu da sala de aposta"
            })
        )
        return

    if j["apostas_restantes"] <= 0:
        await websocket.send(
            json.dumps({
                "tipo": "erro",
                "mensagem": "Você não tem apostas restantes"
            })
        )
        return

    nomes = list(
        items_aposta["espadas"].keys()
    )

    resultado = random.choices(
        nomes,
        weights=PESOS_APOSTA,
        k=1
    )[0]

    j["inventario"] = [resultado]
    j["apostas_restantes"] -= 1

    if resultado == "faca de diamante":
        aviso = (
            f"jogador {jogador_id} "
            "tirou uma faca de diamante!"
        )

        print(aviso)

        transmitir({
            "tipo": "aviso",
            "mensagem": aviso
        })

    await websocket.send(
        json.dumps({
            "tipo": "resultado_aposta",
            "item": resultado
        })
    )

    await websocket.send(
        json.dumps(perfil(jogador_id))
    )

    if j["apostas_restantes"] == 0:
        await asyncio.sleep(PAUSA_RESULTADO)
        await mandar_para_combate(
            websocket,
            jogador_id
        )

async def enviar_perfil_para(jogador_id):
    for ws, ide in list(clientes.items()):
        if ide == jogador_id:
            try:
                await ws.send(
                    json.dumps(
                        perfil(jogador_id)
                    )
                )
            except websockets.exceptions.ConnectionClosed:
                pass

async def atacar(atacante_id, alvo_id):
    atacante = jogadores.get(atacante_id)

    if atacante is None or atacante["sala"] != "sala de combate":
        return

    if isinstance(alvo_id, bool) or not isinstance(alvo_id, int):
        return

    if alvo_id == atacante_id:
        return

    alvo = jogadores.get(alvo_id)

    if alvo is None or alvo["sala"] != "sala de combate":
        return

    agora = time.monotonic()
    desde = agora - atacante["inicio_golpe"]

    if desde >= COOLDOWN_ATAQUE * 0.8:
        atacante["inicio_golpe"] = agora
        atacante["acertados"] = set()

    elif desde > DURACAO_ATAQUE + 0.1:
        return

    if alvo_id in atacante["acertados"]:
        return

    dx = abs(
        atacante["x"] - alvo["x"]
    )

    dy = abs(
        atacante["y"] - alvo["y"]
    )

    limite_x = (
        TAM_JOGADOR[0]
        + ALCANCE_ATAQUE
        + TOLERANCIA
    )

    limite_y = (
        TAM_JOGADOR[1]
        + ALCANCE_ATAQUE
        + TOLERANCIA
    )

    if dx > limite_x or dy > limite_y:
        return

    atacante["acertados"].add(
        alvo_id
    )

    arma = (
        atacante["inventario"][0]
        if atacante["inventario"]
        else None
    )

    dano_faca = items_aposta[
        "espadas"
    ].get(
        arma,
        0
    )

    dano = (
        atacante["ataque"]
        + dano_faca
    )

    alvo["vida"] = max(
        0,
        alvo["vida"] - dano
    )

    print(
        f"Jogador {atacante_id} "
        f"acertou {alvo_id}: "
        f"-{dano} "
        f"(vida {alvo['vida']})"
    )

    if alvo["vida"] == 0:
        alvo["sala"] = "eliminado"

        transmitir({
            "tipo": "aviso",
            "mensagem":
                f"jogador {alvo_id} "
                f"foi eliminado por {atacante_id}!"
        })

    enviar_estado()

    await enviar_perfil_para(
        alvo_id
    )

async def clienteconectado(websocket):
    global proximo_id

    if len(clientes) >= MAX_JOGADORES:
        await websocket.close()
        return

    jogador_id = proximo_id
    proximo_id += 1

    raca = escolher_raca()
    cor = escolher_cor()

    jogadores[jogador_id] = {
        "raca": raca,
        "cor": cor,
        "vida": racas_possiveis[raca]["vida"],
        "ataque": racas_possiveis[raca]["ataque"],
        "velocidade": racas_possiveis[raca]["velocidade"],
        "inventario": ["faca de plástico"],
        "apostas_restantes": APOSTAS_INICIAIS,
        "x": 600,
        "y": 600,
        "sala": "sala de aposta",
        "fim_aposta":
            time.monotonic()
            + TEMPO_APOSTA,
        "olhando": 1,
        "inicio_golpe": -999.0,
        "acertados": set(),
    }

    clientes[websocket] = jogador_id

    print(
        "Jogador",
        jogador_id,
        "conectado como",
        raca
    )

    await websocket.send(
        json.dumps(
            perfil(jogador_id)
        )
    )

    enviar_estado()

    tarefa_tempo = asyncio.create_task(
        tempo_de_aposta(
            websocket,
            jogador_id
        )
    )

    try:
        async for texto in websocket:

            try:
                dados = json.loads(texto)
            except json.JSONDecodeError:
                continue

            tipo = dados.get("tipo")

            if (
                tipo == "mover"
                and jogadores[jogador_id]["sala"]
                == "sala de combate"
            ):

                x = dados.get("x")
                y = dados.get("y")

                if (
                    isinstance(x, (int, float))
                    and isinstance(y, (int, float))
                ):

                    novo_x = max(
                        0,
                        min(LARGURA, x)
                    )

                    if novo_x > jogadores[jogador_id]["x"]:
                        jogadores[jogador_id]["olhando"] = 1

                    elif novo_x < jogadores[jogador_id]["x"]:
                        jogadores[jogador_id]["olhando"] = -1

                    jogadores[jogador_id]["x"] = novo_x

                    jogadores[jogador_id]["y"] = max(
                        0,
                        min(ALTURA, y)
                    )

                    enviar_estado()

            elif tipo == "apostar":
                await apostar(
                    websocket,
                    jogador_id
                )

            elif tipo == "sair_aposta":
                await mandar_para_combate(
                    websocket,
                    jogador_id
                )

            elif tipo == "atacar":
                await atacar(
                    jogador_id,
                    dados.get("alvo")
                )

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        tarefa_tempo.cancel()

        clientes.pop(
            websocket,
            None
        )

        jogadores.pop(
            jogador_id,
            None
        )

        print(
            "Jogador",
            jogador_id,
            "saiu"
        )

        enviar_estado()


# ============================================================
# LIGAR O SERVER
# ============================================================

async def main():

    # A hospedagem normalmente fornece PORT.
    # Se não fornecer, usa 8765 para teste local.
    porta = int(
        os.environ.get(
            "PORT",
            8765
        )
    )

    async with websockets.serve(
        clienteconectado,
        "0.0.0.0",
        porta
    ):

        print(
            f"Servidor iniciado na porta {porta}!"
        )

        await asyncio.Future()


asyncio.run(main())
