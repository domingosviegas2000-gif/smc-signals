import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
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

    if ush > psh: eventos.append({"tipo":"Topo Mais Alto (HH)","preco":round(ush,p_round),"hora":hora_sh})
    else: eventos.append({"tipo":"Topo Mais Baixo (LH)","preco":round(ush,p_round),"hora":hora_sh})
    if usl > psl: eventos.append({"tipo":"Fundo Mais Alto (HL)","preco":round(usl,p_round),"hora":hora_sl})
    else: eventos.append({"tipo":"Fundo Mais Baixo (LL)","preco":round(usl,p_round),"hora":hora_sl})

    bos_bull = (p>ush) and (pp>ush)
    bos_bear = (p<usl) and (pp<usl)
    tendencia_anterior = "ALTA" if (ush>psh and usl>psl) else "BAIXA" if (ush<psh and usl<psl) else "NEUTRA"

    if bos_bull:
        if tendencia_anterior == "BAIXA":
            eventos.append({"tipo":"Mudanca de Carater Altista (CHoCH)","preco":round(p,p_round),"hora":df.index[-1]})
        else:
            eventos.append({"tipo":"Rompimento de Estrutura Altista (BOS)","preco":round(p,p_round),"hora":df.index[-1]})
    if bos_bear:
        if tendencia_anterior == "ALTA":
            eventos.append({"tipo":"Mudanca de Carater Baixista (CHoCH)","preco":round(p,p_round),"hora":df.index[-1]})
        else:
            eventos.append({"tipo":"Rompimento de Estrutura Baixista (BOS)","preco":round(p,p_round),"hora":df.index[-1]})

    return eventos

# ==================== LIQUIDEZ: BSL/SSL/EQH/EQL ====================
def detectar_liquidez_completa(df, par):
    p_round = precisao(par)
    h20 = df["High"].tail(30)
    l20 = df["Low"].tail(30)
    p = float(df["Close"].iloc[-1])

    bsl = float(h20.max())
    ssl = float(l20.min())

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

    h5 = df["High"].tail(5); l5 = df["Low"].tail(5)
    grab_topo = float(h5.max()) >= bsl*0.999 and p < bsl
    grab_fundo = float(l5.min()) <= ssl*1.001 and p > ssl

    bsl_varrida = p > bsl
    ssl_varrida = p < ssl

    return {
        "bsl": round(bsl,p_round), "bsl_varrida": bsl_varrida,
        "ssl": round(ssl,p_round), "ssl_varrida": ssl_varrida,
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
        if float(candle["Close"]) < float(candle["Open"]):
            if float(df["Close"].iloc[-1]) > float(candle["High"]):
                alto = round(float(candle["High"]),p_round)
                baixo = round(float(candle["Low"]),p_round)
                pos_candles = df.iloc[i+1:]
                testes = ((pos_candles["Low"]<=alto)&(pos_candles["High"]>=baixo)).sum()
                invalido = float(df["Close"].iloc[-1]) < baixo
                if not invalido:
                    forca = max(50, 95 - testes*8)
                    blocks.append({
                        "tipo":"Bloco de Ordem Altista","tf":tf_label,"alto":alto,"baixo":baixo,
                        "hora_criacao":hora_criacao,"testes":int(testes),
                        "forca":int(forca),"valido": forca>=70
                    })
                    break

    for i in range(len(df)-3, max(len(df)-30,0), -1):
        if not df["vf"].iloc[i]: continue
        candle = df.iloc[i]
        hora_criacao = df.index[i]
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
                        "tipo":"Bloco de Ordem Baixista","tf":tf_label,"alto":alto,"baixo":baixo,
                        "hora_criacao":hora_criacao,"testes":int(testes),
                        "forca":int(forca),"valido": forca>=70
                    })
                    break
    return blocks

# ==================== FAIR VALUE GAP — H1 + M15 + M5 ====================
def detectar_fvg(df, par, tf_label):
    p_round = precisao(par)
    p = float(df["Close"].iloc[-1])
    fvgs = []
    for i in range(len(df)-2, max(len(df)-25,1), -1):
        if i+1 >= len(df): continue
        if df["Low"].iloc[i+1] > df["High"].iloc[i-1]:
            topo = round(float(df["Low"].iloc[i+1]),p_round)
            base = round(float(df["High"].iloc[i-1]),p_round)
            preenchido_pct = 0
            if p < topo:
                if p <= base: preenchido_pct = 100
                else: preenchido_pct = round((topo-p)/(topo-base)*100,0)
            estado = "PREENCHIDO" if preenchido_pct>=100 else "PARCIAL" if preenchido_pct>0 else "ABERTO"
            if estado != "PREENCHIDO":
                fvgs.append({"tipo":"Gap de Valor Justo Altista","tf":tf_label,"topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
            break
    for i in range(len(df)-2, max(len(df)-25,1), -1):
        if i+1 >= len(df): continue
        if df["High"].iloc[i+1] < df["Low"].iloc[i-1]:
            topo = round(float(df["Low"].iloc[i-1]),p_round)
            base = round(float(df["High"].iloc[i+1]),p_round)
            preenchido_pct = 0
            if p > base:
                if p >= topo: preenchido_pct = 100
                else: preenchido_pct = round((p-base)/(topo-base)*100,0)
            estado = "PREENCHIDO" if preenchido_pct>=100 else "PARCIAL" if preenchido_pct>0 else "ABERTO"
            if estado != "PREENCHIDO":
                fvgs.append({"tipo":"Gap de Valor Justo Baixista","tf":tf_label,"topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
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

# ==================== TENDENCIA POR TIMEFRAME ====================
def tendencia_tf_smc(par, intervalo, periodo):
    try:
        d = obter_dados_smc(par,intervalo,periodo)
        if len(d)<20: return "NEUTRA"
        e20 = d["Close"].ewm(span=20).mean()
        p = float(d["Close"].iloc[-1])
        e = float(e20.iloc[-1])
        return "ALTA" if p>e else "BAIXA"
    except: return "NEUTRA"

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

# ==================== FASE DE MERCADO (sem vies direcional) ====================
def determinar_fase_mercado(estrutura, liquidez, ob_validos, fvg_abertos):
    tem_choch = any("CHoCH" in e["tipo"] or "Mudanca" in e["tipo"] for e in estrutura)
    tem_bos = any("BOS" in e["tipo"] or "Rompimento de Estrutura" in e["tipo"] for e in estrutura)
    tem_grab = liquidez["grab_topo"] or liquidez["grab_fundo"]

    if tem_grab and tem_choch:
        return "MANIPULACAO — Varredura de liquidez seguida de mudanca de estrutura"
    elif tem_grab:
        return "MANIPULACAO — Varredura de liquidez detectada, estrutura ainda nao reagiu"
    elif tem_bos:
        return "EXPANSAO — Continuacao de estrutura em curso"
    elif tem_choch:
        return "TRANSICAO — Possivel mudanca de tendencia em curso"
    elif ob_validos or fvg_abertos:
        return "CORRECAO — Preco em zona de blocos institucionais, sem rompimento recente"
    else:
        return "INDEFINIDA — Sem eventos estruturais relevantes no momento"

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

        # FVG alinhado em H1 + M15 + M5
        fvg_h1 = detectar_fvg(dh1, par, "H1") if len(dh1)>30 else []
        fvg_m15 = detectar_fvg(d15, par, "M15")
        fvg_m5 = detectar_fvg(dm5, par, "M5") if len(dm5)>30 else []

        niveis = niveis_institucionais(par)

        tend_h1 = tendencia_tf_smc(par,"1h","10d")
        tend_m15 = tendencia_tf_smc(par,"15m","5d")
        tend_m5 = tendencia_tf_smc(par,"5m","2d")

        for ob in ob_m15+ob_h1:
            ob["idade"] = formatar_idade(ob["hora_criacao"])
        for f in fvg_h1+fvg_m15+fvg_m5:
            f["idade"] = formatar_idade(f["hora"])

        ob_validos = [o for o in (ob_m15+ob_h1) if o["valido"]]
        fvg_todos = fvg_h1+fvg_m15+fvg_m5
        fvg_abertos = [f for f in fvg_todos if f["estado"]=="ABERTO"]
        liq_nao_varrida = sum([not liquidez["bsl_varrida"], not liquidez["ssl_varrida"], liquidez["eqh"] is not None, liquidez["eql"] is not None])

        fase_mercado = determinar_fase_mercado(estrutura_m15, liquidez, ob_validos, fvg_abertos)

        gc.collect()
        return {
            "par": nome,
            "estrutura_m15": estrutura_m15,
            "estrutura_m5": estrutura_m5,
            "liquidez": liquidez,
            "ob_m15": ob_m15,
            "ob_h1": ob_h1,
            "fvg_h1": fvg_h1,
            "fvg_m15": fvg_m15,
            "fvg_m5": fvg_m5,
            "niveis": niveis,
            "tend_h1": tend_h1,
            "tend_m15": tend_m15,
            "tend_m5": tend_m5,
            "fase_mercado": fase_mercado,
            "ob_validos": len(ob_validos),
            "fvg_abertos": len(fvg_abertos),
            "liq_nao_varrida": liq_nao_varrida
        }
    except:
        gc.collect()
        return None
