import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import gc
from datetime import datetime, timezone, timedelta

pares_smc = ["EURUSD=X","GBPUSD=X","USDJPY=X","GC=F","BTC-USD"]
nomes_smc = {"EURUSD=X":"EURUSD","GBPUSD=X":"GBPUSD","USDJPY=X":"USDJPY","GC=F":"XAUUSD","BTC-USD":"BTCUSD"}

def obter_dados_smc(par, intervalo="15m", periodo="7d"):
    try:
        d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except: return pd.DataFrame()

def add_ind_smc(df):
    if len(df)<20: return df
    df = df.copy()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    return df.dropna()

def precisao(par):
    if "GC" in par: return 2
    if "BTC" in par: return 0
    return 5

# ==================== ESTRUTURA: BOS/CHoCH/HH/HL/LH/LL ====================
def detectar_estrutura_completa(df, par):
    p_round = precisao(par)
    df2 = df.copy()
    df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
    df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
    sh = df2[df2["sh"]]
    sl = df2[df2["sl"]]
    if len(sh)<2 or len(sl)<2: return []

    eventos = []
    ush = float(sh["High"].iloc[-1]); psh = float(sh["High"].iloc[-2])
    usl = float(sl["Low"].iloc[-1]); psl = float(sl["Low"].iloc[-2])
    p = float(df["Close"].iloc[-1]); pp = float(df["Close"].iloc[-2])
    hora_sh = sh.index[-1]
    hora_sl = sl.index[-1]

    # Classificacao de estrutura
    if ush > psh: eventos.append({"tipo":"HH","preco":round(ush,p_round),"hora":hora_sh})
    else: eventos.append({"tipo":"LH","preco":round(ush,p_round),"hora":hora_sh})
    if usl > psl: eventos.append({"tipo":"HL","preco":round(usl,p_round),"hora":hora_sl})
    else: eventos.append({"tipo":"LL","preco":round(usl,p_round),"hora":hora_sl})

    # BOS / CHoCH
    bos_bull = (p>ush) and (pp>ush)
    bos_bear = (p<usl) and (pp<usl)
    tendencia_anterior = "BULLISH" if (ush>psh and usl>psl) else "BEARISH" if (ush<psh and usl<psl) else "NEUTRO"

    if bos_bull:
        if tendencia_anterior == "BEARISH":
            eventos.append({"tipo":"CHOCH_BULL","preco":round(p,p_round),"hora":df.index[-1]})
        else:
            eventos.append({"tipo":"BOS_BULL","preco":round(p,p_round),"hora":df.index[-1]})
    if bos_bear:
        if tendencia_anterior == "BULLISH":
            eventos.append({"tipo":"CHOCH_BEAR","preco":round(p,p_round),"hora":df.index[-1]})
        else:
            eventos.append({"tipo":"BOS_BEAR","preco":round(p,p_round),"hora":df.index[-1]})

    return eventos

# ==================== LIQUIDEZ: BSL/SSL/EQH/EQL ====================
def detectar_liquidez_completa(df, par):
    p_round = precisao(par)
    h20 = df["High"].tail(30)
    l20 = df["Low"].tail(30)
    p = float(df["Close"].iloc[-1])

    bsl = float(h20.max())
    ssl = float(l20.min())

    # Equal highs/lows: dois topos/fundos muito proximos (tolerancia 0.05%)
    highs_sorted = h20.nlargest(5)
    lows_sorted = l20.nsmallest(5)

    eqh = None
    for i in range(len(highs_sorted)):
        for j in range(i+1, len(highs_sorted)):
            v1, v2 = highs_sorted.iloc[i], highs_sorted.iloc[j]
            if abs(v1-v2)/v1 < 0.0008:
                eqh = round((v1+v2)/2, p_round)
                break
        if eqh: break

    eql = None
    for i in range(len(lows_sorted)):
        for j in range(i+1, len(lows_sorted)):
            v1, v2 = lows_sorted.iloc[i], lows_sorted.iloc[j]
            if abs(v1-v2)/v1 < 0.0008:
                eql = round((v1+v2)/2, p_round)
                break
        if eql: break

    # Liquidity grab: varredura recente
    h5 = df["High"].tail(5); l5 = df["Low"].tail(5)
    grab_topo = float(h5.max()) >= bsl*0.999 and p < bsl
    grab_fundo = float(l5.min()) <= ssl*1.001 and p > ssl

    bsl_capturada = p > bsl
    ssl_capturada = p < ssl

    return {
        "bsl": round(bsl,p_round), "bsl_capturada": bsl_capturada,
        "ssl": round(ssl,p_round), "ssl_capturada": ssl_capturada,
        "eqh": eqh, "eql": eql,
        "grab_topo": grab_topo, "grab_fundo": grab_fundo
    }

# ==================== ORDER BLOCKS ====================
def detectar_order_blocks(df, par, tf_label):
    p_round = precisao(par)
    df = df.copy()
    df["vf"] = df["corpo"] > df["media_corpo"]*1.4
    p = float(df["Close"].iloc[-1])
    blocks = []

    for i in range(len(df)-3, max(len(df)-30,0), -1):
        if not df["vf"].iloc[i]: continue
        candle = df.iloc[i]
        hora_criacao = df.index[i]

        # Bullish OB: ultima vela bear antes de subida forte
        if float(candle["Close"]) < float(candle["Open"]):
            if float(df["Close"].iloc[-1]) > float(candle["High"]):
                alto = round(float(candle["High"]),p_round)
                baixo = round(float(candle["Low"]),p_round)
                # Testes: quantas vezes o preco voltou a zona depois
                pos_candles = df.iloc[i+1:]
                testes = ((pos_candles["Low"]<=alto)&(pos_candles["High"]>=baixo)).sum()
                # Invalidado se preco fechou abaixo do baixo do OB
                invalido = float(df["Close"].iloc[-1]) < baixo
                if not invalido:
                    forca = max(50, 95 - testes*8)
                    idade = datetime.now(timezone.utc) - hora_criacao.to_pydatetime().replace(tzinfo=timezone.utc) if hora_criacao.tzinfo is None else datetime.now(timezone.utc)-hora_criacao.to_pydatetime()
                    blocks.append({
                        "tipo":"BULLISH_OB","tf":tf_label,"alto":alto,"baixo":baixo,
                        "hora_criacao":hora_criacao,"testes":int(testes),
                        "forca":int(forca),"valido": forca>=70
                    })
                    break

    for i in range(len(df)-3, max(len(df)-30,0), -1):
        if not df["vf"].iloc[i]: continue
        candle = df.iloc[i]
        hora_criacao = df.index[i]
        # Bearish OB
        if float(candle["Close"]) > float(candle["Open"]):
            if float(df["Close"].iloc[-1]) < float(candle["Low"]):
                alto = round(float(candle["High"]),p_round)
                baixo = round(float(candle["Low"]),p_round)
                pos_candles = df.iloc[i+1:]
                testes = ((pos_candles["Low"]<=alto)&(pos_candles["High"]>=baixo)).sum()
                invalido = float(df["Close"].iloc[-1]) > alto
                if not invalido:
                    forca = max(50, 95 - testes*8)
                    blocks.append({
                        "tipo":"BEARISH_OB","tf":tf_label,"alto":alto,"baixo":baixo,
                        "hora_criacao":hora_criacao,"testes":int(testes),
                        "forca":int(forca),"valido": forca>=70
                    })
                    break
    return blocks

# ==================== FAIR VALUE GAP ====================
def detectar_fvg(df, par, tf_label):
    p_round = precisao(par)
    p = float(df["Close"].iloc[-1])
    fvgs = []
    for i in range(len(df)-2, max(len(df)-25,1), -1):
        if i+1 >= len(df): continue
        # Bullish FVG
        if df["Low"].iloc[i+1] > df["High"].iloc[i-1]:
            topo = round(float(df["Low"].iloc[i+1]),p_round)
            base = round(float(df["High"].iloc[i-1]),p_round)
            preenchido_pct = 0
            if p < topo:
                if p <= base: preenchido_pct = 100
                else: preenchido_pct = round((topo-p)/(topo-base)*100,0)
            estado = "PREENCHIDO" if preenchido_pct>=100 else "PARCIAL" if preenchido_pct>0 else "ABERTO"
            if estado != "PREENCHIDO":
                fvgs.append({"tipo":"BULLISH_FVG","tf":tf_label,"topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
            break
    for i in range(len(df)-2, max(len(df)-25,1), -1):
        if i+1 >= len(df): continue
        # Bearish FVG
        if df["High"].iloc[i+1] < df["Low"].iloc[i-1]:
            topo = round(float(df["Low"].iloc[i-1]),p_round)
            base = round(float(df["High"].iloc[i+1]),p_round)
            preenchido_pct = 0
            if p > base:
                if p >= topo: preenchido_pct = 100
                else: preenchido_pct = round((p-base)/(topo-base)*100,0)
            estado = "PREENCHIDO" if preenchido_pct>=100 else "PARCIAL" if preenchido_pct>0 else "ABERTO"
            if estado != "PREENCHIDO":
                fvgs.append({"tipo":"BEARISH_FVG","tf":tf_label,"topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
            break
    return fvgs

# ==================== NIVEIS INSTITUCIONAIS ====================
def niveis_institucionais(par):
    p_round = precisao(par)
    try:
        d_diario = obter_dados_smc(par,"1d","10d")
        pdh = round(float(d_diario["High"].iloc[-2]),p_round)
        pdl = round(float(d_diario["Low"].iloc[-2]),p_round)
        d_semanal = obter_dados_smc(par,"1wk","10wk")
        pwh = round(float(d_semanal["High"].iloc[-2]),p_round)
        pwl = round(float(d_semanal["Low"].iloc[-2]),p_round)
        return {"pdh":pdh,"pdl":pdl,"pwh":pwh,"pwl":pwl}
    except:
        return {"pdh":0,"pdl":0,"pwh":0,"pwl":0}

# ==================== TENDENCIA H1 ====================
def tendencia_h1_smc(par):
    try:
        d = obter_dados_smc(par,"1h","10d")
        if len(d)<20: return "NEUTRO"
        e20 = d["Close"].ewm(span=20).mean()
        p = float(d["Close"].iloc[-1])
        e = float(e20.iloc[-1])
        return "BULLISH" if p>e else "BEARISH"
    except: return "NEUTRO"

def formatar_idade(hora_criacao):
    try:
        agora = datetime.now(timezone.utc)
        if hora_criacao.tzinfo is None:
            hc = hora_criacao.to_pydatetime().replace(tzinfo=timezone.utc)
        else:
            hc = hora_criacao.to_pydatetime()
        diff = agora - hc
        horas = int(diff.total_seconds()//3600)
        minutos = int((diff.total_seconds()%3600)//60)
        return f"{horas}h {minutos}m"
    except: return "N/D"

def escanear_par(par):
    nome = nomes_smc[par]
    try:
        d15 = obter_dados_smc(par,"15m","5d")
        if len(d15)<30: return None
        d15 = add_ind_smc(d15)

        dh1 = obter_dados_smc(par,"1h","10d")
        dh1 = add_ind_smc(dh1)

        dm5 = obter_dados_smc(par,"5m","2d")
        dm5 = add_ind_smc(dm5)

        estrutura_m15 = detectar_estrutura_completa(d15, par)
        estrutura_m5 = detectar_estrutura_completa(dm5, par) if len(dm5)>30 else []
        liquidez = detectar_liquidez_completa(d15, par)
        ob_m15 = detectar_order_blocks(d15, par, "M15")
        ob_h1 = detectar_order_blocks(dh1, par, "H1") if len(dh1)>30 else []
        fvg_m5 = detectar_fvg(dm5, par, "M5") if len(dm5)>30 else []
        fvg_m15 = detectar_fvg(d15, par, "M15")
        niveis = niveis_institucionais(par)
        tend_h1 = tendencia_h1_smc(par)

        for ob in ob_m15+ob_h1:
            ob["idade"] = formatar_idade(ob["hora_criacao"])
        for f in fvg_m5+fvg_m15:
            f["idade"] = formatar_idade(f["hora"])

        ob_validos = [o for o in (ob_m15+ob_h1) if o["valido"]]
        fvg_abertos = [f for f in (fvg_m5+fvg_m15) if f["estado"]=="ABERTO"]
        liq_nao_capturada = sum([not liquidez["bsl_capturada"], not liquidez["ssl_capturada"], liquidez["eqh"] is not None, liquidez["eql"] is not None])

        gc.collect()
        return {
            "par": nome,
            "estrutura_m15": estrutura_m15,
            "estrutura_m5": estrutura_m5,
            "liquidez": liquidez,
            "ob_m15": ob_m15,
            "ob_h1": ob_h1,
            "fvg_m5": fvg_m5,
            "fvg_m15": fvg_m15,
            "niveis": niveis,
            "tend_h1": tend_h1,
            "ob_validos": len(ob_validos),
            "fvg_abertos": len(fvg_abertos),
            "liq_nao_capturada": liq_nao_capturada
        }
    except:
        gc.collect()
        return None

def gerar_recomendacao_entrada(r):
    """Gera recomendacao de tipo de ordem e vela esperada baseado no estado actual do par"""
    recomendacoes = []

    estrutura_recente = r["estrutura_m15"]
    tem_choch = any(e["tipo"] in ["CHOCH_BULL","CHOCH_BEAR"] for e in estrutura_recente)
    tem_bos = any(e["tipo"] in ["BOS_BULL","BOS_BEAR"] for e in estrutura_recente)

    ob_validos = [o for o in (r["ob_m15"]+r["ob_h1"]) if o["valido"]]
    fvg_abertos = [f for f in (r["fvg_m5"]+r["fvg_m15"]) if f["estado"] in ["ABERTO","PARCIAL"]]

    # Cenario 1: CHoCH + OB disponivel = reversao
    if tem_choch and ob_validos:
        ob = ob_validos[0]
        direcao = "BUY" if ob["tipo"]=="BULLISH_OB" else "SELL"
        vela = "Bullish Engulfing ou Pin Bar de alta" if direcao=="BUY" else "Bearish Engulfing ou Pin Bar de baixa"
        recomendacoes.append({
            "cenario": "REVERSAO (CHoCH)",
            "direcao": direcao,
            "ordem": "BUY LIMIT" if direcao=="BUY" else "SELL LIMIT",
            "zona_entrada": f"{ob['baixo']} - {ob['alto']}",
            "vela_esperada": vela,
            "sl_sugerido": f"Abaixo de {ob['baixo']}" if direcao=="BUY" else f"Acima de {ob['alto']}",
            "prioridade": "ALTA" if ob["forca"]>=85 else "MEDIA"
        })

    # Cenario 2: BOS + OB na mesma direcao = continuacao
    if tem_bos and ob_validos:
        bos_dir = None
        for e in estrutura_recente:
            if e["tipo"]=="BOS_BULL": bos_dir="BUY"
            elif e["tipo"]=="BOS_BEAR": bos_dir="SELL"
        if bos_dir:
            obs_alinhados = [o for o in ob_validos if (o["tipo"]=="BULLISH_OB" and bos_dir=="BUY") or (o["tipo"]=="BEARISH_OB" and bos_dir=="SELL")]
            if obs_alinhados:
                ob = obs_alinhados[0]
                vela = "Bullish Pin Bar ou vela de retomada" if bos_dir=="BUY" else "Bearish Pin Bar ou vela de retomada"
                recomendacoes.append({
                    "cenario": "CONTINUACAO (BOS)",
                    "direcao": bos_dir,
                    "ordem": "BUY LIMIT" if bos_dir=="BUY" else "SELL LIMIT",
                    "zona_entrada": f"{ob['baixo']} - {ob['alto']}",
                    "vela_esperada": vela,
                    "sl_sugerido": f"Abaixo de {ob['baixo']}" if bos_dir=="BUY" else f"Acima de {ob['alto']}",
                    "prioridade": "ALTA" if ob["forca"]>=85 else "MEDIA"
                })

    # Cenario 3: FVG aberto sem CHoCH/BOS recente = correcao/reteste
    if fvg_abertos and not tem_choch and not tem_bos:
        fvg = fvg_abertos[0]
        direcao = "BUY" if fvg["tipo"]=="BULLISH_FVG" else "SELL"
        vela = "Vela de rejeicao na base do gap" if direcao=="BUY" else "Vela de rejeicao no topo do gap"
        recomendacoes.append({
            "cenario": "CORRECAO / RETESTE (FVG)",
            "direcao": direcao,
            "ordem": "MARKET ORDER (apos confirmacao)",
            "zona_entrada": f"{fvg['base']} - {fvg['topo']}",
            "vela_esperada": vela,
            "sl_sugerido": f"Abaixo de {fvg['base']}" if direcao=="BUY" else f"Acima de {fvg['topo']}",
            "prioridade": "MEDIA" if fvg["estado"]=="ABERTO" else "BAIXA"
        })

    # Cenario 4: Liquidez proxima sem grab ainda = aguardar
    liq = r["liquidez"]
    if not liq["bsl_capturada"] and not liq["ssl_capturada"] and not recomendacoes:
        recomendacoes.append({
            "cenario": "AGUARDAR LIQUIDEZ",
            "direcao": "N/D",
            "ordem": "NENHUMA — aguardar",
            "zona_entrada": f"BSL: {liq['bsl']} | SSL: {liq['ssl']}",
            "vela_esperada": "Aguardar varredura de liquidez antes de qualquer entrada",
            "sl_sugerido": "N/D",
            "prioridade": "AGUARDAR"
        })

    if not recomendacoes:
        recomendacoes.append({
            "cenario": "SEM SETUP CLARO",
            "direcao": "N/D",
            "ordem": "NENHUMA",
            "zona_entrada": "N/D",
            "vela_esperada": "Aguardar nova marcacao",
            "sl_sugerido": "N/D",
            "prioridade": "AGUARDAR"
        })

    return recomendacoes
